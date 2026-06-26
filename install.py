#!/usr/bin/env python3
r"""
devtool — install / update / uninstall / run tools.
No params, no launchers, no mess.
"""

import os
import platform
import re
import shutil
import ssl
import subprocess
import sys
import urllib.request
from pathlib import Path

GITHUB_USER = "jimprivate"
GITHUB_REPO = "devtool"
GITHUB_BRANCH = "master"
APPS_SUBDIR = "apps"

APPS_DIR = Path.home() / "devtool" / APPS_SUBDIR
DEVTOOL_DIR = APPS_DIR.parent
REPO_URL = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}.git"


# ── HTTP ─────────────────────────────────────────────────────────────────────

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "devtool"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()


# ── Git ───────────────────────────────────────────────────────────────────────

def refresh_path():
    if platform.system() == "Windows":
        machine = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('Path','Machine')"],
            capture_output=True, text=True).stdout.strip()
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + machine


def find_git():
    refresh_path()
    return shutil.which("git")


def _fix_mac_ssl():
    """Run Install Certificates.command for Python on macOS if SSL is broken."""
    import glob as _glob
    for app in sorted(_glob.glob("/Applications/Python*/Install Certificates.command"), reverse=True):
        print(f"    Running {app}...")
        subprocess.run([app], capture_output=True, timeout=120)


def _check_ssl():
    """Test SSL and offer to fix macOS certificate issue."""
    if platform.system() != "Darwin":
        return
    try:
        urllib.request.urlopen("https://api.github.com", timeout=10)
    except ssl.SSLCertVerificationError:
        print()
        print("[!] SSL certificate error detected on macOS.")
        print("    Python cannot verify HTTPS connections.")
        while True:
            print("    1. Auto-fix (run Install Certificates.command)")
            print("    2. Skip for now")
            c = input("    Select [1]: ").strip() or "1"
            if c == "1":
                _fix_mac_ssl()
                # verify it worked
                try:
                    urllib.request.urlopen("https://api.github.com", timeout=10)
                    print("    SSL fixed!")
                    return
                except Exception:
                    print("    Still failing. Try manually:")
                    print("    open /Applications/Python*/Install Certificates.command")
                    return
            elif c == "2":
                return
            print("Invalid.")


# ── Git Identity ──────────────────────────────────────────────────────────────

def _parse_gitconfig():
    """Parse ~/.gitconfig and return all identity sections as list of dicts."""
    cfg = Path.home() / ".gitconfig"
    if not cfg.exists():
        return []

    identities = []
    current = {"name": None, "email": None, "label": "(default)"}
    content = cfg.read_text()

    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("[include]"):
            # TODO: resolve path and recurse — skip for now
            continue
        m_include = re.match(r'\[user\s+"([^"]+)"\]', stripped)
        if m_include:
            if current["name"] or current["email"]:
                identities.append(current)
            current = {"name": None, "email": None, "label": m_include.group(1)}
            continue
        if stripped == "[user]":
            if current["name"] or current["email"]:
                identities.append(current)
            current = {"name": None, "email": None, "label": "(default)"}
            continue
        if stripped.startswith("name"):
            current["name"] = stripped.split("=", 1)[1].strip().strip('"')
        elif stripped.startswith("email"):
            current["email"] = stripped.split("=", 1)[1].strip().strip('"')

    if current["name"] or current["email"]:
        identities.append(current)

    return identities


def _add_git_identity(name, email, label=None):
    """Save a new identity to ~/.gitconfig, optionally as a named section."""
    git = find_git()
    cfg_path = Path.home() / ".gitconfig"
    content = cfg_path.read_text() if cfg_path.exists() else ""

    if label:
        section = f'[user "{label}"]\n'
    else:
        section = "[user]\n"

    entry = f"{section}\tname = {name}\n\temail = {email}\n"
    cfg_path.write_text(content + "\n" + entry)
    return True


def _set_devtool_identity(name, email):
    """Apply identity to devtool repo's local git config (does not affect other repos)."""
    git = find_git()
    subprocess.run(
        [git, "-C", str(DEVTOOL_DIR), "config", "user.name", name],
        check=True, capture_output=True)
    subprocess.run(
        [git, "-C", str(DEVTOOL_DIR), "config", "user.email", email],
        check=True, capture_output=True)


def _check_git_identity():
    """
    Show identity picker for this devtool repo.
    Always asks, pre-selecting the currently active identity.
    """
    git = find_git()
    if not git:
        return

    identities = _parse_gitconfig()

    # Get the currently active identity (local > global)
    local_name = subprocess.run(
        [git, "-C", str(DEVTOOL_DIR), "config", "user.name"],
        capture_output=True, text=True).stdout.strip()
    local_email = subprocess.run(
        [git, "-C", str(DEVTOOL_DIR), "config", "user.email"],
        capture_output=True, text=True).stdout.strip()

    if local_name and local_email:
        active = f"{local_name} <{local_email}>"
    else:
        active = None

    while True:
        print()
        if active:
            print(f"    Current: {active}")
        if identities:
            for i, ident in enumerate(identities, 1):
                display = f"{ident['label']}: {ident['name']} <{ident['email']}>"
                marker = " [current]" if display.startswith(active or "") else ""
                print(f"    {i}. {display}{marker}")
            print(f"    {len(identities) + 1}. Add new identity")
        else:
            print("    No identities found in ~/.gitconfig.")
            print("    1. Add new identity")

        default = "0"
        choice = input(f"    Select identity [{default}]: ").strip() or default

        if choice == "0":
            if active:
                print(f"    Keeping: {active}")
                return
            print("    Please select an identity.")
            continue

        try:
            c = int(choice)
        except ValueError:
            print("    Invalid.")
            continue

        if identities and 1 <= c <= len(identities):
            ident = identities[c - 1]
            _set_devtool_identity(ident["name"], ident["email"])
            print(f"    git identity: {ident['name']} <{ident['email']}>")
            return
        elif c == len(identities) + 1:
            name = input("    Name: ").strip()
            if not name:
                print("    Required.")
                continue
            email = input("    Email: ").strip()
            if not email:
                print("    Required.")
                continue
            label = input("    Label (e.g. work, personal, leave blank for default): ").strip()
            _add_git_identity(name, email, label or None)
            _set_devtool_identity(name, email)
            print(f"    git identity: {name} <{email}>")
            return
        else:
            print("    Invalid.")


def install_git():
    sysname = platform.system()
    print("[+] git not found. Installing...")

    if sysname == "Windows":
        try:
            r = subprocess.run(
                ["winget", "install", "--id", "Git.Git", "-e",
                 "--source", "winget",
                 "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, timeout=180)
            if r.returncode == 0:
                refresh_path()
                if find_git():
                    print("    git installed via winget.")
                    return
        except Exception as e:
            print(f"    winget failed: {e}")

        url = "https://github.com/git-for-windows/git/releases/download/v2.47.0.windows.1/MinGit-64bit.zip"
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "mingit.zip"
        dest = Path.home() / ".local" / "git"
        print(f"    downloading portable git to {dest}...")
        urllib.request.urlretrieve(url, tmp)
        import zipfile
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest.parent)
        tmp.unlink()
        for sub in dest.parent.iterdir():
            if sub.name.startswith("MinGit"):
                git_bin = sub / "cmd"
                break
        else:
            git_bin = dest / "cmd"
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(git_bin)
        print(f"    git extracted to {dest.parent}.")

    elif sysname == "Darwin":
        if shutil.which("brew"):
            subprocess.run(["brew", "install", "git"], check=True)
        else:
            print("    Run:  xcode-select --install")
            sys.exit(1)

    else:
        for cmd_pair in [
            (["apt-get", "update"], ["apt-get", "install", "-y", "git"]),
            (["dnf", "install", "-y", "git"], None),
            (["pacman", "-S", "--noconfirm", "git"], None),
            (["apk", "add", "git"], None),
        ]:
            setup, install = cmd_pair
            inst_cmd = install or setup
            try:
                if setup[0] == "apt-get":
                    subprocess.run(setup, check=True, capture_output=True)
                subprocess.run(inst_cmd, check=True, capture_output=True)
                print(f"    git installed via {' '.join(inst_cmd)}.")
                return
            except Exception:
                pass
        print("[!] Could not auto-install git. Install manually.")
        sys.exit(1)


# ── Path ─────────────────────────────────────────────────────────────────────

def path_line():
    apps = str(APPS_DIR)
    sep = ";" if platform.system() == "Windows" else ":"
    return f'export PATH="$PATH:{apps}"\n'


def in_path():
    sep = ";" if platform.system() == "Windows" else ":"
    return str(APPS_DIR) in os.environ.get("PATH", "").split(sep)


def add_to_path():
    if in_path():
        print("    Already in PATH.")
        return

    if platform.system() == "Windows":
        machine = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('Path','Machine')"],
            capture_output=True, text=True).stdout.strip()
        new_path = machine + ";" + str(APPS_DIR) if machine else str(APPS_DIR)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"[Environment]::SetEnvironmentVariable('Path', '{new_path}', 'User')"],
            check=True, capture_output=True)
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(APPS_DIR)
        print("    Added to system PATH. Restart your terminal to use tools.")

    else:
        rc = Path.home() / (
            ".zshrc" if (Path.home() / ".zshrc").exists()
            else ".bashrc" if (Path.home() / ".bashrc").exists()
            else ".profile"
        )
        line = path_line()
        existing = rc.read_text() if rc.exists() else ""
        if line.strip() not in existing:
            rc.write_text(existing + line)
            print(f"    Added to {rc}. Run:  source {rc}")
        else:
            print("    Already in PATH.")


def remove_from_path():
    if platform.system() == "Windows":
        machine = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('Path','Machine')"],
            capture_output=True, text=True).stdout.strip()
        sep = ";"
        parts = [p for p in machine.split(sep) if p != str(APPS_DIR)]
        new_path = sep.join(parts)
        subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             f"[Environment]::SetEnvironmentVariable('Path', '{new_path}', 'User')"],
            check=True, capture_output=True)
    else:
        rc = Path.home() / (
            ".zshrc" if (Path.home() / ".zshrc").exists()
            else ".bashrc" if (Path.home() / ".bashrc").exists()
            else ".profile"
        )
        if not rc.exists():
            return
        line = path_line().strip()
        lines = [l for l in rc.read_text().splitlines() if l.strip() != line]
        rc.write_text("\n".join(lines) + "\n")


# ── Commands ─────────────────────────────────────────────────────────────────

def _make_wrappers():
    """Create launchers so tools work as global commands on any platform."""
    sysname = platform.system()
    for py_tool in sorted(APPS_DIR.glob("*.py")):
        # Always ensure .py is executable on Unix
        if sysname != "Windows":
            py_tool.chmod(0o755)

        if sysname == "Windows":
            wrapper = py_tool.with_suffix(".cmd")
            content = f'@echo off\npython "%~dp0{py_tool.name}" %*\n'
            if wrapper.exists():
                existing = wrapper.read_text(encoding="utf-8", errors="replace")
                if existing == content:
                    continue
            wrapper.write_text(content, encoding="utf-8")
            print(f"    + {wrapper.name}")
        else:
            # Unix: create a shell wrapper (no extension) so `go-github` works directly
            wrapper = py_tool.with_suffix("")
            content = f"#!/bin/sh\nexec python3 \"$HOME/devtool/apps/{py_tool.name}\" \"$@\"\n"
            if wrapper.exists():
                existing = wrapper.read_text()
                if existing == content:
                    continue
            wrapper.write_text(content)
            wrapper.chmod(0o755)
            print(f"    + {wrapper.name}")


def _is_worktree_clean():
    """Check if the git working tree is clean (no staged/unstaged changes)."""
    git = find_git()
    if not git:
        return True
    r = subprocess.run(
        [git, "-C", str(DEVTOOL_DIR), "status", "--porcelain"],
        capture_output=True, text=True)
    return r.stdout.strip() == ""


def _clean_worktree_for_pull():
    """
    Ensure the working tree is clean so a fast-forward pull can proceed.
    Prompts user if there are local changes.
    """
    if _is_worktree_clean():
        return True

    git = find_git()
    print("    Local changes detected in ~/devtool.")
    print("    1. Stash changes (restore later)")
    print("    2. Discard changes and pull latest")
    while True:
        choice = input("    Select [1]: ").strip()
        if choice == "":
            choice = "1"
        if choice not in ("1", "2"):
            print("    Invalid.")
            continue

        if choice == "1":
            r = subprocess.run(
                [git, "-C", str(DEVTOOL_DIR), "stash"],
                capture_output=True, text=True)
            if r.returncode == 0:
                print("    Changes stashed.")
                return True
            else:
                print(f"    Stash failed: {r.stderr.strip()}")
                return False
        else:
            r = subprocess.run(
                [git, "-C", str(DEVTOOL_DIR), "checkout", "--", "."],
                capture_output=True, text=True)
            if r.returncode == 0:
                print("    Changes discarded.")
                return True
            else:
                print(f"    Discard failed: {r.stderr.strip()}")
                return False


def _is_shallow_clone():
    """Check if the devtool repo is a shallow clone (missing full history)."""
    git = find_git()
    if not git:
        return False
    r = subprocess.run(
        [git, "-C", str(DEVTOOL_DIR), "rev-parse", "--is-shallow-repository"],
        capture_output=True, text=True)
    return r.stdout.strip() == "true"


def _git_pull():
    """Fetch and fast-forward to the latest remote commit."""
    if _is_shallow_clone():
        print("    Shallow clone detected — fetching full history...")
        git = find_git()
        subprocess.run(
            [git, "-C", str(DEVTOOL_DIR), "fetch", "--unshallow"],
            capture_output=True, text=True)

    if not _clean_worktree_for_pull():
        print("[!] Cannot pull. Please resolve manually.")
        return False
    git = find_git()
    r = subprocess.run(
        [git, "-C", str(DEVTOOL_DIR), "pull", "--ff-only"],
        capture_output=True, text=True)
    if r.returncode != 0:
        print(f"[!] git pull failed: {r.stderr.strip()}")
        return False
    return True


def cmd_install():
    print()
    print("[ Install ]")
    _check_ssl()
    if not find_git():
        install_git()
        refresh_path()
    _check_git_identity()

    if DEVTOOL_DIR.exists() and (DEVTOOL_DIR / ".git").exists():
        print(f"[+] ~/devtool already exists — pulling latest...")
        _git_pull()
    else:
        print(f"[+] Cloning {REPO_URL}")
        DEVTOOL_DIR.mkdir(parents=True, exist_ok=True)
        subprocess.run(
            ["git", "clone", "--branch", GITHUB_BRANCH,
             "--depth", "1", REPO_URL, str(DEVTOOL_DIR)],
            check=True, capture_output=True)

    for pf in sorted(APPS_DIR.glob("*.py")):
        print(f"    + {pf.name}")

    add_to_path()
    _make_wrappers()
    print("[+] Install complete.")


def cmd_update():
    print()
    print("[ Update ]")
    if not DEVTOOL_DIR.exists():
        print("[!] Not installed. Run Install first.")
        return
    if not find_git():
        print("[!] git not found.")
        return
    _check_ssl()
    _check_git_identity()
    print(f"[+] Pulling into ~/devtool...")
    if not _git_pull():
        return
    for pf in sorted(APPS_DIR.glob("*.py")):
        print(f"    ~ {pf.name}")
    _make_wrappers()
    print("[+] Update complete.")


def cmd_uninstall():
    print()
    print("[ Uninstall ]")
    print("    This removes ~/devtool and removes PATH entry.")
    print("    Your code is safe — ~/devtool will be deleted.")
    confirm = input("    Type 'yes' to confirm: ").strip()
    if confirm != "yes":
        print("    Cancelled.")
        return

    remove_from_path()
    shutil.rmtree(DEVTOOL_DIR)
    print("[+] ~/devtool removed.")
    print("    Restart your terminal. Tools are gone.")


def _tool_description(tool_path: Path) -> str:
    """Extract first line of docstring as short description."""
    try:
        text = tool_path.read_text(encoding="utf-8", errors="ignore")
    except Exception:
        return ""
    # Find first triple-quoted docstring
    start = text.find('"""')
    if start == -1:
        start = text.find("'''")
        if start == -1:
            return ""
    quote = text[start:start + 3]
    start += 3
    end = text.find(quote, start)
    if end == -1:
        return ""
    # Grab first non-empty line from inside the docstring
    for line in text[start:end].strip().splitlines():
        line = line.strip()
        if line:
            # Strip leading comment characters used in shebang-style docs
            return line.lstrip("#").strip()
    return ""


def cmd_list():
    print()
    print("[ Tools ]")
    if APPS_DIR.exists():
        tools = sorted(APPS_DIR.glob("*.py"))
    else:
        tools = []
    if tools:
        for t in tools:
            desc = _tool_description(t)
            print(f"    {t.stem}   {desc}")
    else:
        print("    No tools found. Run Install first.")


def cmd_run():
    if not APPS_DIR.exists():
        print("[!] Not installed. Run Install first.")
        return

    tools = sorted(APPS_DIR.glob("*.py"))
    if not tools:
        print("[!] No tools found.")
        return

    print()
    print("[ Run ]")
    print("-" * 40)
    for i, t in enumerate(tools, 1):
        print(f"  {i}. {t.stem}")
    print("  0. Back")
    print()

    while True:
        val = input("Select: ").strip()
        if val == "0":
            return
        try:
            n = int(val)
            if 1 <= n <= len(tools):
                break
        except ValueError:
            pass
        print("Invalid.")

    tool = tools[n - 1]
    py = "py" if platform.system() == "Windows" else "python3"
    print()
    subprocess.run([py, str(tool)])


# ── Menu ──────────────────────────────────────────────────────────────────────

MENU_INSTALL   = ("Install / Update",    "install")
MENU_UPDATE    = ("Check for Updates",   "update")
MENU_UNINSTALL = ("Uninstall",           "uninstall")
MENU_LIST      = ("List Tools",          "list")
MENU_RUN       = ("Run a Tool",          "run")


def show_menu(header):
    options = [MENU_INSTALL, MENU_UPDATE, MENU_LIST, MENU_RUN, MENU_UNINSTALL]
    print()
    print(header)
    print("-" * 40)
    for i, (label, _) in enumerate(options, 1):
        print(f"  {i}. {label}")
    print("  0. Exit")
    print()
    while True:
        val = input("Select: ").strip()
        if val == "0":
            sys.exit(0)
        try:
            n = int(val)
            if 1 <= n <= len(options):
                return options[n - 1][1]
        except ValueError:
            pass
        print("Invalid.")


def main():
    is_installed = DEVTOOL_DIR.exists() and (DEVTOOL_DIR / ".git").exists()

    if is_installed:
        branch = subprocess.run(
            ["git", "-C", str(DEVTOOL_DIR), "branch", "--show-current"],
            capture_output=True, text=True).stdout.strip() or GITHUB_BRANCH
        header = f"[ devtool | {branch} ]"
    else:
        header = "[ devtool | not installed ]"

    action = show_menu(header)

    if action == "install":
        cmd_install()
    elif action == "update":
        cmd_update()
    elif action == "uninstall":
        cmd_uninstall()
    elif action == "list":
        cmd_list()
    elif action == "run":
        cmd_run()

    input("\nPress Enter to continue...")
    main()


if __name__ == "__main__":
    main()
