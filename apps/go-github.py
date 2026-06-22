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
from typing import Literal

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


def _ensure_lf_preserved(repo: Path) -> None:
    """core.autocrlf=false + .gitattributes: LF is never rewritten to CRLF. Idempotent."""
    subprocess.run(
        ["git", "-C", str(repo), "config", "--local", "core.autocrlf", "false"],
        capture_output=True, check=True,
    )
    attrs = repo / ".gitattributes"
    if not attrs.exists():
        attrs.write_text("* text eol=lf\n", encoding="utf-8")
        print("[+] Created .gitattributes (force LF for all text files)")


def _git_env_for(repo: Path) -> dict | None:
    """Try to extract author identity from existing commits in the repo.
    Returns a dict with GIT_AUTHOR_NAME/EMAIL if found."""
    r = subprocess.run(
        ["git", "-C", str(repo), "log", "-1", "--format=%ae%n%an"],
        capture_output=True, text=True
    )
    if r.returncode != 0 or not r.stdout.strip():
        return None
    parts = r.stdout.strip().split("\n", 1)
    if len(parts) < 2:
        return None
    email, name = parts
    return {"GIT_AUTHOR_EMAIL": email, "GIT_AUTHOR_NAME": name, "GIT_COMMITTER_EMAIL": email, "GIT_COMMITTER_NAME": name}


def _prompt_git_identity():
    """Ask user for git identity, save globally, return (name, email)."""
    print()
    print("    git needs your identity for commits.")
    while True:
        name = input("    Git user.name: ").strip()
        if name:
            break
        print("    Required.")
    while True:
        email = input("    Git user.email (GitHub login email): ").strip()
        if email:
            break
        print("    Required.")

    subprocess.run(["git", "config", "--global", "user.name", name], check=True)
    subprocess.run(["git", "config", "--global", "user.email", email], check=True)
    print(f"    Saved to ~/.gitconfig.")
    return name, email


def _has_global_git_identity() -> bool:
    """True if both user.name and user.email are set globally."""
    name = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True).stdout.strip()
    return bool(name and email)


def cmd_push(repo: Path, msg: str):
    """Git add + commit + push."""
    _ensure_lf_preserved(repo)
    if not _has_global_git_identity():
        _prompt_git_identity()
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True
    ).stdout.strip()

    if not result:
        print("[+] Nothing to commit.")
        return

    print(f"[+] Committing in {repo.name}...")
    subprocess.run(["git", "-C", str(repo), "add", "."], check=True, capture_output=True)
    commit = subprocess.run(
        ["git", "-C", str(repo), "commit", "-m", msg],
        capture_output=True, text=True
    )
    if commit.returncode != 0:
        stderr = commit.stderr.strip()
        if "Author identity unknown" in stderr or "please tell me who you are" in stderr.lower():
            env_vars = _git_env_for(repo)
            if env_vars:
                print("[i] No global git identity. Detected from repo history — using it once.")
                commit = subprocess.run(
                    ["git", "-C", str(repo), "commit", "-m", msg],
                    capture_output=True, text=True,
                    env={**os.environ, **env_vars}
                )
                if commit.returncode != 0:
                    _prompt_git_identity()
                    subprocess.run(
                        ["git", "-C", str(repo), "commit", "-m", msg],
                        check=True, capture_output=True
                    )
            else:
                print("[!] No global git identity and no repo history.")
                _prompt_git_identity()
                subprocess.run(
                    ["git", "-C", str(repo), "commit", "-m", msg],
                    check=True, capture_output=True
                )
        else:
            print(f"[!] Commit failed:\n{stderr}")
            return

    print(f"[+] Pushing to remote...")
    push = subprocess.run(
        ["git", "-C", str(repo), "push"],
        capture_output=True, text=True
    )
    if push.returncode != 0:
        print(f"[!] Push failed (exit {push.returncode}):\n{push.stderr.strip()}")
        return

    web_url = _get_remote_url(repo)
    print(f"[+] Done." + (f"  {web_url}" if web_url else ""))


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
    # On Windows / network drives, git refuses -C into paths it considers
    # "dubious ownership". Whitelist the directory once, globally.
    here_str = str(here.resolve()).replace("\\", "/")
    subprocess.run(
        ["git", "config", "--global", "--add", "safe.directory", here_str],
        capture_output=True,
    )

    print(f"[+] Git init in {here}")

    is_new_init = not (here / ".git").exists()
    if is_new_init:
        subprocess.run(["git", "init"], cwd=here, check=True, capture_output=True)
    _ensure_lf_preserved(here)

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
        subprocess.run(
            ["git", "-C", str(here), "add", "."],
            check=True, capture_output=True,
        )
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

    push = subprocess.run(
        ["git", "-C", str(here), "push", "-u", "origin", "HEAD"],
        capture_output=True, text=True,
    )
    if push.returncode == 0:
        print("[+] Pushed to origin.")
        print(f"  -> https://github.com/{username}/{repo_name}")
    else:
        print(f"[!] Push failed: {push.stderr.strip() or push.stdout.strip()}")


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


def _repo_info(repo: Path) -> str:
    """Return a one-line summary of the repo's remote identity."""
    branch = subprocess.run(
        ["git", "-C", str(repo), "branch", "--show-current"],
        capture_output=True, text=True
    ).stdout.strip() or "(detached)"

    remote_url = _get_remote_url(repo)
    if remote_url:
        return f"  -> {remote_url}  [{branch}]"
    return f"  -> (no remote)  [{branch}]"


def _find_all_repos() -> list[tuple[Path, str, str]]:
    """Scan ~/devtool and its siblings for git repos. Returns (path, name, remote_url)."""
    SKIP_NAMES = {"devtool", ".cursor", ".config", ".local", "AppData", "Downloads"}
    candidates = []
    try:
        for sibling in Path.home().iterdir():
            if sibling.name in SKIP_NAMES:
                continue
            if sibling.is_dir():
                candidates.append(sibling)
    except OSError:
        pass

    found = []
    seen_urls = set()
    for base in candidates:
        try:
            for root, dirs, files in os.walk(base):
                if ".git" in dirs:
                    rp = Path(root)
                    url = _get_remote_url(rp) or "(no remote)"
                    name = rp.name
                    if url not in seen_urls:
                        found.append((rp, name, url))
                        seen_urls.add(url)
                    dirs.clear()
                dirs[:] = [d for d in dirs if not d.startswith(".")]
        except OSError:
            pass
    return found


def _pick_repo() -> Path | Literal["__init__"] | None:
    """Interactive repo picker. Returns chosen Path, '__init__', or None."""
    repos = _find_all_repos()
    print()
    print("[ Pick a repo ]")
    print("-" * 50)
    for i, (rp, name, url) in enumerate(repos, 1):
        print(f"  {i}. {name}")
        print(f"     {url}")
    print(f"  I. Init here")
    print(f"     Create a new remote repo + git init + push local files")
    print(f"  0. Exit")
    print()

    while True:
        val = input("Select: ").strip().lower()
        if val == "0":
            sys.exit(0)
        if val == "i":
            return "__init__"
        try:
            n = int(val)
            if 1 <= n <= len(repos):
                return repos[n - 1][0]
        except ValueError:
            pass
        print("Invalid, try again.")


def _confirm_repo(repo: Path) -> bool | Path | None:
    """Ask user to confirm this is the right repo.
    Returns True (confirmed), False (user wants to switch), None (abort)."""
    info = _repo_info(repo)
    print()
    print(f"[ {repo.name} ]  {info}")
    print("-" * 50)
    while True:
        val = input("Is this the right repo? [Y/n/s(switch)/?]: ").strip().lower()
        if val in ("", "y", "yes"):
            return True
        if val in ("n", "no"):
            return None
        if val == "s":
            return False  # signal to switch
        if val == "?":
            cmd_status(repo)
            print("-" * 50)
            print(f"[ {repo.name} ]  {info}")
            print("-" * 50)
            continue
        print("Invalid. Type Y, n, s, or ?.")


def interactive():
    """Confirm repo -> action menu."""
    repo = find_repo_root(Path.cwd())

    def _resolve_and_confirm(r: Path | None) -> Path | None:
        if r is None:
            return None
        result = _confirm_repo(r)
        if result is True:
            return r
        if result is None:
            return None
        # result is False — user wants to switch
        picked = _pick_repo()
        if picked is None:
            return None
        if picked == "__init__":
            return picked
        result2 = _confirm_repo(picked)
        if result2 is True:
            return picked
        return None  # keep rejecting

    if repo:
        repo = _resolve_and_confirm(repo)
        if repo is None:
            print("Aborted.")
            return
    else:
        print("[i] Not in a git repo -- picking one for you...")
        repo = _pick_repo()
        if repo is None:
            print("[!] No git repos found.")
            return
        # If user picked init directly, skip confirm and go straight to action
        if repo == "__init__":
            # handle inline below after menu
            pass
        else:
            repo = _resolve_and_confirm(repo)
            if repo is None:
                print("Aborted.")
                return

    # Handle direct init picks from non-repo directories
    if repo == "__init__":
        name = input("Repo name: ").strip()
        if not name:
            print("[!] Repo name required.")
            return
        priv = input("Private? [Y/n]: ").strip().lower() != "n"
        cmd_init(name, priv)
        return

    info = _repo_info(repo)
    header = f"[ {repo.name} ]  {info}\n[ What to do? ]"
    choices = [
        ("Push (commit + push)", "push", "Stage all + commit with a message + push to remote"),
        ("Pull", "pull", "Fetch and merge latest changes from remote"),
        ("Status", "status", "Show uncommitted changes and remote state"),
        ("Switch repo", "switch", "Pick a different git repo to work with"),
    ]
    choice = show_menu(choices, header)
    action = ["push", "pull", "status", "switch"][choice - 1]

    if action == "switch":
        repo = _pick_repo()
        if repo is None:
            print("[!] No git repos found.")
            return
        repo = _resolve_and_confirm(repo)
        if repo is None:
            print("Aborted.")
            return
        info = _repo_info(repo)
        header = f"[ {repo.name} ]  {info}\n[ What to do? ]"
        choices = [
            ("Push (commit + push)", "push", "Stage all + commit with a message + push to remote"),
            ("Pull", "pull", "Fetch and merge latest changes from remote"),
            ("Status", "status", "Show uncommitted changes and remote state"),
            ("Switch repo", "switch", "Pick a different git repo to work with"),
        ]
        choice = show_menu(choices, header)
        action = ["push", "pull", "status", "switch"][choice - 1]

    if action == "status":
        cmd_status(repo)
        return

    if action in ("push", "pull"):
        msg = input("Commit message: ").strip() or ("update" if action == "push" else "")
        if action == "push":
            cmd_push(repo, msg)
        else:
            cmd_pull(repo, msg)


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
