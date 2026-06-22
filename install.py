#!/usr/bin/env python3
r"""
devtool — install / update / uninstall / run tools.
No params, no launchers, no mess.
"""

import os
import platform
import shutil
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


def _prompt_git_identity():
    """Ask user for git identity, save globally."""
    git = find_git()
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

    subprocess.run([git, "config", "--global", "user.name", name], check=True)
    subprocess.run([git, "config", "--global", "user.email", email], check=True)
    print(f"    Saved to ~/.gitconfig.")


def _check_git_identity():
    """Ensure global git user.name and user.email are set. Prompt if missing."""
    git = find_git()
    if not git:
        return

    name_r = subprocess.run([git, "config", "--global", "user.name"],
                           capture_output=True, text=True)
    email_r = subprocess.run([git, "config", "--global", "user.email"],
                            capture_output=True, text=True)

    name_ok = name_r.returncode == 0 and name_r.stdout.strip()
    email_ok = email_r.returncode == 0 and email_r.stdout.strip()

    if name_ok and email_ok:
        print(f"    git identity: {name_r.stdout.strip()} <{email_r.stdout.strip()}>")
        return

    _prompt_git_identity()


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
    """Create .cmd launchers for each .py tool so they work as global commands."""
    py = "py" if platform.system() == "Windows" else "python3"
    for py_tool in sorted(APPS_DIR.glob("*.py")):
        wrapper = py_tool.with_suffix(".cmd")
        content = f'@echo off\n{py} "%~dp0{py_tool.name}" %*\n'
        if wrapper.exists():
            existing = wrapper.read_text(encoding="utf-8", errors="replace")
            if existing == content:
                continue
        wrapper.write_text(content, encoding="utf-8")
        print(f"    + {wrapper.name}")


def cmd_install():
    print()
    print("[ Install ]")
    if not find_git():
        install_git()
        refresh_path()
    _check_git_identity()

    if DEVTOOL_DIR.exists() and (DEVTOOL_DIR / ".git").exists():
        print(f"[+] ~/devtool already exists — pulling latest...")
        subprocess.run(["git", "-C", str(DEVTOOL_DIR), "pull", "--ff-only"],
                      check=True, capture_output=True)
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
    _check_git_identity()
    print(f"[+] Pulling into ~/devtool...")
    subprocess.run(["git", "-C", str(DEVTOOL_DIR), "pull", "--ff-only"],
                   check=True, capture_output=True)
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
