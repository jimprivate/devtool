#!/usr/bin/env python3
"""Unified GitHub CLI — push, pull, new repo, init repo."""

import argparse
import os
import subprocess
import sys
from pathlib import Path

GITHUB_API = "https://api.github.com"


def get_token():
    token = os.environ.get("GITHUB_TOKEN")
    if not token:
        print("[!] GITHUB_TOKEN not set.")
        print("    Set it via:  export GITHUB_TOKEN=your_token_here")
        sys.exit(1)
    return token


def http_get(url, token, params=None):
    import urllib.request, urllib.parse
    if params:
        url += "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


def http_post(url, token, data):
    import urllib.request, json
    body = json.dumps(data).encode()
    req = urllib.request.Request(url, data=body, method="POST", headers={
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "Content-Type": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


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
    result = subprocess.run(
        ["git", "-C", str(repo), "pull"],
        capture_output=True, text=True
    )
    if result.returncode == 0:
        print(result.stdout or "[+] Pulled.")
    else:
        print(f"[!] Pull failed: {result.stderr}")


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
    push = subprocess.run(
        ["git", "-C", str(repo), "push"],
        capture_output=True, text=True
    )
    print(push.stdout or "[+] Pushed.")
    if push.returncode != 0:
        print(f"[!] Push failed: {push.stderr}")


def cmd_new(repo_name: str, private: bool = True):
    """Create a GitHub repo without cloning."""
    token = get_token()
    user_url = f"{GITHUB_API}/user"
    user_data = http_get(user_url, token)
    import json
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
    except Exception as e:
        if "already exists" in str(e):
            print(f"[!] Repo '{username}/{repo_name}' already exists.")
        else:
            print(f"[!] Failed to create repo: {e}")


def cmd_init(repo_name: str, private: bool = True):
    """Create GitHub repo, git init, first commit + push."""
    token = get_token()
    user_url = f"{GITHUB_API}/user"
    user_data = http_get(user_url, token)
    import json
    username = json.loads(user_data)["login"]

    # Create GitHub repo
    create_url = f"{GITHUB_API}/user/repos"
    try:
        http_post(create_url, token, {
            "name": repo_name,
            "private": private,
            "auto_init": True,
            "description": "",
        })
        print(f"[+] Created GitHub repo: https://github.com/{username}/{repo_name}")
    except Exception as e:
        if "already exists" not in str(e):
            print(f"[!] Failed to create repo: {e}")
            return
        print(f"[i] Repo already exists, using existing one.")

    # git init in current directory
    here = Path.cwd()
    print(f"[+] Git init in {here}")
    subprocess.run(["git", "init"], cwd=here, check=True, capture_output=True)
    subprocess.run(
        ["git", "remote", "add", "origin",
         f"https://github.com/{username}/{repo_name}.git"],
        cwd=here, check=True, capture_output=True
    )

    # First commit if needed
    result = subprocess.run(
        ["git", "-C", str(here), "status", "--porcelain"],
        capture_output=True, text=True
    ).stdout.strip()
    if result:
        subprocess.run(["git", "-C", str(here), "add", "."], check=True)
        subprocess.run(
            ["git", "-C", str(here), "commit", "-m", "Initial commit"],
            check=True, capture_output=True
        )
        print("[+] First commit created.")
    else:
        print("[i] No files to commit.")

    # Push
    push = subprocess.run(
        ["git", "-C", str(here), "push", "-u", "origin", "HEAD"],
        capture_output=True, text=True
    )
    print(push.stdout or "[+] Pushed to origin.")
    if push.returncode != 0:
        print(f"[!] Push failed: {push.stderr}")


# ── Interactive Menu ─────────────────────────────────────────

def show_menu(choices: list[tuple[str, str]], header: str) -> int:
    print()
    print(header)
    print("-" * 40)
    for i, (label, _) in enumerate(choices, 1):
        print(f"  {i}. {label}")
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
            ("Push (commit + push)", "push"),
            ("Pull", "pull"),
            ("Status", "status"),
            ("New GitHub repo (no clone)", "new"),
            ("Init here (create repo + push)", "init"),
        ], f"[{repo.name}] What to do?")
        action = ["push", "pull", "status", "new", "init"][choice - 1]
    else:
        choice = show_menu([
            ("New GitHub repo (no clone)", "new"),
            ("Init here (create repo + push)", "init"),
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
            cmd_new(name, priv)
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
            print("[!] Usage: gh new <repo-name>")
            sys.exit(1)
        cmd_new(args.arg, args.private)

    elif cmd == "init":
        if not args.arg:
            print("[!] Usage: gh init <repo-name>")
            sys.exit(1)
        cmd_init(args.arg, args.private)

    else:
        print(f"[!] Unknown command: {cmd}")
        print("    Available: push, pull, new, init, status")
        print("    Or run 'gh' for interactive menu.")


if __name__ == "__main__":
    main()
