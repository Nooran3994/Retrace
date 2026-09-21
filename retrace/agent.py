"""Retrace agent — the persistent intelligence loop.

Design goals:
  - Runs forever under systemd (user unit). Restarts on crash.
  - Capture + detect NEVER depend on a model. Model analysis is an
    OPT-IN add-on that runs AFTER deterministic detection and only
    ever sees redacted text.
  - Every cycle is idempotent: re-ingesting the same spool/history
    dedups against existing rows (see db/remote/collectors).
  - The agent keeps a small state file so it can report health and
    last-cycle timing to the web UI.

Loop per cycle:
  1. ingest: history, flight spool, PS transcripts, win-events, remotes
  2. detect: rule-based, offline, deterministic -> alerts table
  3. model:  OPT-IN. redacted text -> insights -> alerts table (advisory)
  4. state:  write cycle summary to state.json for the UI
  5. sleep:  configurable interval, skip if a cycle overruns
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from .db import connect

CYCLE_DEFAULT_S = 60


def state_path() -> Path:
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "retrace" / "state.json"


def _write_state(state: dict) -> None:
    p = state_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(json.dumps(state, indent=2), encoding="utf-8")
        if os.name != "nt":
            os.chmod(p, 0o600)
    except OSError:
        pass


def _read_state() -> dict:
    p = state_path()
    try:
        return json.loads(p.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return {}


def run_cycle(conn, verbose: bool = True, include_model: bool = True) -> dict:
    """One full capture+detect+optional-model cycle. Returns summary dict."""
    summary: dict = {
        "ingested": 0, "events": 0, "alerts": 0, "remote": 0, "insights": 0,
        "errors": [],
    }

    # --- 1. Shell history ---
    try:
        from .collectors.history import ingest_history
        res = ingest_history(conn)
        summary["ingested"] += sum(res.values())
    except Exception as exc:
        summary["errors"].append(f"history: {exc}")

    # --- 2. Flight spool ---
    try:
        from .flight_recorder import ingest_spool
        spool = ingest_spool(conn)
        summary["ingested"] += spool.get("loaded", 0)
    except Exception as exc:
        summary["errors"].append(f"flight: {exc}")

    # --- 3. PowerShell transcripts ---
    try:
        from .ps_transcript import ingest_transcripts
        ps = ingest_transcripts(conn)
        summary["ingested"] += ps.get("loaded", 0)
    except Exception as exc:
        summary["errors"].append(f"ps: {exc}")

    # --- 4. Windows events (best-effort) ---
    try:
        from .collectors.windows_events import collect
        res = collect(conn, minutes=60)
        if "error" not in res:
            summary["events"] = sum(res.values())
    except Exception as exc:
        summary["errors"].append(f"win-events: {exc}")

    # --- 5. Remote hosts (agentless SSH) ---
    try:
        from . import remote
        hosts = remote.load_hosts()
        if hosts:
            res = remote.collect_all(db_conn=conn)
            summary["remote"] = sum(
                r.get("loaded", 0) for r in res.values() if "error" not in r
            )
    except Exception as exc:
        summary["errors"].append(f"remote: {exc}")

    # --- 6. Deterministic detection (offline, always) ---
    try:
        from .detectors import detect
        from .webui import insert_alert
        alerts = detect(conn, since_minutes=60, limit=5000)
        for a in alerts:
            insert_alert(conn, a)
        summary["alerts"] = len(alerts)
    except Exception as exc:
        summary["errors"].append(f"detect: {exc}")

    # --- 7. OPT-IN model analysis (redacted text only) ---
    if include_model:
        try:
            from .models import load_config, analyze_recent
            cfg = load_config()
            if cfg.get("model", {}).get("enabled", False):
                insights = analyze_recent(conn, since_minutes=60, limit=200, cfg=cfg)
                for ins in insights:
                    insert_alert(
                        conn,
                        {
                            "rule": "model-insight",
                            "severity": ins.get("severity", "low"),
                            "message": f"{ins.get('title', '')} — {ins.get('detail', '')}",
                            "count": 1,
                        },
                    )
                summary["insights"] = len(insights)
        except Exception as exc:
            summary["errors"].append(f"model: {exc}")

    return summary


def loop(interval_s: int = CYCLE_DEFAULT_S, once: bool = False,
         verbose: bool = True, include_model: bool = True) -> None:
    """Main agent loop. `once=True` runs a single cycle and exits."""
    conn = connect()
    if verbose:
        print(f"Retrace agent — interval {interval_s}s (Ctrl-C to stop)")

    while True:
        cycle_started = time.time()
        summary = run_cycle(conn, verbose=verbose, include_model=include_model)
        state = {
            "last_cycle": cycle_started,
            "last_cycle_end": time.time(),
            "interval_s": interval_s,
            "summary": summary,
            "pid": os.getpid(),
        }
        _write_state(state)
        if verbose:
            print(
                f"[{time.strftime('%H:%M:%S')}] ingested={summary['ingested']} "
                f"events={summary['events']} remote={summary['remote']} "
                f"alerts={summary['alerts']} insights={summary['insights']}"
            )
            if summary["errors"]:
                for err in summary["errors"]:
                    print(f"    ! {err}")
        if once:
            break
        elapsed = time.time() - cycle_started
        try:
            time.sleep(max(1, interval_s - elapsed))
        except KeyboardInterrupt:
            if verbose:
                print("\nStopped.")
            break

    conn.close()

def _main(argv=None) -> int:
    """CLI entry for `python3 -m retrace.agent`."""
    import argparse
    p = argparse.ArgumentParser(prog="retrace-agent", description="Retrace persistent agent loop")
    p.add_argument("--interval", type=int, default=CYCLE_DEFAULT_S, help="Seconds between cycles")
    p.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p.add_argument("--no-model", action="store_true", help="Skip model analysis even if enabled")
    p.add_argument("--quiet", action="store_true", help="Suppress per-cycle output")
    args = p.parse_args(argv)
    try:
        loop(interval_s=args.interval, once=args.once,
             verbose=not args.quiet, include_model=not args.no_model)
    except KeyboardInterrupt:
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(_main())
