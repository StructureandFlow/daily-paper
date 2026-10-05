#!/usr/bin/env python3
"""python run.py check   -> decides if this scheduled run should send (stdlib only)
   python run.py build   -> gather stories, make the PDF, email it   [--no-email] [--demo]"""
import json, os, sys, time
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

ROOT = Path(__file__).parent


def read(name, default):
    p = ROOT / name
    return json.loads(p.read_text()) if p.exists() else default


def check():
    cfg, st = read("settings.json", {}), read("state.json", {})
    now = datetime.now(ZoneInfo(cfg.get("timezone", "America/New_York")))
    sch = cfg.get("schedule", {})
    forced = os.environ.get("GITHUB_EVENT_NAME") == "workflow_dispatch"      # "Send now" button
    due = bool(sch.get("enabled")) and now.strftime("%H:%M") >= sch.get("time", "07:00") \
        and st.get("last_sent") != now.strftime("%Y-%m-%d")
    go = "true" if (forced or due) else "false"
    if os.environ.get("GITHUB_OUTPUT"):
        open(os.environ["GITHUB_OUTPUT"], "a").write(f"go={go}\n")
    print(f"local time {now:%H:%M} · forced={forced} · due={due} · go={go}")


def build():
    tz = read("settings.json", {}).get("timezone", "America/New_York")
    os.environ["TZ"] = tz
    if hasattr(time, "tzset"):
        time.tzset()
    import engine
    demo, quiet = "--demo" in sys.argv, "--no-email" in sys.argv
    today, st = datetime.now().strftime("%Y-%m-%d"), read("state.json", {})
    cfg = engine.load()
    cfg["edition_start"] = st.get("first_sent", today)       # issue No. counts from this copy's first send
    out, n = engine.build(cfg, send=not (demo or quiet), demo=demo)
    print(f"{out.name}: {n} stories")
    if not (demo or quiet):
        (ROOT / "state.json").write_text(json.dumps({"first_sent": st.get("first_sent", today), "last_sent": today}))


if __name__ == "__main__":
    {"check": check, "build": build}.get(sys.argv[1] if len(sys.argv) > 1 else "", lambda: sys.exit(__doc__))()
