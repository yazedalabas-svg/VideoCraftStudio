#!/usr/bin/env python3
"""Daily Blackboard study assistant: install, verify, schedule and run.

    python bb.py setup      venv + BlackboardMCP (pinned commit), creates .env and .mcp.json
    python bb.py check      calls whoami + list_courses through the MCP (never prints the cookie)
    python bb.py schedule   daily at 13:00 Asia/Riyadh: Task Scheduler (Windows) or cron (macOS/Linux)
    python bb.py run        what the scheduler calls: headless Claude Code -> bb-daily skill
    python bb.py mcp        (internal) starts the MCP server with the cookie loaded from .env

Standard library only; the MCP's own dependencies live in .venv.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path

# ---------------------------------------------------------------- paths & constants
ROOT = Path(__file__).resolve().parent
WIN = os.name == "nt"
VENV = ROOT / ".venv"
PY = VENV / ("Scripts/python.exe" if WIN else "bin/python")
PYW = VENV / "Scripts/pythonw.exe"  # Windows: no console window flashing at 13:00
ENV = ROOT / ".env"
LOG = ROOT / "bb-daily.log"
RIYADH = timezone(timedelta(hours=3))  # Saudi Arabia has no DST
TASK = "bb-daily"

MCP_COMMIT = "9a470b29bae8e98fcea08811c8f4b188ae85aeb6"  # reviewed 2026-11-12
MCP_PKG = f"https://github.com/yoyoconnor/BlackboardMCP/archive/{MCP_COMMIT}.zip"

ENV_TEMPLATE = """\
# Blackboard session — keep this file private (it is gitignored).
BB_BASE_URL=https://uoh.blackboard.com
BB_TIMEZONE=Asia/Riyadh
# Paste the whole Cookie header value (it contains BbRouter=...) on ONE line after the '='.
BB_COOKIE=
# Optional: token from `claude setup-token` if the scheduled run cannot reach your Claude login.
# CLAUDE_CODE_OAUTH_TOKEN=
"""


def read_env() -> dict[str, str]:
    out: dict[str, str] = {}
    if not ENV.exists():
        return out
    for line in ENV.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        v = v.strip()
        if len(v) > 1 and v[0] == v[-1] and v[0] in "\"'":
            v = v[1:-1]
        out[k.strip()] = v
    return out


def in_venv() -> bool:
    return PY.exists() and Path(sys.prefix).resolve() == VENV.resolve()


def reexec_in_venv() -> None:
    if not PY.exists():
        sys.exit("Run `python bb.py setup` first.")
    sys.exit(subprocess.call([str(PY), str(Path(__file__).resolve()), *sys.argv[1:]]))


# ---------------------------------------------------------------- setup
def setup() -> None:
    if sys.version_info < (3, 10):
        sys.exit("Python 3.10+ is required.")
    if not PY.exists():
        print("Creating .venv ...")
        subprocess.check_call([sys.executable, "-m", "venv", str(VENV)])
    print(f"Installing BlackboardMCP @ {MCP_COMMIT[:7]} ...")
    subprocess.check_call([str(PY), "-m", "pip", "install", "-q", "--disable-pip-version-check", MCP_PKG])

    if not ENV.exists():
        ENV.write_text(ENV_TEMPLATE, encoding="utf-8")
        print(f"Created {ENV.name} — paste your cookie into BB_COOKIE.")
    if not WIN:
        os.chmod(ENV, 0o600)

    mcp = {"mcpServers": {"blackboard": {"command": str(PY), "args": [str(ROOT / "bb.py"), "mcp"]}}}
    (ROOT / ".mcp.json").write_text(json.dumps(mcp, indent=2) + "\n", encoding="utf-8")
    for d in ("reports", "drafts"):
        (ROOT / d).mkdir(exist_ok=True)
    print("Setup done. Next: fill BB_COOKIE in .env, then `python bb.py check`.")


# ---------------------------------------------------------------- mcp (stdio server launcher)
def serve_mcp() -> None:
    if not in_venv():
        reexec_in_venv()
    env = read_env()
    for key in ("BB_BASE_URL", "BB_COOKIE", "BB_TIMEZONE", "BB_CACHE_DIR"):
        if env.get(key):
            os.environ[key] = env[key]
    from blackboard_mcp.server import main

    main()


# ---------------------------------------------------------------- check
def check() -> None:
    if not in_venv():
        reexec_in_venv()
    if not read_env().get("BB_COOKIE"):
        sys.exit("BB_COOKIE is empty in .env.")
    import asyncio

    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def go() -> None:
        params = StdioServerParameters(command=str(PY), args=[str(ROOT / "bb.py"), "mcp"], cwd=str(ROOT))
        async with stdio_client(params) as (r, w), ClientSession(r, w) as s:
            await s.initialize()
            for tool in ("whoami", "list_courses"):
                res = await s.call_tool(tool, {})
                text = "\n".join(getattr(c, "text", "") for c in res.content)
                print(f"\n## {tool}\n{text}")

    asyncio.run(go())


# ---------------------------------------------------------------- schedule
def local_time_for_riyadh_13() -> tuple[int, int]:
    t = datetime.now(RIYADH).replace(hour=13, minute=0, second=0, microsecond=0).astimezone()
    return t.hour, t.minute


def schedule() -> None:
    hh, mm = local_time_for_riyadh_13()
    claude = find_claude()
    if not claude:
        sys.exit("Claude Code (`claude`) not found on PATH. Install it and log in first.")
    script = str(ROOT / "bb.py")

    if WIN:
        q = lambda s: str(s).replace("'", "''")  # noqa: E731  PowerShell single-quote escape
        exe = PYW if PYW.exists() else PY
        ps = (
            f"$a = New-ScheduledTaskAction -Execute '{q(exe)}' -Argument '\"{q(script)}\" run' "
            f"-WorkingDirectory '{q(ROOT)}';"
            f"$t = New-ScheduledTaskTrigger -Daily -At '{hh:02d}:{mm:02d}';"
            "$s = New-ScheduledTaskSettingsSet -StartWhenAvailable -ExecutionTimeLimit (New-TimeSpan -Hours 1);"
            f"Register-ScheduledTask -TaskName '{TASK}' -Action $a -Trigger $t -Settings $s -Force | Out-Null"
        )
        subprocess.check_call(["powershell", "-NoProfile", "-Command", ps])
        print(f"Task Scheduler: '{TASK}' daily at {hh:02d}:{mm:02d} local (= 13:00 Riyadh).")
        return

    q = lambda s: "'" + str(s).replace("'", "'\\''") + "'"  # noqa: E731  sh single-quote escape
    path = os.pathsep.join(dict.fromkeys([str(Path(claude).parent), os.environ.get("PATH", "")]))
    line = f"{mm} {hh} * * * cd {q(ROOT)} && PATH={q(path)} {q(PY)} {q(script)} run  # {TASK}"
    cur = subprocess.run(["crontab", "-l"], capture_output=True, text=True).stdout
    kept = [ln for ln in cur.splitlines() if not ln.rstrip().endswith(f"# {TASK}")]
    subprocess.run(["crontab", "-"], input="\n".join([*kept, line]) + "\n", text=True, check=True)
    print(f"cron: daily at {hh:02d}:{mm:02d} local (= 13:00 Riyadh).")


# ---------------------------------------------------------------- run (headless)
def find_claude() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    home = Path.home()
    for p in (home / ".local/bin/claude", home / ".claude/local/claude", Path("/opt/homebrew/bin/claude"),
              Path("/usr/local/bin/claude"), home / ".local/bin/claude.exe", home / "AppData/Roaming/npm/claude.cmd"):
        if p.exists():
            return str(p)
    return None


def log(msg: str) -> None:
    if LOG.exists() and LOG.stat().st_size > 2_000_000:  # keep the single log file small
        LOG.write_text(LOG.read_text(encoding="utf-8", errors="replace")[-1_000_000:], encoding="utf-8")
    with LOG.open("a", encoding="utf-8") as f:
        f.write(msg.rstrip() + "\n")


def run() -> None:
    now = datetime.now(RIYADH)
    day = now.strftime("%Y-%m-%d")
    report = ROOT / "reports" / f"{day}.md"
    log(f"\n===== {now:%Y-%m-%d %H:%M:%S} Riyadh — run start =====")

    claude = find_claude()
    code, out = 127, "claude not found on PATH"
    if claude:
        env = os.environ.copy()
        token = read_env().get("CLAUDE_CODE_OAUTH_TOKEN")
        if token:
            env["CLAUDE_CODE_OAUTH_TOKEN"] = token
        cmd = [
            claude, "-p", f"/bb-daily {day}",
            "--permission-mode", "dontAsk",
            "--settings", str(ROOT / ".claude" / "settings.json"),
            "--mcp-config", str(ROOT / ".mcp.json"), "--strict-mcp-config",
            "--output-format", "text",
        ]
        try:
            p = subprocess.run(cmd, cwd=ROOT, env=env, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=45 * 60,
                               creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            code, out = p.returncode, (p.stdout + ("\n[stderr]\n" + p.stderr if p.stderr.strip() else ""))
        except subprocess.TimeoutExpired:
            code, out = 124, "timed out after 45 minutes"
        except OSError as e:
            code, out = 126, f"could not start claude: {e}"
    log(out)
    log(f"===== exit {code} =====")

    if not report.exists():  # Claude never got to write: leave a visible note instead of silence
        report.parent.mkdir(exist_ok=True)
        report.write_text(
            f"# تقرير Blackboard — {day}\n\n> ⚠️ **لم يكتمل التشغيل اليومي** (exit {code}).\n"
            f"> راجع آخر جزء من `bb-daily.log`. إن كان السبب تسجيل الدخول في Claude Code، شغّل `claude` مرة "
            "وسجّل الدخول، أو ضع `CLAUDE_CODE_OAUTH_TOKEN` في `.env`.\n",
            encoding="utf-8",
        )


# ---------------------------------------------------------------- entry
if __name__ == "__main__":
    cmds = {"setup": setup, "check": check, "schedule": schedule, "run": run, "mcp": serve_mcp}
    if len(sys.argv) < 2 or sys.argv[1] not in cmds:
        sys.exit(__doc__)
    cmds[sys.argv[1]]()
