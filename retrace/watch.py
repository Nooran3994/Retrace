"""Retrace watch daemon — periodic ingest + detection.

Runs a loop that:
  1. Ingests shell history, flight spool, PS transcripts
  2. Collects Windows Event Log entries (if available)
  3. Runs rule-based detectors and persists alerts
  4. Sleeps for the configured interval

Designed to run under systemd (Linux/WSL) or Task Scheduler (Windows).
"""

from __future__ import annotations

import time


def run_once(conn, verbose: bool = True) -> dict:
    """One full capture+detect cycle. Returns a summary dict."""
    summary: dict = {"ingested": 0, "events": 0, "alerts": 0}

    # 1. Shell history
    try:
        from .collectors.history import ingest_history

        res = ingest_history(conn)
        summary["ingested"] += sum(res.values())
    except Exception as exc:
        if verbose:
            print(f"[watch] history ingest failed: {exc}")

    # 2. Flight spool
    try:
        from .flight_recorder import ingest_spool

        spool = ingest_spool(conn)
        summary["ingested"] += spool["loaded"]
    except Exception as exc:
        if verbose:
            print(f"[watch] flight spool failed: {exc}")

    # 3. PowerShell transcripts
    try:
        from .ps_transcript import ingest_transcripts

        ps = ingest_transcripts(conn)
        summary["ingested"] += ps["loaded"]
    except Exception as exc:
        if verbose:
            print(f"[watch] ps transcripts failed: {exc}")

    # 4. Windows events (best-effort; no-op outside Windows interop)
    try:
        from .collectors.windows_events import collect

        res = collect(conn, minutes=60)
        if "error" not in res:
            summary["events"] = sum(res.values())
    except Exception as exc:
        if verbose:
            print(f"[watch] win-events failed: {exc}")

    # 5. Detect + persist
    try:
        from .detectors import detect
        from .webui import insert_alert

        alerts = detect(conn, since_minutes=60, limit=5000)
        for a in alerts:
            insert_alert(conn, a)
        summary["alerts"] = len(alerts)
    except Exception as exc:
        if verbose:
            print(f"[watch] detect failed: {exc}")

    return summary


def loop(interval_s: int = 60, once: bool = False, verbose: bool = True) -> None:
    """Main loop. `once=True` runs a single cycle and exits."""
    from .db import connect

    conn = connect()
    if verbose:
        print(f"Retrace watch — interval {interval_s}s (Ctrl-C to stop)")

    while True:
        started = time.time()
        summary = run_once(conn, verbose=verbose)
        if verbose:
            print(
                f"[{time.strftime('%H:%M:%S')}] ingested={summary['ingested']} "
                f"events={summary['events']} alerts={summary['alerts']}"
            )
        if once:
            break
        elapsed = time.time() - started
        try:
            time.sleep(max(1, interval_s - elapsed))
        except KeyboardInterrupt:
            if verbose:
                print("\nStopped.")
            break

    conn.close()