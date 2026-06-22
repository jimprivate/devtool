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


def _prompt_git_identity() -> tuple[str, str] | None:
    """Ensure global git user.name and user.email are set. Prompt if missing.

    Self-heal: instead of crashing with `Author identity unknown`, ask
    the user and save to ~/.gitconfig in one step. Returns (name, email)
    on success, or None if the user aborted or stdin is not a TTY.
    """
    git = shutil.which("git") or "git"

    name_r = subprocess.run([git, "config", "--global", "user.name"],
                            capture_output=True, text=True)
    email_r = subprocess.run([git, "config", "--global", "user.email"],
                             capture_output=True, text=True)
    cur_name = name_r.stdout.strip()
    cur_email = email_r.stdout.strip()

    if cur_name and cur_email:
        info(f"    git identity: {cur_name} <{cur_email}>")
        return cur_name, cur_email

    if not sys.stdin.isatty():
        err("Git identity not configured and stdin is not a TTY — cannot prompt.")
        err(f"Set it with:")
        err(f"  git config --global user.name  \"Your Name\"")
        err(f"  git config --global user.email \"you@example.com\"")
        return None

    print()
    warn("    git needs your identity for commits.")
    while True:
        try:
            name = input(f"    Git user.name [{cur_name or 'required'}]: ").strip() or cur_name
        except (KeyboardInterrupt, EOFError):
            print("\n    Aborted.")
            return None
        if name:
            break
        print("    Required.")
    while True:
        try:
            email = input(f"    Git user.email (GitHub login email) [{cur_email or 'required'}]: ").strip() or cur_email
        except (KeyboardInterrupt, EOFError):
            print("\n    Aborted.")
            return None
        if email:
            break
        print("    Required.")

    subprocess.run([git, "config", "--global", "user.name", name], check=True)
    subprocess.run([git, "config", "--global", "user.email", email], check=True)
    ok(f"    Saved to ~/.gitconfig: {name} <{email}>")
    return name, email


def _has_global_git_identity() -> bool:
    """True if both user.name and user.email are set globally."""
    name = subprocess.run(["git", "config", "--global", "user.name"], capture_output=True, text=True).stdout.strip()
    email = subprocess.run(["git", "config", "--global", "user.email"], capture_output=True, text=True).stdout.strip()
    return bool(name and email)


def _run_streaming(cmd: list[str], cwd: Path | None = None,
                   heartbeat_after: float = 5.0,
                   heartbeat_interval: float = 10.0) -> tuple[int, str]:
    """Run a subprocess, streaming output to terminal in real-time while capturing it.
    Uses char-level reads so \\r progress bars and partial lines update immediately.
    If subprocess produces no output for `heartbeat_after` seconds, a heartbeat line
    is printed every `heartbeat_interval` seconds so the user knows it is alive.
    Returns (returncode, combined_stdout_and_stderr)."""
    import threading, sys, time
    proc = subprocess.Popen(
        cmd, cwd=str(cwd) if cwd else None,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, bufsize=0,
    )
    captured: list[str] = []
    last_output_at = [time.monotonic()]
    stop_heartbeat = [False]
    try:
        sys.stdout.reconfigure(errors="replace")
    except Exception:
        pass
    def _consume():
        while True:
            ch = proc.stdout.read(1)
            if not ch:
                break
            sys.stdout.write(ch)
            sys.stdout.flush()
            captured.append(ch)
            last_output_at[0] = time.monotonic()
        proc.stdout.close()
    def _heartbeat():
        while not stop_heartbeat[0]:
            time.sleep(heartbeat_interval)
            if stop_heartbeat[0]:
                break
            if proc.poll() is not None:
                break
            idle = time.monotonic() - last_output_at[0]
            if idle >= heartbeat_after:
                sys.stdout.write(f"\n[...] still working... ({int(idle)}s idle)\n")
                sys.stdout.flush()
    t = threading.Thread(target=_consume, daemon=True)
    h = threading.Thread(target=_heartbeat, daemon=True)
    t.start()
    h.start()
    proc.wait()
    stop_heartbeat[0] = True
    t.join(timeout=1)
    h.join(timeout=1)
    return proc.returncode, "".join(captured)


def _list_large_files_in_history(repo: Path, threshold_mb: int = 100) -> list[tuple[str, float]]:
    """Scan all reachable commits for blobs exceeding threshold.
    Uses `git rev-list --objects --all` + `git cat-file --batch-check` to be fast.
    Returns [(path, size_mb), ...] deduped by path, largest first."""
    threshold = threshold_mb * 1024 * 1024
    # Get every (oid, path) in history
    r = subprocess.run(
        ["git", "-C", str(repo), "rev-list", "--objects", "--all"],
        capture_output=True, text=True, errors="replace",
    )
    if r.returncode != 0 or not r.stdout.strip():
        return []
    # Build {oid: [paths...]}
    oid_paths: dict[str, list[str]] = {}
    for line in r.stdout.splitlines():
        parts = line.split(" ", 1)
        if len(parts) == 2:
            oid, path = parts
            oid_paths.setdefault(oid, []).append(path)
    if not oid_paths:
        return []
    # Batch-check sizes
    input_data = "\n".join(oid_paths.keys()).encode("utf-8")
    r2 = subprocess.run(
        ["git", "-C", str(repo), "cat-file", "--batch-check=%(objectname) %(objecttype) %(objectsize)"],
        input=input_data, capture_output=True,
    )
    big_by_path: dict[str, int] = {}
    for line in r2.stdout.decode("utf-8", errors="ignore").splitlines():
        parts = line.split(" ", 2)
        if len(parts) < 3:
            continue
        oid, otype, size_s = parts
        if otype != "blob":
            continue
        try:
            size = int(size_s)
        except ValueError:
            continue
        if size > threshold:
            for path in oid_paths.get(oid, []):
                if size > big_by_path.get(path, 0):
                    big_by_path[path] = size
    big = [(p, s / 1024 / 1024) for p, s in big_by_path.items()]
    big.sort(key=lambda x: -x[1])
    return big


def _lfs_tracked_patterns(repo: Path) -> list[str]:
    """Read .gitattributes and return patterns marked as LFS (filter=lfs)."""
    ga = repo / ".gitattributes"
    if not ga.exists():
        return []
    patterns = []
    for line in ga.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        if "filter=lfs" in line:
            # First whitespace-separated token is the pattern
            pat = line.split()[0]
            patterns.append(pat)
    return patterns


def _list_large_files(repo: Path, threshold_mb: int = 100) -> list[tuple[str, float]]:
    """Return [(path, size_mb), ...] for files that would be pushed and exceed threshold.
    Looks at working-tree files that are tracked OR staged."""
    threshold = threshold_mb * 1024 * 1024
    big = []
    # `git ls-files` lists tracked files; `-c` excludes cached, `-m` modified, etc.
    # We want everything that would actually go in the next commit, including new untracked ones.
    # Cheapest approach: walk the working tree and check size + gitignore.
    import re
    gi_path = repo / ".gitignore"
    ignore_patterns: list[str] = []
    if gi_path.exists():
        for line in gi_path.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if line and not line.startswith("#"):
                ignore_patterns.append(line)
    def _is_ignored(rel: str) -> bool:
        for pat in ignore_patterns:
            # very basic — use fnmatch
            import fnmatch
            if fnmatch.fnmatch(rel, pat) or fnmatch.fnmatch("/" + rel, "/" + pat):
                return True
        return False
    import fnmatch
    # Files matched by LFS patterns in .gitattributes will be turned into pointers
    # at commit time, so their working-tree size is irrelevant.
    lfs_patterns = _lfs_tracked_patterns(repo)
    def _is_lfs_tracked(rel: str) -> bool:
        return any(fnmatch.fnmatch(rel, pat) for pat in lfs_patterns)
    for p in repo.rglob("*"):
        if not p.is_file():
            continue
        try:
            rel = p.relative_to(repo).as_posix()
        except ValueError:
            continue
        if "/.git/" in "/" + rel or rel.startswith(".git/"):
            continue
        if _is_ignored(rel):
            continue
        if _is_lfs_tracked(rel):
            continue
        # Skip our own push tools and __pycache__
        if rel.startswith("apps/") and rel.endswith(".py"):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > threshold:
            big.append((rel, size / 1024 / 1024))
    big.sort(key=lambda x: -x[1])
    return big


def _has_remote(repo: Path) -> bool:
    """True if `origin` (or any remote) is configured."""
    r = subprocess.run(
        ["git", "-C", str(repo), "remote"],
        capture_output=True, text=True,
    )
    return r.returncode == 0 and bool(r.stdout.strip())


def _prompt_set_remote(repo: Path) -> bool:
    """Offer to set origin to a GitHub repo from the user's account.
    Returns True if origin was set (or already correct), False if user aborted."""
    token = get_token()
    if not token:
        return False
    user_data = http_get(f"{GITHUB_API}/user", token)
    username = json.loads(user_data)["login"]

    print()
    warn(f"No remote configured for {repo.name}.")
    repos = _list_user_repos(token, username)
    if repos:
        info(f"  Your GitHub repos (newest first):")
        for i, (name, url) in enumerate(repos[:15], 1):
            print(f"    [{i}] {name}")
        if len(repos) > 15:
            print(f"    ... and {len(repos) - 15} more")
        info(f"    [N] Type a different repo name")
    else:
        info("  No repos on your GitHub account yet.")

    val = input("Repo to push to (blank to cancel): ").strip()
    if not val:
        print("[i] Aborted.")
        return False

    if val.isdigit() and repos and 1 <= int(val) <= min(len(repos), 15):
        repo_name = repos[int(val) - 1][0]
    else:
        repo_name = val

    remote_url = f"https://github.com/{username}/{repo_name}.git"
    subprocess.run(
        ["git", "-C", str(repo), "remote", "add", "origin", remote_url],
        check=True, capture_output=True,
    )
    ok(f"Set origin = {remote_url}")
    return True


def _list_user_repos(token: str, username: str) -> list[tuple[str, str]]:
    """List the user's repos, newest first. Returns [(name, html_url), ...]."""
    try:
        data = http_get(f"{GITHUB_API}/users/{username}/repos?per_page=30&sort=created&direction=desc",
                        token, params={"type": "owner"})
        items = json.loads(data)
        return [(it["name"], it["html_url"]) for it in items if isinstance(it, dict) and "name" in it]
    except (HttpError, json.JSONDecodeError, urllib.error.URLError):
        return []


def _push_only(repo: Path) -> None:
    """Push current branch, with upstream auto-fix and remote auto-setup."""
    if not _has_remote(repo):
        warn(f"No remote configured for {repo.name}.")
        if sys.stdin.isatty():
            choice = input("  Set one now? [Y/n]: ").strip().lower()
            if choice in ("", "y", "yes"):
                if not _prompt_set_remote(repo):
                    print("[!] Push aborted — no remote.")
                    return
            else:
                print("[!] Push aborted — no remote.")
                return
        else:
            print("[!] No remote configured. Run:  git -C \"<repo>\" remote add origin <url>")
            return

    print(f"[+] Pushing to remote...\n", end="")
    rc, stderr = _run_streaming(["git", "-C", str(repo), "push"])
    if rc != 0 and "no upstream branch" in stderr:
        branch = subprocess.run(
            ["git", "-C", str(repo), "rev-parse", "--abbrev-ref", "HEAD"],
            capture_output=True, text=True,
        ).stdout.strip() or "master"
        print(f"[i] Setting upstream to origin/{branch}...\n", end="")
        rc, stderr = _run_streaming(["git", "-C", str(repo), "push", "--set-upstream", "origin", branch])
    if rc != 0:
        print(f"[!] Push failed (exit {rc}).")
        # Common auth/identity failure: surface a hint.
        if "could not read Username" in stderr or "terminal prompts disabled" in stderr:
            info("    Hint: set a token via:  gh new <repo>   (will prompt for token)")
        return
    web_url = _get_remote_url(repo)
    print(f"[+] Done." + (f"  {web_url}" if web_url else ""))


def _has_lfs() -> bool:
    try:
        return subprocess.run(["git", "lfs", "version"],
                              capture_output=True).returncode == 0
    except FileNotFoundError:
        return False


def _lfs_install(repo: Path, patterns: list[str]) -> bool:
    """Run git lfs install + track for given patterns. Returns True on success."""
    print("[+] Setting up Git LFS...")
    if subprocess.run(["git", "lfs", "install"], cwd=str(repo),
                      capture_output=True).returncode != 0:
        return False
    ga = repo / ".gitattributes"
    existing = ga.read_text(encoding="utf-8", errors="ignore") if ga.exists() else ""
    new_lines = []
    for pat in patterns:
        line = f"{pat} filter=lfs diff=lfs merge=lfs -text"
        if line not in existing:
            new_lines.append(line)
    if new_lines:
        with ga.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            for line in new_lines:
                f.write(line + "\n")
        print(f"[+] Updated .gitattributes")
    return True


def _handle_large_files(repo: Path, big: list[tuple[str, float]]) -> bool:
    """Interactively resolve >100MB files. Returns True if user wants to continue."""
    print(f"[!] {len(big)} file(s) exceed GitHub's 100 MB limit:")
    for rel, mb in big[:10]:
        print(f"    {rel}  ({mb:.1f} MB)")
    if len(big) > 10:
        print(f"    ... and {len(big) - 10} more")
    print()
    print("    [1] Skip via .gitignore (recommended)")
    print("    [2] Set up Git LFS (keeps large files in repo)")
    print("    [3] Force anyway (GitHub will REJECT the push)")
    print("    [0] Cancel")
    choice = input("    Choose [0-3]: ").strip()
    if choice == "0" or not choice:
        print("[i] Aborted.")
        return False
    if choice == "1":
        gi = repo / ".gitignore"
        existing = gi.read_text(encoding="utf-8", errors="ignore").splitlines() if gi.exists() else []
        added = []
        for rel, _ in big:
            if rel not in existing:
                added.append(rel)
        if added:
            with gi.open("a", encoding="utf-8") as f:
                if existing and not existing[-1].endswith(""):
                    f.write("\n")
                for rel in added:
                    f.write(rel + "\n")
            print(f"[+] Added {len(added)} path(s) to .gitignore")
        else:
            print(f"[i] All files already in .gitignore")
        # Untrack any that are already tracked
        for rel, _ in big:
            subprocess.run(
                ["git", "-C", str(repo), "rm", "--cached", "-r", "--", rel],
                capture_output=True,
            )
        return False  # user chose to skip, do not push
    if choice == "2":
        if not _has_lfs():
            print("[!] `git lfs` is not installed.")
            print("    Install: winget install GitHub.LFS  (or  choco install git-lfs)")
            print("    Or download: https://git-lfs.github.com")
            return False
        # Derive patterns from extensions of the large files
        exts = sorted({Path(rel).suffix for rel, _ in big if Path(rel).suffix})
        patterns = [f"*{ext}" for ext in exts] if exts else []
        if not patterns:
            patterns = [Path(big[0][0]).name]
        if not _lfs_install(repo, patterns):
            print("[!] LFS setup failed.")
            return False
        # Untrack and re-add under LFS
        for rel, _ in big:
            subprocess.run(
                ["git", "-C", str(repo), "rm", "--cached", "-r", "--", rel],
                capture_output=True,
            )
        # The next `git add` (in cmd_push) will pick them up via LFS
        return True
    if choice == "3":
        return True
    print("[i] Aborted.")
    return False


def cmd_push(repo: Path, msg: str):
    """Git add + commit + push."""
    _ensure_lf_preserved(repo)
    if not _has_global_git_identity():
        _prompt_git_identity()
    # First check files already in history (LFS cannot fix these after-the-fact)
    hist_big = _list_large_files_in_history(repo, 100)
    if hist_big:
        print(f"[!] {len(hist_big)} file(s) in your git history exceed 100 MB.")
        print("    LFS cannot retroactively migrate these — GitHub will reject the push.")
        for rel, mb in hist_big[:10]:
            print(f"    {rel}  ({mb:.1f} MB)")
        if len(hist_big) > 10:
            print(f"    ... and {len(hist_big) - 10} more")
        exts = sorted({Path(rel).suffix for rel, _ in hist_big if Path(rel).suffix})
        ext_pattern = ",".join(f"*{e}" for e in exts) if exts else ""
        if ext_pattern:
            print(f"    Fix: git lfs migrate import --include=\"{ext_pattern}\" --everything")
        else:
            print("    Fix: git lfs migrate import --everything")
        print("    (Or wipe remote history and start fresh with LFS from the start.)")
        if input("    Continue anyway (will likely fail)? [y/N] ").strip().lower() != "y":
            print("[i] Aborted. Run the migrate command above, then retry.")
            return
    big = _list_large_files(repo, 100)
    if big:
        if not _handle_large_files(repo, big):
            return
    result = subprocess.run(
        ["git", "-C", str(repo), "status", "--porcelain"],
        capture_output=True, text=True
    ).stdout.strip()

    if not result:
        # Nothing to commit, but maybe LFS objects still need to be pushed (e.g. after
        # `git lfs migrate`). Just push whatever is on the branch.
        return _push_only(repo)

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

    _push_only(repo)


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


def _push_local(here: Path, username: str, repo_name: str):
    """Git init + remote + add + commit + push from an existing directory."""
    # Pre-check git identity so the commit step doesn't fail-and-recover.
    if not _has_global_git_identity():
        if _prompt_git_identity() is None:
            print("[!] Cannot commit without a git identity.")
            return

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
            # Pre-check passed but commit still failed (race / config layer) — try once more.
            if _prompt_git_identity() is None:
                print("[!] Commit aborted — no git identity.")
                return
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


def cmd_wipe_remote(repo: Path) -> None:
    """Destructive: replace remote history with a single empty commit.
    Keeps the repo, URL, stars, watches. All branches except default become orphans
    that need manual deletion."""
    import tempfile, shutil
    token = get_token()
    if not token:
        return
    remote_url = _get_remote_url(repo)
    if not remote_url:
        print("[!] No remote URL configured.")
        return
    owner, name = _parse_owner_repo(remote_url)
    if not owner or not name:
        print(f"[!] Cannot parse owner/repo from remote URL: {remote_url}")
        return

    # Confirm twice — this is irreversible on the server side.
    print(f"[!] This will REPLACE all commits on GitHub with a single empty commit.")
    print(f"    Target: https://github.com/{owner}/{name}")
    if input("    Continue? [y/N] ").strip().lower() != "y":
        print("[i] Aborted.")
        return
    if input(f"    Type the repo name '{name}' to confirm: ").strip() != name:
        print("[i] Aborted.")
        return

    default_branch = _get_default_branch(owner, name, token) or "main"

    # Build an orphan branch in a temp clone, then force-push it to the default.
    with tempfile.TemporaryDirectory() as tmp:
        tmp_path = Path(tmp) / name
        print(f"[+] Cloning to {tmp_path}...")
        rc, out = _run_streaming(
            ["git", "clone", remote_url, str(tmp_path)],
        )
        if rc != 0:
            print("[!] Clone failed.")
            return
        # Make this checkout the orphan branch
        subprocess.run(
            ["git", "-C", str(tmp_path), "checkout", "--orphan", "tmp_wipe"],
            check=True, capture_output=True,
        )
        # Remove all tracked files
        subprocess.run(
            ["git", "-C", str(tmp_path), "rm", "-rf", "."],
            capture_output=True,  # OK if there are no files
        )
        # Commit the empty tree
        subprocess.run(
            ["git", "-C", str(tmp_path), "commit", "--allow-empty",
             "-m", "wipe: replace history with empty commit"],
            check=True, capture_output=True,
        )
        # Force-push the orphan branch to the default
        print(f"[+] Force-pushing orphan to {default_branch}...")
        rc, out = _run_streaming(
            ["git", "-C", str(tmp_path), "push", "origin",
             "tmp_wipe:" + default_branch, "--force"],
        )
        if rc != 0:
            print("[!] Force-push failed.")
            return
        # Delete the orphan branch on remote
        subprocess.run(
            ["git", "-C", str(tmp_path), "push", "origin", "--delete", "tmp_wipe"],
            capture_output=True,
        )
        # Also delete any other branches the server still has
        ls = subprocess.run(
            ["git", "-C", str(tmp_path), "ls-remote", "--heads", "origin"],
            capture_output=True, text=True,
        )
        for line in ls.stdout.splitlines():
            parts = line.split()
            if len(parts) != 2 or not parts[1].startswith("refs/heads/"):
                continue
            br = parts[1].removeprefix("refs/heads/")
            if br != default_branch:
                subprocess.run(
                    ["git", "-C", str(tmp_path), "push", "origin", "--delete", br],
                    capture_output=True,
                )
                print(f"    - deleted remote branch: {br}")

    print(f"[+] Done. https://github.com/{owner}/{name} now has a single empty commit on {default_branch}.")


def _get_default_branch(owner: str, name: str, token: str) -> str | None:
    try:
        data = http_get(f"{GITHUB_API}/repos/{owner}/{name}", token)
        return json.loads(data).get("default_branch")
    except Exception:
        return None


def _parse_owner_repo(remote_url: str) -> tuple[str | None, str | None]:
    """Extract (owner, repo) from a GitHub remote URL.
    Supports https://github.com/owner/repo[.git] and git@github.com:owner/repo[.git]."""
    import re
    m = re.search(r"github\.com[:/]([^/]+)/([^/.]+?)(?:\.git)?/?$", remote_url)
    if m:
        return m.group(1), m.group(2)
    return None, None


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
        ("Wipe remote history", "wipe", "Destructive: replace all GitHub history with a single empty commit"),
    ]
    choice = show_menu(choices, header)
    action = ["push", "pull", "status", "switch", "wipe"][choice - 1]

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
            ("Wipe remote history", "wipe", "Destructive: replace all GitHub history with a single empty commit"),
        ]
        choice = show_menu(choices, header)
        action = ["push", "pull", "status", "switch", "wipe"][choice - 1]

    if action == "status":
        cmd_status(repo)
        return

    if action == "wipe":
        cmd_wipe_remote(repo)
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
    parser.add_argument("command", nargs="?", help="push, pull, new, init, status, wipe")
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

    elif cmd == "wipe":
        repo = find_repo_root(Path.cwd())
        if not repo:
            print("[!] Not in a git repository.")
            sys.exit(1)
        cmd_wipe_remote(repo)

    else:
        print(f"[!] Unknown command: {cmd}")
        print("    Available: push, pull, new, init, status, wipe")
        print("    Or run 'gh' for interactive menu.")


if __name__ == "__main__":
    main()
