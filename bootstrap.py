#!/usr/bin/env python3
r"""
bootstrap: one-time setup for the w11 tools collection.

Source of truth (high change frequency):
    https://github.com/jimprivate/tools/tree/main/tools

This file (low change frequency — lives on GitHub):
    https://github.com/jimprivate/tools/blob/main/bootstrap.py
"""

import os
import platform
import shutil
import stat
import subprocess
import sys
import urllib.request
from pathlib import Path

# ============================================================
# Config — change here if you ever move the repo
# ============================================================

GITHUB_USER = "jimprivate"
GITHUB_REPO = "devtool"
GITHUB_BRANCH = "master"
TOOLS_SUBDIR = "tools"

API_LIST = f"https://api.github.com/repos/{GITHUB_USER}/{GITHUB_REPO}/contents/{TOOLS_SUBDIR}?ref={GITHUB_BRANCH}"
RAW_BASE  = f"https://raw.githubusercontent.com/{GITHUB_USER}/{GITHUB_REPO}/{GITHUB_BRANCH}/{TOOLS_SUBDIR}"

# ============================================================
# Paths
# ============================================================

def install_dir():
    return Path.home() / "w11-tools" / "tools"

def bin_dir():
    return Path.home() / ".local" / "bin"

# ============================================================
# HTTP — stdlib only
# ============================================================

def http_get(url):
    req = urllib.request.Request(url, headers={"User-Agent": "w11-bootstrap"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return r.read()

def http_json(url):
    import json
    return json.loads(http_get(url).decode("utf-8"))

# ============================================================
# Git install — only called when git is missing
# ============================================================

def install_git():
    sysname = platform.system()
    print("[+] git not found — installing...")

    if sysname == "Windows":
        # Win: try winget first, fallback to direct download
        import urllib.request as _urllib
        try:
            import subprocess as _sub
            r = _sub.run(["winget", "install", "--id", "Git.Git", "-e",
                          "--source", "winget",
                          "--accept-package-agreements", "--accept-source-agreements"],
                         capture_output=True, timeout=120)
            if r.returncode == 0:
                # Refresh PATH from registry
                user_path  = os.environ.get("PATH", "")
                machine_path = _sub.run(
                    ["powershell", "-NoProfile", "-Command",
                     "[Environment]::GetEnvironmentVariable('Path','Machine')"],
                    capture_output=True, text=True).stdout.strip()
                os.environ["PATH"] = user_path + os.pathsep + machine_path
                if shutil.which("git"):
                    print("    git installed via winget.")
                    return True
        except Exception as e:
            print(f"    winget failed: {e}")

        # Fallback: direct portable git zip
        url = "https://github.com/git-for-windows/git/releases/download/v2.47.0.windows.1/MinGit-64bit.zip"
        tmp = Path(os.environ.get("TEMP", "/tmp")) / "mingit.zip"
        dest = Path.home() / ".local" / "git"
        print(f"    downloading portable git to {dest}...")
        _urllib.request.urlretrieve(url, tmp)
        import zipfile
        with zipfile.ZipFile(tmp) as z:
            z.extractall(dest.parent)
        tmp.unlink()
        # Find the extracted folder (MinGit-xxx)
        for sub in dest.parent.iterdir():
            if sub.name.startswith("MinGit"):
                git_bin = sub / "cmd"
                break
        else:
            git_bin = dest / "cmd"
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + str(git_bin)
        print(f"    git installed to {dest.parent}.")
        return True

    elif sysname == "Darwin":
        # macOS: brew or xcode-select
        if shutil.which("brew"):
            subprocess.run(["brew", "install", "git"], check=True)
        else:
            print("    Homebrew not found. Run:  xcode-select --install")
            sys.exit(1)
        return True

    else:
        # Linux: detect package manager
        for cmd in [
            (["apt-get", "update"], ["apt-get", "install", "-y", "git"]),
            (["dnf", "install", "-y", "git"], None),
            (["pacman", "-S", "--noconfirm", "git"], None),
            (["apk", "add", "git"], None),
        ]:
            installer = cmd[1] or cmd[0]
            try:
                if cmd[0][0] == "apt-get":
                    subprocess.run(cmd[0], check=True, capture_output=True)
                subprocess.run(installer, check=True, capture_output=True)
                print(f"    git installed via {' '.join(installer)}.")
                return True
            except Exception:
                pass
        print("[!] Could not auto-install git. Install manually via your package manager.")
        sys.exit(1)

# ============================================================
# Git — ensure available, install if missing
# ============================================================

def refresh_path():
    if platform.system() != "Windows":
        return
    import subprocess as _sub
    machine_path = _sub.run(
        ["powershell", "-NoProfile", "-Command",
         "[Environment]::GetEnvironmentVariable('Path','Machine')"],
        capture_output=True, text=True).stdout.strip()
    user_path = os.environ.get("PATH", "")
    os.environ["PATH"] = user_path + os.pathsep + machine_path

def ensure_git():
    refresh_path()
    if shutil.which("git"):
        return True
    install_git()
    refresh_path()
    if not shutil.which("git"):
        print("[!] git still not found after install. Please re-run bootstrap in a new shell.")
        sys.exit(1)

# ============================================================
# Pull tools via git clone
# ============================================================

def pull_tools():
    inst = install_dir()
    repo_url = f"https://github.com/{GITHUB_USER}/{GITHUB_REPO}.git"
    tools_instatl = inst.parent / ".git"

    if tools_instatl.exists():
        inst.mkdir(parents=True, exist_ok=True)
        print(f"[+] {inst} already has a repo — pulling latest.")
        subprocess.run(["git", "-C", str(inst.parent), "pull", "--ff-only"],
                       check=True, capture_output=True)
    else:
        print(f"[+] Cloning {repo_url}")
        subprocess.run(["git", "clone", "--branch", GITHUB_BRANCH,
                        "--depth", "1", repo_url, str(inst.parent)],
                       check=True, capture_output=True)

    # List what we got
    py_files = sorted(inst.glob("*.py"))
    for pf in py_files:
        print(f"    + {pf.name}")

# ============================================================
# Launchers — one per .py, on PATH
# ============================================================

def launcher_name(name):
    return name[:-3] if name.endswith(".py") else name

def make_launcher(py_path, bin_path):
    name = launcher_name(py_path.name)
    if platform.system() == "Windows":
        shim = bin_path / f"{name}.cmd"
        shim.write_text(
            f"@echo off\r\npython \"{py_path}\" %*\r\n",
            encoding="ascii",
        )
    else:
        shim = bin_path / name
        shim.write_text(
            f"#!/usr/bin/env bash\nexec python3 \"{py_path}\" \"$@\"\n",
            encoding="utf-8",
        )
        shim.chmod(shim.stat().st_mode | stat.S_IEXEC | stat.S_IXGRP | stat.S_IXOTH)
    return name

def install_launchers():
    inst = install_dir()
    py_files = sorted(inst.glob("*.py"))
    bd = bin_dir()
    bd.mkdir(parents=True, exist_ok=True)
    print(f"[+] Launchers -> {bd}")
    names = []
    for pf in py_files:
        n = make_launcher(pf, bd)
        print(f"    + {n}")
        names.append(n)
    return names

# ============================================================
# PATH warning
# ============================================================

def warn_path():
    bd = bin_dir()
    cur = os.environ.get("PATH", "")
    sep = ";" if platform.system() == "Windows" else ":"
    if str(bd) in cur.split(sep):
        return
    print()
    print(f"[!] {bd} not on PATH for this session.")
    if platform.system() == "Windows":
        print("    Run once to persist (PowerShell Admin):")
        print(f'      [Environment]::SetEnvironmentVariable("PATH", $env:PATH + ";{bd}", "User")')
    else:
        rc = Path.home() / (".zshrc" if (Path.home() / ".zshrc").exists() else ".bashrc")
        print(f"    Add to {rc}:")
        print(f'      export PATH="$PATH:{bd}"')
        print(f"    Then:  source {rc}")
    print()

# ============================================================
# Main
# ============================================================

def main():
    print(f"[bootstrap] python {sys.version.split()[0]} on {platform.system()}")

    ensure_git()
    pull_tools()
    names = install_launchers()
    warn_path()

    print("[bootstrap] Done. Open a new terminal and run:")
    for n in names:
        print(f"    {n}")
    print()
    print("To update: cd ~/w11-tools && git pull")

if __name__ == "__main__":
    main()
