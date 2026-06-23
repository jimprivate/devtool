#!/usr/bin/env python3
r"""
go-rclone — Google Drive upload / download / sync via rclone.

Single-file driver. Works on Windows / macOS / Linux.

Subcommands (all fall through to the interactive menu if no args):

    go-rclone                       # interactive menu
    go-rclone setup                 # install rclone + add 'gdrive' remote
    go-rclone config                # manage rclone remotes
    go-rclone upload  <local> [gdrive:path]    # copy local -> Drive
    go-rclone download [gdrive:path] <local>   # copy Drive -> local
    go-rclone sync    <local> [gdrive:path]    # mirror Drive <- local (destructive)
    go-rclone list    [gdrive:path]            # list Drive contents
    go-rclone browse                          # interactive path picker on Drive

Remote name defaults to 'gdrive'. Override with --remote NAME.
The first 'upload' / 'download' / 'list' will self-heal:
  - rclone missing  -> install it
  - 'gdrive' remote missing -> run `rclone config` interactively
"""

import argparse
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

REMOTE_NAME = "gdrive"

# ── Output helpers ───────────────────────────────────────────────────────────

def info(msg: str) -> None:
    print(f"  {msg}")


def ok(msg: str) -> None:
    print(f"  [+] {msg}")


def warn(msg: str) -> None:
    print(f"  [!] {msg}")


def err(msg: str) -> None:
    print(f"  [!!] {msg}")


# ── rclone binary ────────────────────────────────────────────────────────────

def _refresh_path() -> None:
    """On Windows, pull Machine PATH into the current process so a freshly
    installed rclone is visible without restarting the terminal."""
    if platform.system() != "Windows":
        return
    try:
        machine = subprocess.run(
            ["powershell", "-NoProfile", "-Command",
             "[Environment]::GetEnvironmentVariable('Path','Machine')"],
            capture_output=True, text=True, timeout=10).stdout.strip()
    except Exception:
        return
    if machine:
        os.environ["PATH"] = os.environ.get("PATH", "") + os.pathsep + machine


def find_rclone() -> str | None:
    _refresh_path()
    return shutil.which("rclone")


def install_rclone() -> str | None:
    """Install rclone on the host. Returns the path on success."""
    print()
    print("[+] rclone not found. Installing...")

    sysname = platform.system()

    if sysname == "Windows":
        # 1) winget
        try:
            r = subprocess.run(
                ["winget", "install", "--id", "Rclone.Rclone", "-e",
                 "--source", "winget",
                 "--accept-package-agreements", "--accept-source-agreements"],
                capture_output=True, timeout=240)
            if r.returncode == 0:
                _refresh_path()
                p = find_rclone()
                if p:
                    ok("rclone installed via winget.")
                    return p
        except Exception as e:
            warn(f"winget failed: {e}")

        # 2) chocolatey
        if shutil.which("choco"):
            try:
                subprocess.run(["choco", "install", "rclone", "-y"],
                               check=True, capture_output=True, timeout=300)
                _refresh_path()
                p = find_rclone()
                if p:
                    ok("rclone installed via choco.")
                    return p
            except Exception as e:
                warn(f"choco failed: {e}")

        # 3) scoop
        if shutil.which("scoop"):
            try:
                subprocess.run(["scoop", "install", "rclone"],
                               check=True, capture_output=True, timeout=300)
                _refresh_path()
                p = find_rclone()
                if p:
                    ok("rclone installed via scoop.")
                    return p
            except Exception as e:
                warn(f"scoop failed: {e}")

        # 4) portable fallback: download official zip into ~/.local/bin
        ok("Falling back to portable install in ~/.local/bin...")
        return _install_rclone_portable_windows()

    elif sysname == "Darwin":
        if shutil.which("brew"):
            try:
                subprocess.run(["brew", "install", "rclone"], check=True)
                p = find_rclone()
                if p:
                    ok("rclone installed via brew.")
                    return p
            except Exception as e:
                warn(f"brew failed: {e}")
        err("Install rclone manually: https://rclone.org/install/")
        return None

    else:  # Linux
        # Try the official install.sh (writes to /usr/local/bin/rclone) — needs sudo.
        try:
            url = "https://rclone.org/install.sh"
            script = subprocess.run(
                ["bash", "-c", f"curl -fsSL {url} | sudo bash"],
                capture_output=True, text=True, timeout=300)
            if script.returncode == 0:
                p = find_rclone()
                if p:
                    ok("rclone installed via official script.")
                    return p
        except Exception as e:
            warn(f"official install.sh failed: {e}")

        # Try distros
        for cmd_pair in [
            (["apt-get", "update"], ["apt-get", "install", "-y", "rclone"]),
            (["dnf", "install", "-y", "rclone"], None),
            (["pacman", "-S", "--noconfirm", "rclone"], None),
            (["apk", "add", "rclone"], None),
        ]:
            setup, install = cmd_pair
            inst_cmd = install or setup
            try:
                if setup[0] == "apt-get":
                    subprocess.run(setup, check=True, capture_output=True, timeout=120)
                subprocess.run(inst_cmd, check=True, capture_output=True, timeout=300)
                p = find_rclone()
                if p:
                    ok(f"rclone installed via {' '.join(inst_cmd)}.")
                    return p
            except Exception:
                pass

        err("Install rclone manually: https://rclone.org/install/")
        return None

    return find_rclone()


def _install_rclone_portable_windows() -> str | None:
    """Download the official rclone Windows zip into ~/.local/bin/."""
    try:
        # Resolve the latest stable download URL.
        req = urllib.request.Request(
            "https://downloads.rclone.org/rclone-current-windows-amd64.zip",
            headers={"User-Agent": "go-rclone"})
        with urllib.request.urlopen(req, timeout=60) as r:
            tmp_zip = Path(os.environ.get("TEMP", "/tmp")) / "rclone.zip"
            with open(tmp_zip, "wb") as f:
                f.write(r.read())

        dest_dir = Path.home() / ".local" / "bin"
        dest_dir.mkdir(parents=True, exist_ok=True)

        with zipfile.ZipFile(tmp_zip) as z:
            z.extractall(dest_dir.parent)
        tmp_zip.unlink(missing_ok=True)

        # zip extracts to rclone-vX.Y.Z-windows-amd64/; flatten it.
        extracted = next(
            (p for p in dest_dir.parent.iterdir()
             if p.is_dir() and p.name.startswith("rclone-") and "windows" in p.name),
            None)
        if extracted:
            for child in extracted.iterdir():
                target = dest_dir / child.name
                if target.exists():
                    if target.is_dir():
                        shutil.rmtree(target)
                    else:
                        target.unlink()
                shutil.move(str(child), str(target))
            shutil.rmtree(extracted, ignore_errors=True)

        rc = dest_dir / ("rclone.exe" if platform.system() == "Windows" else "rclone")
        if not rc.exists():
            err("rclone binary not found after extraction.")
            return None

        # Make sure ~/.local/bin is on PATH for this process.
        os.environ["PATH"] = str(dest_dir) + os.pathsep + os.environ.get("PATH", "")
        ok(f"rclone extracted to {dest_dir}.")
        warn("Add ~/.local/bin to your PATH for future sessions.")
        return str(rc)

    except Exception as e:
        err(f"Portable install failed: {e}")
        return None


def ensure_rclone() -> str:
    """Self-heal: return rclone path, installing if needed."""
    p = find_rclone()
    if p:
        return p
    installed = install_rclone()
    if not installed:
        err("Could not install rclone. Aborting.")
        sys.exit(1)
    return installed


# ── rclone config / remote management ────────────────────────────────────────

def _run(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, capture_output=True, text=True, check=check)


def _rclone(cmd: list[str], check: bool = False) -> subprocess.CompletedProcess:
    return _run(["rclone", *cmd], check=check)


def list_remotes() -> list[str]:
    r = _rclone(["listremotes"])
    if r.returncode != 0:
        return []
    return [line.strip().rstrip(":") for line in r.stdout.splitlines() if line.strip()]


def has_remote(name: str) -> bool:
    return name in list_remotes()


def _authorize_google_drive() -> bool:
    """Run `rclone authorize` to get a Google Drive token, then create the
    'gdrive' remote non-interactively."""
    print()
    print("    Launching rclone's Google Drive authorization…")
    print("    A browser window will open. Sign in, allow access, and copy")
    print("    the token back into this window.")
    print()

    # `rclone authorize "drive"` blocks on user pasting the token from a
    # browser session. We don't need to wrap it — run it directly so the
    # browser popup works as designed.
    auth = subprocess.run(
        ["rclone", "authorize", "drive"], check=False)
    if auth.returncode != 0:
        err("Authorization step failed.")
        return False
    return True


def _config_create(name: str, remote_type: str = "drive") -> None:
    """Create a remote non-interactively using a temp config file."""
    _rclone(["config", "create", name, remote_type], check=True)
    ok(f"Created remote '{name}'.")


def ensure_remote(name: str = REMOTE_NAME) -> str:
    """Self-heal: return remote name, creating it (with auth) if needed."""
    if has_remote(name):
        return name

    print()
    warn(f"Remote '{name}:' is not configured.")
    print("    Let's set it up (Google Drive).")
    print()

    if not _authorize_google_drive():
        sys.exit(1)
    _config_create(name, "drive")

    if not has_remote(name):
        err(f"Remote '{name}' still not visible. Run `rclone config` to inspect.")
        sys.exit(1)

    ok(f"Remote '{name}:' is ready.")
    return name


# ── Subcommands ──────────────────────────────────────────────────────────────

def cmd_setup() -> None:
    """Install rclone + create the default remote. Idempotent."""
    print()
    print("[ Setup ]")
    ensure_rclone()
    ensure_remote()
    ok("go-rclone is ready.")


def cmd_config() -> None:
    """Hand off to `rclone config` for full remote management."""
    ensure_rclone()
    print()
    print("[ rclone config — full remote management ]")
    print("    Use 'n' for new, 'd' to delete, etc.")
    print()
    subprocess.run(["rclone", "config"])


def cmd_list(path: str) -> None:
    rc = ensure_rclone()
    ensure_remote()
    target = path if path.startswith(f"{REMOTE_NAME}:") else f"{REMOTE_NAME}:{path}"
    print()
    print(f"[ List: {target} ]")
    # Don't auto-recurse on root: a single `rclone ls` call lists the top level.
    # If the user wants a tree, they can `rclone tree` themselves via `config`.
    subprocess.run([rc, "ls", target])


def cmd_browse() -> None:
    """Walk the remote interactively, let user pick a path, then act on it."""
    rc = ensure_rclone()
    ensure_remote()
    cwd_remote = ""  # relative to remote root
    while True:
        prefix = f"{REMOTE_NAME}:{cwd_remote}".rstrip(":")
        print()
        print(f"[ Browse: {prefix or REMOTE_NAME + ':/'} ]")
        r = _rclone(["lsjson", f"{REMOTE_NAME}:{cwd_remote}".rstrip(":"), "--dirs-only"])
        dirs = []
        for line in r.stdout.splitlines():
            try:
                import json
                obj = json.loads(line)
                if obj.get("IsDir"):
                    dirs.append(obj["Path"])
            except Exception:
                continue
        if not dirs:
            info("(no subdirectories)")
        for i, d in enumerate(dirs, 1):
            print(f"  {i}. {d}")
        print(f"  [.] Pick this path: {prefix or REMOTE_NAME + ':/'}")
        print(f"  [..] Go up")
        print(f"  [0] Cancel")
        val = input("  Choose: ").strip()
        if val == "0":
            return
        if val == "..":
            if "/" in cwd_remote:
                cwd_remote = cwd_remote.rsplit("/", 1)[0]
            else:
                cwd_remote = ""
            continue
        if val == ".":
            print(f"  Selected: {prefix or REMOTE_NAME + ':/'}")
            _action_menu_on_remote(prefix or f"{REMOTE_NAME}:/")
            return
        if val.isdigit() and 1 <= int(val) <= len(dirs):
            cwd_remote = (cwd_remote + "/" + dirs[int(val) - 1]).lstrip("/")
            continue
        warn("Invalid.")


def _action_menu_on_remote(remote_path: str) -> None:
    """After `browse` lands on a remote path, ask what to do with it."""
    print()
    print(f"  [D] Download to <local path>")
    print(f"  [S] Sync local into this remote path (mirror, destructive)")
    print(f"  [0] Cancel")
    sub = input("  Pick [d/s]: ").strip().lower()
    if sub == "d":
        dest = input("  Local destination path: ").strip()
        if not dest:
            warn("Cancelled.")
            return
        cmd_download(remote_path, dest)
    elif sub == "s":
        src = input("  Local source path: ").strip()
        if not src:
            warn("Cancelled.")
            return
        if not _confirm(f"  rclone sync will MIRROR {src} -> {remote_path} (deletes remote extras). Continue?", default_yes=False):
            warn("Cancelled.")
            return
        cmd_sync(src, remote_path)


def _confirm(question: str, default_yes: bool = False) -> bool:
    suffix = " [Y/n]: " if default_yes else " [y/N]: "
    val = input(question + suffix).strip().lower()
    if not val:
        return default_yes
    return val in ("y", "yes")


def _normalize_remote_arg(arg: str) -> str:
    """Accept 'gdrive', 'gdrive:', 'gdrive:foo/bar', or just 'foo/bar'."""
    if not arg:
        return f"{REMOTE_NAME}:"
    if arg.startswith(f"{REMOTE_NAME}:"):
        return arg
    if ":" in arg and not os.path.exists(arg):
        # Looks like a remote spec for a different remote name — pass through.
        return arg
    return f"{REMOTE_NAME}:{arg.lstrip('/')}"


def _rclone_progress(cmd: list[str]) -> int:
    """Run an rclone command, showing live progress on stderr but returning
    the exit code. stdout is captured and discarded (rclone's --progress
    writes to stderr; stdout is empty for transfer commands)."""
    proc = subprocess.run(cmd)
    return proc.returncode


def cmd_upload(src: str, dest_remote: str | None) -> None:
    rc = ensure_rclone()
    ensure_remote()

    src_p = Path(src).expanduser()
    if not src_p.exists():
        err(f"Local path not found: {src_p}")
        sys.exit(1)

    if not dest_remote:
        dest_remote = f"{REMOTE_NAME}:"
    dest = _normalize_remote_arg(dest_remote)

    print()
    print(f"[ Upload ]")
    info(f"src: {src_p}")
    info(f"dst: {dest}")
    rc2 = _rclone_progress([rc, "copy", str(src_p), dest, "--progress", "--stats", "5s"])
    print()
    if rc2 != 0:
        err("Upload failed.")
        sys.exit(1)
    ok("Upload complete.")


def cmd_download(src_remote: str | None, dest: str) -> None:
    rc = ensure_rclone()
    ensure_remote()

    if not src_remote:
        src_remote = f"{REMOTE_NAME}:"
    src = _normalize_remote_arg(src_remote)

    dest_p = Path(dest).expanduser()
    if dest_p.exists() and dest_p.is_dir():
        # Destination is a directory — keep the source leaf name.
        leaf = src.split(":", 1)[-1].rstrip("/").rsplit("/", 1)[-1] or "download"
        dest_p = dest_p / leaf
    dest_p.parent.mkdir(parents=True, exist_ok=True)

    print()
    print(f"[ Download ]")
    info(f"src: {src}")
    info(f"dst: {dest_p}")
    rc2 = _rclone_progress([rc, "copy", src, str(dest_p), "--progress", "--stats", "5s"])
    print()
    if rc2 != 0:
        err("Download failed.")
        sys.exit(1)
    ok("Download complete.")


def cmd_sync(src: str, dest_remote: str | None) -> None:
    rc = ensure_rclone()
    ensure_remote()

    src_p = Path(src).expanduser()
    if not src_p.exists():
        err(f"Local path not found: {src_p}")
        sys.exit(1)

    if not dest_remote:
        dest_remote = f"{REMOTE_NAME}:"
    dest = _normalize_remote_arg(dest_remote)

    print()
    warn("rclone sync is MIRRORING: files missing from source will be DELETED on destination.")
    if not _confirm(f"  Proceed: {src_p}  =>  {dest}", default_yes=False):
        warn("Cancelled.")
        return

    rc2 = _rclone_progress([rc, "sync", str(src_p), dest, "--progress", "--stats", "5s"])
    print()
    if rc2 != 0:
        err("Sync failed.")
        sys.exit(1)
    ok("Sync complete.")


# ── Interactive menu ─────────────────────────────────────────────────────────

MENU = [
    ("Upload (local -> Drive)",        "upload",   "Copy a local file/folder to Google Drive"),
    ("Download (Drive -> local)",      "download", "Copy a Google Drive path down to your machine"),
    ("Sync (mirror local -> Drive)",   "sync",     "Destructive: mirror local folder onto Drive"),
    ("List Drive contents",            "list",     "Show files/folders under a Drive path"),
    ("Browse Drive (pick a path)",     "browse",   "Walk the remote, then upload/download/sync"),
    ("Setup (install + auth)",         "setup",    "One-time: install rclone and add the remote"),
    ("Config (full remote manager)",   "config",   "Run `rclone config` for advanced settings"),
]


def show_menu(choices: list[tuple[str, str, str]], header: str) -> int:
    print(header)
    print("-" * 50)
    for i, (label, _, _) in enumerate(choices, 1):
        print(f"  {i}. {label}")
    print(f"  0. Exit")
    print()
    while True:
        val = input("Select: ").strip()
        if val == "0":
            sys.exit(0)
        try:
            n = int(val)
            if 1 <= n <= len(choices):
                return n
        except ValueError:
            pass
        warn("Invalid.")


def interactive() -> None:
    print()
    print("[ go-rclone ]")
    print(f"    default remote: {REMOTE_NAME}:")
    print()

    choice = show_menu(MENU, "[ What do you want to do? ]")
    _, action, _ = MENU[choice - 1]

    if action == "setup":
        cmd_setup()
        return
    if action == "config":
        cmd_config()
        return
    if action == "list":
        path = input(f"  Remote path [/{REMOTE_NAME}/]: ").strip().lstrip("/")
        cmd_list(path or "/")
        return
    if action == "browse":
        cmd_browse()
        return
    if action == "upload":
        src = input("  Local source path: ").strip()
        if not src:
            warn("Cancelled.")
            return
        dest = input(f"  Remote destination [{REMOTE_NAME}:/]: ").strip()
        cmd_upload(src, dest or None)
        return
    if action == "download":
        src = input(f"  Remote source [{REMOTE_NAME}:/]: ").strip()
        dest = input("  Local destination path: ").strip()
        if not dest:
            warn("Cancelled.")
            return
        cmd_download(src or None, dest)
        return
    if action == "sync":
        src = input("  Local source path: ").strip()
        if not src:
            warn("Cancelled.")
            return
        dest = input(f"  Remote destination [{REMOTE_NAME}:/]: ").strip()
        cmd_sync(src, dest or None)
        return


# ── Main ─────────────────────────────────────────────────────────────────────

def main() -> None:
    global REMOTE_NAME

    parser = argparse.ArgumentParser(
        description="Google Drive upload/download/sync via rclone.")
    sub = parser.add_subparsers(dest="cmd")

    p_setup = sub.add_parser("setup", help="install rclone and add the default remote")
    p_config = sub.add_parser("config", help="run `rclone config`")
    p_list = sub.add_parser("list", help="list Drive contents")
    p_list.add_argument("path", nargs="?", default="/",
                        help=f"remote path (default: /, under remote '{REMOTE_NAME}')")
    p_browse = sub.add_parser("browse", help="interactively walk the remote")
    p_up = sub.add_parser("upload", help="copy local -> Drive")
    p_up.add_argument("src", help="local file or directory")
    p_up.add_argument("dest", nargs="?", default=None,
                      help=f"remote destination (default: {REMOTE_NAME}:/)")
    p_dl = sub.add_parser("download", help="copy Drive -> local")
    p_dl.add_argument("src", nargs="?", default=None,
                      help=f"remote source (default: {REMOTE_NAME}:/)")
    p_dl.add_argument("dest", help="local destination path")
    p_sync = sub.add_parser("sync", help="mirror local -> Drive (destructive)")
    p_sync.add_argument("src", help="local directory")
    p_sync.add_argument("dest", nargs="?", default=None,
                        help=f"remote destination (default: {REMOTE_NAME}:/)")

    parser.add_argument("--remote", default=REMOTE_NAME,
                        help=f"rclone remote name to use (default: {REMOTE_NAME})")

    args = parser.parse_args()

    # Allow overriding the default remote name globally.
    if args.remote:
        REMOTE_NAME = args.remote

    if not args.cmd:
        interactive()
        return

    if args.cmd == "setup":
        cmd_setup()
    elif args.cmd == "config":
        cmd_config()
    elif args.cmd == "list":
        cmd_list(args.path)
    elif args.cmd == "browse":
        cmd_browse()
    elif args.cmd == "upload":
        cmd_upload(args.src, args.dest)
    elif args.cmd == "download":
        cmd_download(args.src, args.dest)
    elif args.cmd == "sync":
        cmd_sync(args.src, args.dest)


if __name__ == "__main__":
    main()
