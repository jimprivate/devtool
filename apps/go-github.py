#!/usr/bin/env python3
"""Unified GitHub CLI — push, pull, new repo, init repo."""

import argparse
import json
import os
import platform
import shutil
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

GITHUB_API = "https://api.github.com"


def _config_dir():
    if platform.system() == "Windows":
        base = Path(os.environ.get("APPDATA", Path.home() / "AppData" / "Roaming"))
    else:
        base = Path.home() / ".config"
    return base / "go-github"


def _token_file():
    return _config_dir() / "token"


def get_token():
    """Read token from disk, prompt and cache if missing."""
    tf = _token_file()
    if tf.exists():
        token = tf.read_text().strip()
        if token:
            return token

    token = os.environ.get("GITHUB_TOKEN", "").strip()
    if token:
        return token

    token = input("GitHub token: ").strip()
    if not token:
        print("[!] Token required. Get one at: https://github.com/settings/tokens (scope: repo)")
        sys.exit(1)

    tf.parent.mkdir(parents=True, exist_ok=True)
    tf.write_text(token)
    tf.chmod(0o600)
    print(f"[+] Token saved to {tf}")
    os.environ["GITHUB_TOKEN"] = token
    return token


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


def http_get(url, token, params=None):
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise HttpError(e.code, body_text) from e


class HttpError(Exception):
    def __init__(self, status: int, body: str):
        self.status = status
        self.body = body
        super().__init__(f"HTTP {status}: {body}")


def http_post(url, token, data):
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return r.read()
    except urllib.error.HTTPError as e:
        body_text = e.read().decode(errors="replace")
        raise HttpError(e.code, body_text) from e


def find_repo_root(start: Path) -> Path | None:
    p = start.resolve()
    while p != p.parent:
        if (p / ".git").exists():
            return p
        p = p.parent
    return None


# ── Commands ────────────────────────────────────────────────

def cmd_status(repo: Path):
    """Show git status of current repo."""
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True
    )
    lines = result.stdout.strip()
    if not lines:
        print("[+] Nothing to commit.")
    else:
        print(f"[+] Changes in {repo.name}:")
        for line in lines.splitlines():
            print(f"  {line}")

    branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True, text=True
    ).stdout.strip()
    remote = subprocess.run(
        ["git", "-C", str(repo), "config", "--get", "remote.origin.url"],
        capture_output=True, text=True
    ).stdout.strip()
    print(f"    Branch: {branch or '(detached)'}")
    print(f"    Remote: {remote or '(none)'}")


def cmd_pull(repo: Path, msg: str):
    """Git pull."""
    result = subprocess.run(["git", "-C", str(repo), "pull"])
    if result.returncode != 0:
        print("[!] Pull failed.")


def _get_remote_url(repo: Path) -> str | None:
    """Get origin remote URL if set."""
    r = subprocess.run(
        ["git", "-C", str(repo), "remote", "get-url", "origin"],
        capture_output=True, text=True
    )
    if r.returncode == 0:
        url = r.stdout.strip()
        # Convert git@github.com:user/repo.git to https://github.com/user/repo
        if url.startswith("git@github.com:"):
            return "https://github.com/" + url.split(":", 1)[1].removesuffix(".git")
        if url.startswith("https://") or url.startswith("http://"):
            return url.removesuffix(".git")
    return None


def cmd_push(repo: Path, msg: str):
    """Git add + commit + push."""
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True
    ).stdout.strip()

    if not result:
        print("[+] Nothing to commit.")
        return

    print(f"[+] Committing in {repo.name}...")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True)
    subprocess.run(["git", "-C", str(repo), "commit", "-m", msg], check=True, capture_output=True)
    push = subprocess.run(["git", "-C", str(repo), "push"])
    if push.returncode == 0:
        web_url = _get_remote_url(repo)
        print(f"[+] Pushed.")
        if web_url:
            print(f"  -> {web_url}")
    else:
        print(f"[!] Push failed.")


def cmd_new(repo_name: str, private: bool = True, push_local: bool = False):
    """Create a GitHub repo. Optionally push local files from current dir."""
    token = get_token()
    user_url = f"{GITHUB_API}/user"
    user_data = http_get(user_url, token)
    username = json.loads(user_data)["login"]

    create_url = f"{GITHUB_API}/user/repos"
    data = {
        "name": repo_name,
        "private": private,
        "auto_init": False,
        "description": "",
    }
    try:
        http_post(create_url, token, data)
        print(f"[+] Created: https://github.com/{username}/{repo_name} ({'private' if private else 'public'})")
    except HttpError as e:
        if "already exists" in e.body:
            print(f"[!] Repo '{username}/{repo_name}' already exists on GitHub.")
            if not push_local:
                print("Aborted.")
                return
            confirm = input("  Push to it anyway? [y/N]: ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return
            print("[i] Continuing with existing repo.")
        elif e.status == 422:
            try:
                msg = json.loads(e.body).get("errors", [{}])[0].get("message", e.body)
            except Exception:
                msg = e.body
            print(f"[!] 422 Unprocessable Entity: {msg}")
            return
        else:
            print(f"[!] API error {e.status}: {e.body}")
            return

    if push_local:
        _push_local(Path.cwd(), username, repo_name)


def cmd_init(repo_name: str, private: bool = True, push_local: bool = True):
    """Create GitHub repo, git init, first commit + push."""
    token = get_token()
    user_url = f"{GITHUB_API}/user"
    user_data = http_get(user_url, token)
    username = json.loads(user_data)["login"]

    # Create GitHub repo
    create_url = f"{GITHUB_API}/user/repos"
    try:
        http_post(create_url, token, {
            "name": repo_name,
            "private": private,
            "auto_init": False,
            "description": "",
        })
        print(f"[+] Created GitHub repo: https://github.com/{username}/{repo_name}")
    except HttpError as e:
        if "already exists" in e.body:
            print(f"[!] Repo '{username}/{repo_name}' already exists on GitHub.")
            confirm = input("  Use existing repo and push local files? [y/N]: ").strip().lower()
            if confirm != "y":
                print("Aborted.")
                return
            print("[i] Continuing with existing repo...")
        elif e.status == 422:
            try:
                msg = json.loads(e.body).get("errors", [{}])[0].get("message", e.body)
            except Exception:
                msg = e.body
            print(f"[!] 422 Unprocessable Entity: {msg}")
            return
        else:
            print(f"[!] API error {e.status}: {e.body}")
            return

    _push_local(Path.cwd(), username, repo_name)


def _get_git_identity():
    """Prompt for git identity if not set, save globally."""
    git = shutil.which("git") or "git"

    name_result = subprocess.run([git, "config", "--global", "user.name"], capture_output=True, text=True)
    email_result = subprocess.run([git, "config", "--global", "user.email"], capture_output=True, text=True)

    if name_result.stdout.strip() and email_result.stdout.strip():
        return

    print()
    print("[!] Git identity not configured.")
    name = input("  git user.name: ").strip()
    email = input("  git user.email: ").strip()
    if not name or not email:
        print("[!] Both name and email are required to commit.")
        return

    subprocess.run([git, "config", "--global", "user.name", name], check=True)
    subprocess.run([git, "config", "--global", "user.email", email], check=True)
    print(f"[+] Git identity saved: {name} <{email}>")


def _push_local(here: Path, username: str, repo_name: str):
    """Git init + remote + add + commit + push from an existing directory."""
    print(f"[+] Git init in {here}")

    is_new_init = not (here / ".git").exists()
    if is_new_init:
        subprocess.run(["git", "init"], cwd=here, check=True, capture_output=True)

    remote_url = f"https://github.com/{username}/{repo_name}.git"
    check_remote = subprocess.run(
        ["git", "-C", str(here), "remote", "get-url", "origin"],
        capture_output=True, text=True
    )
    if check_remote.returncode == 0:
        if check_remote.stdout.strip() != remote_url:
            subprocess.run(
                ["git", "-C", str(here), "remote", "set-url", "origin", remote_url],
                check=True, capture_output=True
            )
            print(f"[i] Updated origin URL to {remote_url}")
        else:
            print("[i] Remote origin already set.")
    else:
        subprocess.run(
            ["git", "-C", str(here), "remote", "add", "origin", remote_url],
            cwd=here, check=True, capture_output=True
        )

    result = subprocess.run(
        ["git", "-C", str(here), "status", "--porcelain"],
        capture_output=True, text=True
    ).stdout.strip()
    if result:
        subprocess.run(["git", "-C", str(here), "add", "."], check=True)
        commit_result = subprocess.run(
            ["git", "-C", str(here), "commit", "-m", "Initial commit"],
            capture_output=True, text=True
        )
        if commit_result.returncode == 0:
            print("[+] First commit created.")
        elif "Author identity unknown" in commit_result.stderr:
            _get_git_identity()
            commit_result = subprocess.run(
                ["git", "-C", str(here), "commit", "-m", "Initial commit"],
                capture_output=True, text=True
            )
            if commit_result.returncode == 0:
                print("[+] First commit created.")
            else:
                print(f"[!] Commit failed:\n{commit_result.stderr}")
                return
        else:
            print(f"[!] Commit failed:\n{commit_result.stderr}")
            return
    else:
        print("[i] No files to commit.")

    push = subprocess.run(["git", "-C", str(here), "push", "-u", "origin", "HEAD"])
    if push.returncode == 0:
        print("[+] Pushed to origin.")
        print(f"  -> https://github.com/{username}/{repo_name}")
    else:
        print("[!] Push failed.")


# ── Interactive Menu ─────────────────────────────────────────

def show_menu(choices: list[tuple[str, str, str]], header: str) -> int:
    print()
    print(header)
    print("-" * 50)
    for i, (label, _, desc) in enumerate(choices, 1):
        print(f"  {i}. {label}")
        print(f"     {desc}")
    print(f"  0. Exit")
    print()
    while True:
        try:
            val = input("Select: ").strip()
            if val == "0":
                sys.exit(0)
            n = int(val)
            if 1 <= n <= len(choices):
                return n
        except ValueError:
            pass
        print("Invalid, try again.")


def interactive():
    """Show context-aware menu based on current directory."""
    repo = find_repo_root(Path.cwd())

    if repo:
        choice = show_menu([
            ("Push (commit + push)", "push", "Stage all + commit with a message + push to remote"),
            ("Pull", "pull", "Fetch and merge latest changes from remote"),
            ("Status", "status", "Show uncommitted changes and remote state"),
            ("New GitHub repo", "new", "Create a remote repo on GitHub (does not push local files)"),
            ("New here", "init", "Create a remote repo + init + push all local files in one step"),
        ], f"[{repo.name}] What to do?")
        action = ["push", "pull", "status", "new", "init"][choice - 1]
    else:
        choice = show_menu([
            ("New GitHub repo", "new", "Create a remote repo on GitHub (does not push local files)"),
            ("New here", "init", "Create a remote repo + init + push all local files in one step"),
        ], "[Not a git repo] What to do?")
        action = ["new", "init"][choice - 1]

    if action in ("push", "pull", "status"):
        if action == "push":
            msg = input("Commit message: ").strip() or "update"
            cmd_push(repo, msg)
        elif action == "pull":
            cmd_pull(repo, "")
        else:
            cmd_status(repo)
    else:
        name = input("Repo name: ").strip()
        if not name:
            print("[!] Repo name required.")
            return
        priv = input("Private? [Y/n]: ").strip().lower() != "n"
        if action == "new":
            push_local = input("Push local files? [y/N]: ").strip().lower() == "y"
            cmd_new(name, priv, push_local)
        else:
            cmd_init(name, priv)


# ── Main ─────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="GitHub CLI: push, pull, new, init.")
    parser.add_argument("command", nargs="?", help="push, pull, new, init, status")
    parser.add_argument("arg", nargs="?", help="repo name or commit message")
    parser.add_argument("-p", "--private", action="store_true", default=True,
                        help="make repo private (default: True)")
    parser.add_argument("--public", dest="private", action="store_false",
                        help="make repo public")
    parser.add_argument("--push-local", dest="push_local", action="store_true", default=False,
                        help="new: also git init + push local files to the new repo")
    parser.add_argument("--no-push", dest="push_local", action="store_false",
                        help="init: skip git init + push (create remote repo only)")
    args = parser.parse_args()

    if not args.command:
        interactive()
        return

    cmd = args.command.lower()

    if cmd == "status":
        repo = find_repo_root(Path.cwd())
        if not repo:
            print("[!] Not in a git repository.")
            sys.exit(1)
        cmd_status(repo)

    elif cmd == "push":
        repo = find_repo_root(Path.cwd())
        if not repo:
            print("[!] Not in a git repository.")
            sys.exit(1)
        msg = args.arg or "update"
        cmd_push(repo, msg)

    elif cmd == "pull":
        repo = find_repo_root(Path.cwd())
        if not repo:
            print("[!] Not in a git repository.")
            sys.exit(1)
        cmd_pull(repo, "")

    elif cmd == "new":
        if not args.arg:
            print("[!] Usage: gh new <repo-name> [--push-local]")
            sys.exit(1)
        cmd_new(args.arg, args.private, args.push_local)

    elif cmd == "init":
        if not args.arg:
            print("[!] Usage: gh init <repo-name> [--no-push]")
            sys.exit(1)
        cmd_init(args.arg, args.private, args.push_local)

    else:
        print(f"[!] Unknown command: {cmd}")
        print("    Available: push, pull, new, init, status")
        print("    Or run 'gh' for interactive menu.")


if __name__ == "__main__":
    main()
