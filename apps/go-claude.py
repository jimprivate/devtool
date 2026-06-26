#!/usr/bin/env python3
r"""
claude-go: cross-platform provider switcher for Claude Code.

Single-file driver. Works on Windows (PowerShell / cmd), macOS, Linux.

Usage (no arguments needed — just run, then pick from menu):
    claude-go                          # pick provider/model from menu, then launch claude
    python claude-go.py                # same, if you don't have the launcher installed

Optional subcommands (all pick-from-menu too — these just preset the provider):
    python claude-go.py flash          # deepseek flash + launch claude
    python claude-go.py pro            # deepseek pro
    python claude-go.py or sonnet      # openrouter sonnet
    python claude-go.py anthropic opus # anthropic opus
    python claude-go.py menu           # picker only
    python claude-go.py status         # show current env
    python claude-go.py cost 100 50    # cost from tokens
    python claude-go.py key deepseek sk-...  # set API key
    python claude-go.py reset          # clear provider env
    python claude-go.py balance        # show DeepSeek account balance

One-time setup (Windows PowerShell):

    # Drop a tiny launcher in a folder already on PATH so you can type
    # `claude-go` (or `go-claude`) from any directory:
    $LauncherDir = "$env:USERPROFILE\.local\bin"
    New-Item -ItemType Directory -Force -Path $LauncherDir | Out-Null
    @'
    @echo off
    py  "C:\path\to\apps\go-claude.py" %*
    '@ | Out-File -Encoding ascii "$LauncherDir\go-claude.cmd"

    # Then run `go-claude prereq` once — it puts both claude-go's and
    # claude.exe's directory on user PATH and creates ~/.claude/.
"""

import argparse
import getpass
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

# ============================================================
# Static config: providers, models, pricing
# ============================================================

ENDPOINTS = {
    "deepseek": {
        "base_url": "https://api.deepseek.com/anthropic",
        "api_key_env": "DEEPSEEK_API_KEY",
        "auth_style": "bearer",
        "models": {
            "flash": "deepseek-v4-flash",
            "pro":   "deepseek-v4-pro[1m]",
        },
        "default_haiku":     "deepseek-v4-flash",
        "default_subagent":  "deepseek-v4-flash",
        "disable_nonessential": "1",
    },
    "openrouter": {
        "base_url": "https://openrouter.ai/api/v1",
        "api_key_env": "OPENROUTER_API_KEY",
        "auth_style": "bearer",
        "models": {
            "sonnet":  "anthropic/claude-sonnet-4.5",
            "opus":    "anthropic/claude-opus-4.8",
            "haiku":   "anthropic/claude-haiku-4.5",
            "deepseek": "deepseek/deepseek-v4-pro",
            "gpt5":    "openai/gpt-5",
            "gemini":  "google/gemini-2.5-pro",
        },
        "default_haiku":     "anthropic/claude-haiku-4.5",
        "default_subagent":  "anthropic/claude-haiku-4.5",
        "disable_nonessential": "1",
    },
    "anthropic": {
        "base_url": None,  # leave unset -> SDK uses default
        "api_key_env": "ANTHROPIC_API_KEY",
        "auth_style": "api_key",
        "models": {
            "sonnet": "claude-sonnet-4.5",
            "opus":   "claude-opus-4.8",
            "haiku":  "claude-haiku-4.5",
        },
        "default_haiku":     "claude-haiku-4.5",
        "default_subagent":  "claude-haiku-4.5",
        "disable_nonessential": "0",
    },
}

PRICING = {
    "deepseek:flash":   {"input": 0.14,    "output": 0.28,    "cache_hit": 0.003625, "cache_miss": 0.14},
    "deepseek:pro":     {"input": 0.435,   "output": 0.87,    "cache_hit": 0.003625, "cache_miss": 0.435},
    "openrouter:sonnet": {"input": 3.00,   "output": 15.00,   "cache_hit": 0.30,     "cache_miss": 3.75},
    "openrouter:opus":   {"input": 15.00,  "output": 75.00,   "cache_hit": 1.50,     "cache_miss": 18.75},
    "openrouter:haiku":  {"input": 0.25,   "output": 1.25,    "cache_hit": 0.03,     "cache_miss": 0.30},
    "anthropic:sonnet": {"input": 3.00,    "output": 15.00,   "cache_hit": 0.30,     "cache_miss": 3.75},
    "anthropic:opus":   {"input": 15.00,   "output": 75.00,   "cache_hit": 1.50,     "cache_miss": 18.75},
    "anthropic:haiku":  {"input": 0.80,    "output": 4.00,    "cache_hit": 0.08,     "cache_miss": 1.00},
}

USD_TWD = 31.5
USD_CNY = 7.25

# ============================================================
# Path helpers
# ============================================================

def home() -> Path:
    return Path(os.environ.get("USERPROFILE") or str(Path.home()))

def env_file() -> Path:
    return home() / ".claude" / ".env"

def settings_file() -> Path:
    return home() / ".claude" / "settings.json"

def projects_dir() -> Path:
    return home() / ".claude" / "projects"

# ============================================================
# Terminal color helpers
# ============================================================

class C:
    RESET  = "\033[0m"
    BOLD   = "\033[1m"
    DIM    = "\033[2m"
    RED    = "\033[31m"
    GREEN  = "\033[32m"
    YELLOW = "\033[33m"
    BLUE   = "\033[34m"
    CYAN   = "\033[36m"
    GRAY   = "\033[90m"

def isatty() -> bool:
    return sys.stdout.isatty()

def color(text: str, *codes: str) -> str:
    if not isatty():
        return text
    return "".join(codes) + text + C.RESET

def info(msg: str)  -> None: print(color(msg, C.CYAN))
def ok(msg: str)    -> None: print(color(msg, C.GREEN))
def warn(msg: str)  -> None: print(color(msg, C.YELLOW))
def err(msg: str)   -> None: print(color("[ERROR] " + msg, C.RED, C.BOLD))

# ============================================================
# Provider / env logic (the engine)
# ============================================================

def get_provider_key(provider: str) -> str | None:
    """Look up API key: process env -> user env -> .env file. Never interactive."""
    ep = ENDPOINTS.get(provider)
    if not ep:
        return None
    var = ep["api_key_env"]

    v = os.environ.get(var)
    if v:
        return v

    if platform.system() == "Windows":
        try:
            import winreg
            for root in (winreg.HKEY_CURRENT_USER, winreg.HKEY_LOCAL_MACHINE):
                try:
                    with winreg.OpenKey(root, r"Environment") as k:
                        val, _ = winreg.QueryValueEx(k, var)
                        if val:
                            return val
                except FileNotFoundError:
                    continue
                except OSError:
                    continue
        except ImportError:
            pass
    else:
        for p in (home() / ".profile", home() / ".bashrc", home() / ".zshrc"):
            try:
                if p.exists():
                    for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
                        s = line.strip()
                        if s.startswith("export ") and s[len("export "):].startswith(var + "="):
                            val = s.split("=", 1)[1].strip().strip('"').strip("'")
                            if val:
                                return val
            except OSError:
                continue

    p = env_file()
    if p.exists():
        try:
            for line in p.read_text(encoding="utf-8").splitlines():
                s = line.strip()
                if s.startswith(var + "="):
                    val = s.split("=", 1)[1].strip().strip('"').strip("'")
                    if val:
                        return val
        except OSError:
            pass
    return None

def set_provider_key(provider: str, value: str) -> None:
    ep = ENDPOINTS.get(provider)
    if not ep:
        err(f"unknown provider: {provider}")
        return
    var = ep["api_key_env"]
    p = env_file()
    p.parent.mkdir(parents=True, exist_ok=True)
    lines = []
    if p.exists():
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith(var + "="):
                lines.append(line)
    lines.append(f"{var}={value}")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    os.environ[var] = value
    ok(f"saved {var} to {p}")

def reset_provider_key(provider: str) -> None:
    ep = ENDPOINTS.get(provider)
    if not ep:
        return
    var = ep["api_key_env"]
    p = env_file()
    if p.exists():
        kept = []
        for line in p.read_text(encoding="utf-8").splitlines():
            if not line.strip().startswith(var + "="):
                kept.append(line)
        if kept:
            p.write_text("\n".join(kept) + "\n", encoding="utf-8")
        else:
            p.unlink()
    os.environ.pop(var, None)


def _prompt_api_key(provider: str) -> bool:
    """Interactively prompt for a missing provider API key, save it, return True on success.

    Self-heal: if the user just picked a provider with no key, ask for it now
    instead of dumping them back to a shell to run a subcommand manually.
    Returns False if user aborts or stdin is not a TTY.
    """
    ep = ENDPOINTS.get(provider)
    if not ep:
        return False
    var = ep["api_key_env"]

    if not sys.stdin.isatty():
        err(f"{var} not set and stdin is not a TTY — cannot prompt")
        err(f"set it with:  go-claude key {provider} <your-key>")
        return False

    print()
    warn(f"{var} is not set for provider '{provider}'.")
    info(f"  Get a key at: {_key_url(provider)}")
    print()

    while True:
        try:
            value = getpass.getpass(f"  Paste your {provider} API key (input hidden): ").strip()
        except (KeyboardInterrupt, EOFError):
            print()
            warn("Aborted.")
            return False
        if value:
            set_provider_key(provider, value)
            return True
        print("    Empty. ", end="")
        try:
            retry = input("Try again? [Y/n]: ").strip().lower()
        except (KeyboardInterrupt, EOFError):
            print()
            warn("Aborted.")
            return False
        if retry == "n":
            print()
            warn(f"Aborted. Run later:  go-claude key {provider} <your-key>")
            return False


def _key_url(provider: str) -> str:
    return {
        "deepseek":   "https://platform.deepseek.com/api_keys",
        "openrouter": "https://openrouter.ai/settings/keys",
        "anthropic":  "https://console.anthropic.com/settings/keys",
    }.get(provider, "your provider's console")

def set_model(provider: str, model: str) -> bool:
    ep = ENDPOINTS.get(provider)
    if not ep:
        err(f"unknown provider: {provider}. Known: {', '.join(ENDPOINTS)}")
        return False

    api_key = get_provider_key(provider)
    if not api_key:
        if not _prompt_api_key(provider):
            return False
        api_key = get_provider_key(provider)
        if not api_key:
            return False

    model_id = ep["models"].get(model)
    if not model_id:
        err(f"unknown model '{model}' for provider '{provider}'. Known: {', '.join(ep['models'])}")
        return False

    # Process env
    if ep["base_url"] is None:
        os.environ.pop("ANTHROPIC_BASE_URL", None)
    else:
        os.environ["ANTHROPIC_BASE_URL"] = ep["base_url"]

    if ep["auth_style"] == "bearer":
        os.environ["ANTHROPIC_AUTH_TOKEN"] = api_key
        os.environ.pop("ANTHROPIC_API_KEY", None)
    else:
        os.environ["ANTHROPIC_API_KEY"] = api_key
        os.environ.pop("ANTHROPIC_AUTH_TOKEN", None)

    os.environ["ANTHROPIC_MODEL"]                    = model_id
    os.environ["ANTHROPIC_DEFAULT_OPUS_MODEL"]      = model_id
    os.environ["ANTHROPIC_DEFAULT_SONNET_MODEL"]    = model_id
    os.environ["ANTHROPIC_DEFAULT_HAIKU_MODEL"]     = ep["default_haiku"]
    os.environ["CLAUDE_CODE_SUBAGENT_MODEL"]        = ep["default_subagent"]
    os.environ["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = ep["disable_nonessential"]

    # Mirror to settings.json (Claude Code v2.1.x reads this on Windows).
    # PowerShell's Set-Content writes UTF-8 with BOM, so read both forms.
    sp = settings_file()
    if sp.exists():
        try:
            raw = sp.read_bytes()
            text = raw.decode("utf-8-sig") or raw.decode("utf-8", errors="ignore")
            cfg = json.loads(text or "{}")
            cfg.setdefault("env", {})
            env = cfg["env"]
            if ep["base_url"] is None:
                env.pop("ANTHROPIC_BASE_URL", None)
            else:
                env["ANTHROPIC_BASE_URL"] = ep["base_url"]
            if ep["auth_style"] == "bearer":
                env["ANTHROPIC_AUTH_TOKEN"] = api_key
                env.pop("ANTHROPIC_API_KEY", None)
            else:
                env["ANTHROPIC_API_KEY"] = api_key
                env.pop("ANTHROPIC_AUTH_TOKEN", None)
            env["ANTHROPIC_MODEL"]                    = model_id
            env["ANTHROPIC_DEFAULT_OPUS_MODEL"]      = model_id
            env["ANTHROPIC_DEFAULT_SONNET_MODEL"]    = model_id
            env["ANTHROPIC_DEFAULT_HAIKU_MODEL"]     = ep["default_haiku"]
            env["CLAUDE_CODE_SUBAGENT_MODEL"]        = ep["default_subagent"]
            env["CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"] = ep["disable_nonessential"]
            sp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
        except (OSError, json.JSONDecodeError) as e:
            warn(f"could not sync settings.json: {e}")

    print()
    ok(f"[PID {os.getpid()}] switched to {provider} / {model}")
    info(f"     model    : {model_id}")
    info(f"     base     : {ep['base_url'] or '(Anthropic default)'}")
    info(f"     auth     : {ep['auth_style']} via {ep['api_key_env']}")
    print()
    return True

def reset_env() -> None:
    for k in (
        "ANTHROPIC_BASE_URL",
        "ANTHROPIC_AUTH_TOKEN",
        "ANTHROPIC_API_KEY",
        "ANTHROPIC_MODEL",
        "ANTHROPIC_DEFAULT_OPUS_MODEL",
        "ANTHROPIC_DEFAULT_SONNET_MODEL",
        "ANTHROPIC_DEFAULT_HAIKU_MODEL",
        "CLAUDE_CODE_SUBAGENT_MODEL",
        "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC",
    ):
        os.environ.pop(k, None)
    print()
    warn(f"[PID {os.getpid()}] cleared all provider env vars. Back to Anthropic default.")
    print()

def show_status() -> None:
    print()
    info(f"[PID {os.getpid()}] Claude Code current configuration:")

    cur = "<none>"
    base = os.environ.get("ANTHROPIC_BASE_URL")
    if os.environ.get("ANTHROPIC_AUTH_TOKEN") and base:
        for k, ep in ENDPOINTS.items():
            if ep["base_url"] == base:
                cur = k
                break
        if cur == "<none>":
            cur = f"unknown ({base})"
    elif os.environ.get("ANTHROPIC_API_KEY"):
        cur = "anthropic"
    else:
        cur = "<not configured>"
    info(f"Provider : {cur}")

    def show(name, val, mask=False):
        if not val:
            disp = "<not set>"
        elif mask:
            disp = "***set***"
        else:
            disp = val
        print(f"  {name:<45} {disp}")

    print()
    show("ANTHROPIC_BASE_URL",                       os.environ.get("ANTHROPIC_BASE_URL"))
    show("ANTHROPIC_AUTH_TOKEN",                     os.environ.get("ANTHROPIC_AUTH_TOKEN"), mask=True)
    show("ANTHROPIC_API_KEY",                        os.environ.get("ANTHROPIC_API_KEY"), mask=True)
    show("ANTHROPIC_MODEL",                          os.environ.get("ANTHROPIC_MODEL"))
    show("ANTHROPIC_DEFAULT_HAIKU_MODEL",            os.environ.get("ANTHROPIC_DEFAULT_HAIKU_MODEL"))
    show("CLAUDE_CODE_SUBAGENT_MODEL",               os.environ.get("CLAUDE_CODE_SUBAGENT_MODEL"))
    show("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC", os.environ.get("CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC"))

    print()
    warn("Provider keys (env / .env):")
    for k, ep in ENDPOINTS.items():
        v = get_provider_key(k)
        shown = "***set***" if v else "<not set>"
        print(f"  {ep['api_key_env']:<30} {shown}  [{k}]")
    print()
    show_balance("deepseek")

# ============================================================
# Balance
# ============================================================

def fetch_balance(provider: str = "deepseek") -> dict | None:
    """Call GET /user/balance for the official DeepSeek API. Returns parsed JSON or None."""
    api_key = get_provider_key(provider)
    if not api_key:
        return None
    if provider != "deepseek":
        return None
    try:
        import urllib.request
        req = urllib.request.Request(
            "https://api.deepseek.com/user/balance",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Accept": "application/json",
            },
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except Exception as e:
        warn(f"could not fetch balance: {e}")
        return None

def show_balance(provider: str = "deepseek") -> None:
    result = fetch_balance(provider)
    print()
    if result is None:
        warn(f"could not fetch balance for {provider} (no key or network error)")
        return
    info(f"balance for {provider}:")
    avail = "yes" if result.get("is_available") else color("NO", C.RED)
    print(f"  available : {avail}")
    for bi in result.get("balance_infos", []):
        sym = bi.get("currency", "?")
        total = float(bi.get("total_balance", 0))
        granted = float(bi.get("granted_balance", 0))
        topped = float(bi.get("topped_up_balance", 0))
        note = "  (CNY, ¥)" if sym == "CNY" else ""
        print(f"  {sym:<5} total={total:.4f}  (topped={topped:.4f}  granted={granted:.4f}){note}")
    print()

# ============================================================
# Cost calculation
# ============================================================

def _newest_session_log() -> str | None:
    """Find the most recently written JSONL session log under ~/.claude/projects/."""
    base = projects_dir()
    if not base.exists():
        return None
    best: tuple[float, str] = (0.0, "")
    try:
        for p in base.rglob("*.jsonl"):
            try:
                mtime = p.stat().st_mtime
                if mtime > best[0]:
                    best = (mtime, str(p))
            except OSError:
                continue
    except OSError:
        return None
    return best[1] or None

def _resolve_pricing(provider: str | None, model: str | None) -> tuple[str, str, dict]:
    if not provider or not model:
        mid = os.environ.get("ANTHROPIC_MODEL") or ""
        for p, ep in ENDPOINTS.items():
            for alias, mid2 in ep["models"].items():
                if mid2 == mid:
                    if not provider:
                        provider = p
                    if not model:
                        model = alias
                    break
            if provider and model:
                break
    if not provider:
        provider = "deepseek"
    if not model:
        model = "flash"
    key = f"{provider}:{model}"
    p = PRICING.get(key)
    if not p:
        warn(f"no pricing for {key}; using deepseek:flash as fallback")
        p = PRICING["deepseek:flash"]
    return provider, model, p

def cost(inp: int, out: int, cr: int, cw: int,
         provider: str | None = None, model: str | None = None,
         no_twd: bool = False, no_cny: bool = False,
         cache_hits: int = 0) -> None:
    provider, model, p = _resolve_pricing(provider, model)
    ic = (inp  / 1_000_000) * p["input"]
    oc = (out / 1_000_000) * p["output"]
    hc = (cr  / 1_000_000) * p["cache_hit"]
    mc = (cw  / 1_000_000) * p["cache_miss"]
    total = ic + oc + hc + mc
    print()
    info(f"provider/model : {provider} / {model}")
    info(f"resolved       : {os.environ.get('ANTHROPIC_MODEL', '')}")
    print(f"tokens: in={inp:,}  out={out:,}  cache_read={cr:,}  cache_write={cw:,}")
    print(f"cache  : hits={cache_hits:,}  read={cr:,}  write={cw:,}")
    warn("cost breakdown:")
    print(f"  input         {ic:.6f}")
    print(f"  output        {oc:.6f}")
    print(f"  cache read    {hc:.6f}")
    print(f"  cache write   {mc:.6f}")
    print()
    ok(f"TOTAL:  $ {total:.6f}")
    if not no_twd:
        ok(f"TOTAL:  NT$ {total * USD_TWD:.4f}  (USD/TWD = {USD_TWD})")
    if not no_cny:
        ok(f"TOTAL:  ¥ {total * USD_CNY:.4f}  (USD/CNY = {USD_CNY})")
    print()

def cost_from_log(path: str | None = None, cwd: str | None = None, no_twd: bool = False, no_cny: bool = False) -> None:
    target = path
    if not target:
        base = projects_dir()
        if not base.exists():
            warn("no ~/.claude/projects")
            return
        # match: project dirs encode cwd path with \\ -> --
        if cwd:
            tag = cwd.replace("\\", "--").replace("/", "--")
            cand = [d for d in base.iterdir() if d.is_dir() and tag in d.name]
            if cand:
                files = sorted((f for f in cand[0].glob("*.jsonl")), key=lambda f: f.stat().st_mtime, reverse=True)
                if files:
                    target = str(files[0])
    if not target or not Path(target).exists():
        warn("no session log found")
        return
    in_t = out_t = cr_t = cw_t = 0
    cache_hits = 0
    n = 0
    for line in Path(target).read_text(encoding="utf-8", errors="ignore").splitlines():
        s = line.strip()
        if not s.startswith("{"):
            continue
        try:
            obj = json.loads(s)
        except json.JSONDecodeError:
            continue
        if obj.get("type") != "assistant":
            continue
        u = obj.get("message", {}).get("usage") or {}
        in_t  += int(u.get("input_tokens")  or 0)
        out_t += int(u.get("output_tokens") or 0)
        cr_t  += int(u.get("cache_read_input_tokens")     or 0)
        cw_t  += int(u.get("cache_creation_input_tokens") or 0)
        if int(u.get("cache_read_input_tokens") or 0) > 0:
            cache_hits += 1
        n += 1
    print()
    warn(f"session : {Path(target).stem}  ({n} assistant turns)")
    warn(f"file    : {target}")
    cost(in_t, out_t, cr_t, cw_t,
         no_twd=no_twd, no_cny=no_cny,
         cache_hits=cache_hits)

def auto_cost_from_log() -> None:
    """Auto-display token & cost summary from the newest session log.

    Called automatically after `claude` exits.  Silently skips when
    there is no session log (first run / no assistant turns).
    """
    target = _newest_session_log()
    if not target:
        return
    # Only show if the log was written within the last 30 seconds
    # (i.e. by the session that just ended, not some old one).
    try:
        mtime = Path(target).stat().st_mtime
        now = __import__("time").time()
        if now - mtime > 30:
            return
    except OSError:
        return
    cost_from_log(path=target)

# ============================================================
# Interactive menu
# ============================================================

def menu_pick() -> tuple[str, str] | None:
    print()
    info("========================================")
    info("  Claude Code - Provider Picker")
    info("========================================")
    choices: dict[str, str] = {}
    for i, (k, ep) in enumerate(ENDPOINTS.items(), 1):
        have = bool(get_provider_key(k))
        mark = "[key: OK]" if have else "[no key]"
        print(color(f"  [{i}] {k} {mark}", C.GREEN if have else C.GRAY))
        choices[str(i)] = k
    print(color("  [R] Reset to Anthropic default", C.YELLOW))
    print(color("  [Q] Quit", C.GRAY))
    info("========================================")
    pick = input("Pick provider: ").strip().upper()
    if pick == "Q":
        return None
    if pick == "R":
        return ("__reset__", "")
    if pick not in choices:
        err("invalid")
        return None
    provider = choices[pick]
    aliases = list(ENDPOINTS[provider]["models"].keys())
    print()
    info(f"Models for {provider}:")
    for i, a in enumerate(aliases, 1):
        print(f"  [{i}] {a:<10} -> {ENDPOINTS[provider]['models'][a]}")
    mpick = input(f"Pick model [1-{len(aliases)}]: ").strip()
    if not (mpick.isdigit() and 1 <= int(mpick) <= len(aliases)):
        err("invalid")
        return None
    return provider, aliases[int(mpick) - 1]

def _list_siblings() -> list[Path]:
    """All claude-go_*.py in the same folder as this script, newest first by mtime."""
    here = Path(__file__).resolve().parent
    out: list[Path] = []
    try:
        for p in here.glob("claude-go_*.py"):
            if p.name == Path(__file__).name:
                continue
            out.append(p)
    except OSError:
        return []
    out.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    return out

def _print_siblings(current: Path | None = None) -> None:
    sibs = _list_siblings()
    if not sibs:
        return
    print()
    info(f"Sibling versions in {current.parent if current else Path.cwd()}:")
    for p in sibs:
        marker = "  <- newest" if p == sibs[0] else ""
        print(f"  {p.name}{marker}")

def _launch_claude(extra_argv: list[str], no_cost: bool = False) -> int:
    """Run the `claude` CLI with the env we just set; fall back gracefully if missing."""
    here = Path(__file__).resolve().parent
    sysname = platform.system()

    if sysname == "Windows":
        launcher = here / "claude-go.cmd"
        try:
            launcher.write_text(
                "@echo off\r\n"
                f'python "{here / Path(__file__).name}" %*\r\n',
                encoding="ascii",
            )
        except OSError as e:
            warn(f"could not refresh {launcher}: {e}")

    claude_bin = find_claude()
    if not claude_bin:
        warn("claude CLI not found.")
        warn("install: https://claude.com/download   (or: npm i -g @anthropic-ai/claude-code)")
        warn("if you just installed it, REOPEN your PowerShell / cmd and run go-claude again.")
        return 2
    if platform.system() == "Windows":
        user = _user_path_windows()
        claude_dir = str(Path(claude_bin).resolve().parent)
        if claude_dir.lower() not in {p.lower() for p in user.split(";") if p}:
            if ensure_dir_on_path(claude_dir, label="claude CLI"):
                warn("REOPEN your PowerShell / cmd so `claude` resolves from PATH.")

    cmd = [claude_bin, *extra_argv]
    _augment_path_from_registry()
    print()
    info(f"$ {' '.join(cmd)}")
    print()
    try:
        rc = subprocess.call(cmd)
    except FileNotFoundError:
        err(f"failed to exec: {cmd[0]}")
        return 127
    except KeyboardInterrupt:
        print()
        return 130

    # Show cost summary after Claude exits (unless opted out)
    if not no_cost and not os.environ.get("CLAUDE_GO_NO_COST"):
        auto_cost_from_log()

    return rc

def run_claude(provider: str | None, model: str | None, extra_argv: list[str], no_cost: bool = False) -> int:
    """Menu / preset / reset → set model (if any) → launch claude."""
    # Merge CLAUDE_GO_NO_COST env var
    if os.environ.get("CLAUDE_GO_NO_COST"):
        no_cost = True

    if provider == "__menu__":
        pick = menu_pick()
        if pick is None:
            return 0
        provider, model = pick

    if provider == "__reset__":
        reset_env()
        return 0

    if not provider or not model:
        err("internal: missing provider/model in run_claude()")
        return 1

    if not set_model(provider, model):
        return 1

    return _launch_claude(extra_argv, no_cost=no_cost)

# ============================================================
# Run claude
# ============================================================

def latest_log_mtime() -> float:
    base = projects_dir()
    if not base.exists():
        return 0.0
    best = 0.0
    for f in base.rglob("*.jsonl"):
        try:
            t = f.stat().st_mtime
            if t > best:
                best = t
        except OSError:
            continue
    return best

def find_claude() -> str | None:
    r"""Locate the `claude` binary.

    Order:
      1. shutil.which("claude") — uses current process PATH + PATHEXT.
      2. Well-known install dirs Claude Code's installer / npm shim into,
         even when not yet on PATH (fresh PowerShell after install).
    Returns the full path string, or None if nothing was found.
    """
    found = shutil.which("claude")
    if found:
        return found

    if platform.system() != "Windows":
        candidates = [
            home() / ".local" / "bin" / "claude",
            Path("/usr/local/bin/claude"),
            Path("/opt/homebrew/bin/claude"),
        ]
    else:
        candidates = [
            home() / ".local" / "bin" / "claude.exe",
            home() / "AppData" / "Roaming" / "npm" / "claude.cmd",
            home() / "AppData" / "Roaming" / "npm" / "claude.exe",
            home() / "AppData" / "Local" / "Programs" / "claude" / "claude.exe",
            Path("C:/Program Files/claude/claude.exe"),
        ]
    for c in candidates:
        try:
            if c.is_file():
                return str(c)
        except OSError:
            continue
    return None

def ensure_skeleton() -> None:
    """Create ~/.claude/ and a minimal settings.json if missing.

    Claude Code reads this on Windows, so we keep it sane even when
    claude-go is the only thing setting ANTHROPIC_* env vars.
    """
    cd = home() / ".claude"
    cd.mkdir(parents=True, exist_ok=True)
    sp = cd / "settings.json"
    if not sp.exists():
        sp.write_text("{}\n", encoding="utf-8")
        ok(f"created {sp}")
    else:
        ok(f"{sp} already exists")

def check_claude_cli() -> bool:
    r"""Verify `claude` resolves. If it does but its directory is not yet on
    the persistent user PATH, add it (so a future shell picks it up).
    Returns True if `claude` is callable from this process.
    """
    c = find_claude()
    if not c:
        warn("claude CLI not found")
        warn("install: https://claude.com/download   (or: npm i -g @anthropic-ai/claude-code)")
        return False
    ok(f"claude CLI: {c}")
    claude_dir = str(Path(c).resolve().parent)
    sysname = platform.system()
    if sysname == "Windows":
        user = _user_path_windows()
        if claude_dir.lower() not in {p.lower() for p in user.split(";") if p}:
            if ensure_dir_on_path(claude_dir, label="claude CLI"):
                warn("Claude CLI directory is now on user PATH; reopen your shell to use it from `claude`.")
    else:
        proc = os.environ.get("PATH", "")
        if claude_dir not in proc.split(os.pathsep):
            ensure_dir_on_path(claude_dir, label="claude CLI")
    return True

# ---------- PATH self-install (one-time, idempotent) ----------

def _user_path_windows() -> str:
    r"""Current user PATH from HKCU\Environment (the source of truth on Windows)."""
    try:
        import winreg
        with winreg.OpenKey(winreg.HKEY_CURRENT_USER, r"Environment") as k:
            val, _ = winreg.QueryValueEx(k, "PATH")
            return val or ""
    except (OSError, ImportError):
        return os.environ.get("PATH", "")

def _augment_path_from_registry() -> None:
    r"""Merge HKCU\Environment\PATH into the current process PATH on Windows.

    A fresh PowerShell snapshots PATH at startup. Anything added to user
    PATH after that is invisible to the process and any child it spawns
    (e.g. `claude` shelling out to git/node). Reading the registry and
    prepending the missing entries fixes that for this run only — the user
    still needs to reopen the shell for it to be permanent.
    """
    if platform.system() != "Windows":
        return
    user_path = _user_path_windows()
    if not user_path:
        return
    proc_parts = {p.lower() for p in os.environ.get("PATH", "").split(os.pathsep) if p}
    extras = [p for p in user_path.split(";") if p and p.lower() not in proc_parts]
    if not extras:
        return
    os.environ["PATH"] = os.pathsep.join(extras + [os.environ.get("PATH", "")])
    info(f"merged {len(extras)} PATH entr(y/ies) from HKCU\\Environment for this run")

def _set_user_path_windows(extra: str) -> bool:
    r"""Append `extra` to HKCU\Environment\PATH. Returns True if changed."""
    try:
        import winreg
        cur = _user_path_windows()
        parts = [p for p in cur.split(";") if p]
        if any(p.lower() == extra.lower() for p in parts):
            return False
        new = (";".join(parts + [extra])).rstrip(";")
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER, r"Environment",
            0, winreg.KEY_SET_VALUE,
        ) as k:
            winreg.SetValueEx(k, "PATH", 0, winreg.REG_EXPAND_SZ, new)
        try:
            import ctypes
            ctypes.windll.user32.SendMessageW(0xFFFF, 0x001A, 0, "Environment")
        except OSError:
            pass
        return True
    except OSError as e:
        warn(f"could not update user PATH: {e}")
        return False

def ensure_dir_on_path(directory: str, label: str = "directory") -> bool:
    r"""Persistently add `directory` to the user's PATH if not already there.

    Cross-platform. Idempotent. Returns True if PATH was modified.
    `label` is used in the user-facing message ("<label> directory").
    """
    if not directory:
        return False
    if platform.system() == "Windows":
        proc = os.environ.get("PATH", "")
        if directory.lower() in {p.lower() for p in proc.split(os.pathsep) if p}:
            return False
        user = _user_path_windows()
        if directory.lower() in {p.lower() for p in user.split(";") if p}:
            return False
        if _set_user_path_windows(directory):
            ok(f"added {label} to user PATH: {directory}")
            warn("REOPEN your PowerShell / cmd to pick this up.")
            return True
        warn(f"could not add {label} to user PATH automatically: {directory}")
        return False

    proc = os.environ.get("PATH", "")
    if directory in proc.split(os.pathsep):
        return False
    rc = home() / ".profile"
    if not rc.exists():
        for cand in (home() / ".bash_profile", home() / ".bashrc", home() / ".zshrc"):
            if cand.exists():
                rc = cand
                break
    if _add_to_shell_rc(directory, rc):
        ok(f"added {label} to {rc}: {directory}")
        warn(f"REOPEN your shell, or run:  source {rc}")
        return True
    info(f"{label} already on PATH: {directory}")
    return False

def _add_to_shell_rc(extra: str, rcfile: Path) -> bool:
    """Append export PATH=...:$PATH to a POSIX rc file. Idempotent."""
    line = f'export PATH="{extra}:$PATH"'
    try:
        existing = ""
        if rcfile.exists():
            existing = rcfile.read_text(encoding="utf-8", errors="ignore")
        if line in existing:
            return False
        with rcfile.open("a", encoding="utf-8") as f:
            if existing and not existing.endswith("\n"):
                f.write("\n")
            f.write("# claude-go PATH\n")
            f.write(line + "\n")
        return True
    except OSError as e:
        warn(f"could not write {rcfile}: {e}")
        return False

def ensure_self_in_path() -> None:
    r"""If this script's directory is not on PATH, add it persistently.

    Idempotent: a second run is a no-op. Uses ensure_dir_on_path() so the
    same helper can also fix Claude CLI's directory later.
    """
    ensure_dir_on_path(str(Path(__file__).resolve().parent), label="claude-go")

def prereq_check() -> None:
    print()
    info("============================================================")
    info(" Claude Switch - prereq check")
    info("============================================================")
    print()
    info(f"platform : {platform.system()} {platform.release()}")
    info(f"script   : {Path(__file__).resolve()}")
    print()
    info("[1/3] Putting claude-go itself on PATH...")
    ensure_self_in_path()
    print()
    info("[2/3] Preparing ~/.claude/ ...")
    ensure_skeleton()
    print()
    info("[3/3] Checking claude CLI...")
    if not check_claude_cli():
        warn("install Claude Code: https://claude.com/download")
        warn("or via npm:         npm install -g @anthropic-ai/claude-code")
    print()
    ok("Done.")
    print()
    _augment_path_from_registry()
    info("PATH for this run is now current. For future shells, REOPEN PowerShell/cmd.")
    info("Next time, you can just run:  claude-go  (or  python claude-go.py)")
    _print_siblings(Path(__file__).resolve())
    print()

# ============================================================
# Argument parsing
# ============================================================

def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="claude-go",
        description="Cross-platform provider switcher for Claude Code.",
    )
    sub = p.add_subparsers(dest="cmd")

    # All subcommands that launch claude get --no-cost
    def add_no_cost(sp):
        sp.add_argument("--no-cost", action="store_true",
                        help="skip auto cost display after Claude exits")

    add_no_cost(sub.add_parser("flash",      help="deepseek flash + menu + launch claude"))
    add_no_cost(sub.add_parser("pro",        help="deepseek pro + menu + launch claude"))
    add_no_cost(sub.add_parser("menu",       help="interactive picker + launch claude"))
    sub.add_parser("status",     help="show current provider / env")
    sub.add_parser("reset",      help="clear all provider env (back to Anthropic default)")
    sub.add_parser("prereq",     help="check prereqs (settings dir + claude CLI)")
    sub.add_parser("list",       help="list providers / models / pricing")

    p_or = sub.add_parser("or",        help="openrouter: python claude-go.py or [model]")
    p_or.add_argument("model", nargs="?", default="sonnet")
    add_no_cost(p_or)

    p_an = sub.add_parser("anthropic", help="anthropic: python claude-go.py anthropic [model]")
    p_an.add_argument("model", nargs="?", default="sonnet")
    add_no_cost(p_an)

    p_custom = sub.add_parser("use",     help="python claude-go.py use <provider> <model>")
    p_custom.add_argument("provider")
    p_custom.add_argument("model")
    add_no_cost(p_custom)

    p_key = sub.add_parser("key",      help="set provider API key: python claude-go.py key <provider> <key>")
    p_key.add_argument("provider")
    p_key.add_argument("value")

    p_unset = sub.add_parser("unset",    help="remove provider API key from .env")
    p_unset.add_argument("provider")

    p_cost = sub.add_parser("cost",     help="python claude-go.py cost <in> [out] [cr] [cw] [-p provider] [-m model]")
    p_cost.add_argument("input", nargs="?", type=int, default=0)
    p_cost.add_argument("output", nargs="?", type=int, default=0)
    p_cost.add_argument("cache_read", nargs="?", type=int, default=0)
    p_cost.add_argument("cache_write", nargs="?", type=int, default=0)
    p_cost.add_argument("-p", "--provider")
    p_cost.add_argument("-m", "--model")
    p_cost.add_argument("--no-twd", action="store_true")
    p_cost.add_argument("--no-cny", action="store_true")

    p_log = sub.add_parser("cost-log", help="compute cost from the most recent session log")
    p_log.add_argument("--cwd", help="project cwd to match (default: any)")
    p_log.add_argument("--path", help="explicit log path")
    p_log.add_argument("--no-twd", action="store_true")
    p_log.add_argument("--no-cny", action="store_true")

    sub.add_parser("models",     help="alias for list")

    p_bal = sub.add_parser("balance",  help="show DeepSeek account balance")
    p_bal.add_argument("provider", nargs="?", default="deepseek")

    return p

# ============================================================
# Main
# ============================================================

def main(argv: list[str] | None = None) -> int:
    argv = argv if argv is not None else sys.argv[1:]

    # Fix stdout encoding on Windows so symbols (¥, $, etc.) render correctly in PowerShell.
    if platform.system() == "Windows":
        try:
            import io
            sys.stdout = io.TextIOWrapper(sys.stdout.buffer, encoding="utf-8", errors="replace")
        except Exception:
            pass

    # No args: go straight to menu + launch. No bootstrap, no shell detection.
    if not argv:
        return run_claude("__menu__", None, [])

    parser = build_parser()
    args = parser.parse_args(argv)

    cmd = args.cmd

    # Helper: extract --no-cost flag from parsed args (present on launch subcommands)
    no_cost = getattr(args, "no_cost", False)
    no_twd  = getattr(args, "no_twd",  False)
    no_cny  = getattr(args, "no_cny",  False)

    if cmd == "flash":
        return run_claude("deepseek", "flash", [], no_cost=no_cost)
    if cmd == "pro":
        return run_claude("deepseek", "pro", [], no_cost=no_cost)
    if cmd == "or":
        return run_claude("openrouter", args.model, [], no_cost=no_cost)
    if cmd == "anthropic":
        return run_claude("anthropic", args.model, [], no_cost=no_cost)
    if cmd == "use":
        return run_claude(args.provider, args.model, [], no_cost=no_cost)
    if cmd == "menu":
        return run_claude("__menu__", None, [], no_cost=no_cost)
    if cmd == "status":
        show_status()
        return 0
    if cmd == "reset":
        reset_env()
        return 0
    if cmd == "prereq":
        prereq_check()
        return 0
    if cmd in ("list", "models"):
        print()
        info("Providers / models / pricing:")
        for prov, ep in ENDPOINTS.items():
            print()
            info(f"  {prov}  (auth={ep['auth_style']}, key={ep['api_key_env']}, base={ep['base_url'] or '(default)'})")
            for alias, mid in ep["models"].items():
                p = PRICING.get(f"{prov}:{alias}")
                if p:
                    print(f"    - {alias:<10} {mid:<40} in={p['input']:>5} out={p['output']:>5} (USD/1M)")
                else:
                    print(f"    - {alias:<10} {mid}")
        print()
        return 0
    if cmd == "key":
        set_provider_key(args.provider, args.value)
        return 0
    if cmd == "unset":
        reset_provider_key(args.provider)
        return 0
    if cmd == "cost":
        cost(args.input, args.output, args.cache_read, args.cache_write,
             provider=args.provider, model=args.model,
             no_twd=args.no_twd, no_cny=args.no_cny,
             cache_hits=0)
        return 0
    if cmd == "cost-log":
        cost_from_log(path=args.path, cwd=args.cwd, no_twd=args.no_twd, no_cny=args.no_cny)
        return 0
    if cmd == "balance":
        show_balance(args.provider)
        return 0

    parser.print_help()
    return 1

if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print()
        sys.exit(130)
