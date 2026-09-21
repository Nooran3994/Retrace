"""Retrace CLI — `retrace` / `rec`.

Commands:
  retrace ingest                 Ingest existing shell history into the DB
  retrace ingest --flight        Load flight-recorder spool into the DB
  retrace ingest --ps            Load PowerShell transcripts into the DB
  retrace hook install           Install the bash flight-recorder hook
  retrace hook install-ps        Install the PowerShell transcript hook
  retrace win-events             Collect Windows Event Log entries
  retrace search <query>         Search captured commands
  retrace stats                  Show aggregate stats
  retrace detect                 Run rule-based detectors over recent records
  retrace export --format jsonl  Export records (default: to stdout)
"""

import argparse
import json
import sys
import time

from . import __version__
from .db import connect, default_db_path, search, stats


def cmd_ingest(args) -> None:
    conn = connect()
    results = {}
    if args.history:
        from .collectors.history import ingest_history

        results = ingest_history(conn)
        total = sum(results.values())
        print(f"Ingested {total} history records: {results}")
    if args.flight:
        from .flight_recorder import ingest_spool

        spool = ingest_spool(conn)
        print(f"Flight spool: {spool['loaded']} loaded, {spool['skipped']} skipped")
    if args.ps:
        from .ps_transcript import ingest_transcripts

        ps = ingest_transcripts(conn)
        print(f"PowerShell transcripts: {ps['loaded']} loaded, {ps['skipped']} skipped, {ps['files']} files")
    if not (args.history or args.flight or args.ps):
        from .collectors.history import ingest_history

        results = ingest_history(conn)
        total = sum(results.values())
        print(f"Ingested {total} history records: {results}")
    conn.close()


def cmd_hook(args) -> None:
    if args.hook_cmd == "install":
        from .flight_recorder import install_bash_hook

        rc = install_bash_hook()
        print(f"Flight recorder hook installed in {rc}")
        print("Open a NEW terminal for it to take effect.")
    elif args.hook_cmd == "install-ps":
        from .ps_transcript import install_ps_hook

        profile = install_ps_hook()
        print(f"PowerShell transcript hook installed in {profile}")
        print("Open a NEW PowerShell window for it to take effect.")


def cmd_win_events(args) -> None:
    conn = connect()
    from .collectors.windows_events import collect

    results = collect(conn, minutes=args.minutes, channels=args.channels)
    if "error" in results:
        print(f"Error: {results['error']}", file=sys.stderr)
        conn.close()
        return
    total = sum(results.values())
    print(f"Collected {total} Windows events:")
    for source, count in results.items():
        print(f"  {source}: {count}")
    conn.close()


def cmd_search(args) -> None:
    conn = connect()
    since = time.time() - args.since * 60 if args.since else None
    rows = search(conn, args.query, limit=args.limit, since=since)
    if not rows:
        print("No matches.")
        conn.close()
        return
    for r in rows:
        ts = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(r["ts"])) if r["ts"] else "?"
        git = f" [{r['git_repo']}@{r['git_branch']}]" if r.get("git_repo") else ""
        print(f"{r['id']}  {ts}  {r['shell']:<4} {r['command']}{git}")
    conn.close()


def cmd_stats(args) -> None:
    conn = connect()
    s = stats(conn)
    print(f"Total records: {s['total']}")
    print(f"By source:     {s['by_source']}")
    if s["last_ts"]:
        print(f"Last capture:  {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s['last_ts']))}")
    conn.close()


def cmd_detect(args) -> None:
    """Run rule-based detectors and print alerts."""
    conn = connect()
    from .detectors import detect, write_default_config

    if args.init_config:
        path = write_default_config()
        print(f"Wrote example config to {path}")
        conn.close()
        return

    alerts = detect(conn, since_minutes=args.since, limit=args.limit)
    if not alerts:
        print(f"No alerts in the last {args.since} minutes. ✓")
        conn.close()
        return

    print(f"{len(alerts)} alert(s) in the last {args.since} minutes:")
    for a in alerts:
        ts = time.strftime("%H:%M:%S", time.localtime(a["last_ts"]))
        print(f"  [{a['severity'].upper():<8}] {a['rule']}  @ {ts}")
        print(f"           {a['message']}")
        if a["count"] > 1:
            print(f"           ({a['count']} occurrences)")
    conn.close()



def cmd_web(args) -> None:
    """Start the local-only web UI."""
    from .webui import serve

    serve(port=args.port)


def cmd_watch(args) -> None:
    """Run the watch daemon (periodic ingest + detection)."""
    from .watch import loop

    loop(interval_s=args.interval, once=args.once)


def cmd_export(args) -> None:
    conn = connect()
    rows = conn.execute("SELECT * FROM commands ORDER BY ts").fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM commands LIMIT 0").description]
    records = [dict(zip(cols, r)) for r in rows]

    if args.format == "jsonl":
        payload = "\n".join(json.dumps(r) for r in records) + ("\n" if records else "")
    elif args.format == "csv":
        import csv
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        writer.writerows(records)
        payload = buf.getvalue()
    else:
        payload = json.dumps(records, indent=2)

    if args.output:
        with open(args.output, "w", encoding="utf-8") as f:
            f.write(payload)
        print(f"Exported {len(records)} records to {args.output}")
    else:
        sys.stdout.write(payload)
    conn.close()


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(prog="retrace", description=__doc__)
    parser.add_argument("--version", action="version", version=f"retrace {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    p_ingest = sub.add_parser("ingest", help="Ingest existing history into the DB")
    p_ingest.add_argument("--flight", action="store_true", help="Also load flight spool")
    p_ingest.add_argument("--ps", action="store_true", help="Load PowerShell transcripts")
    p_ingest.add_argument("--history", action="store_true", help="Load shell history (default)")
    p_ingest.set_defaults(func=cmd_ingest)

    p_hook = sub.add_parser("hook", help="Manage the flight recorder hook")
    p_hook_sub = p_hook.add_subparsers(dest="hook_cmd", required=True)
    p_hook_install = p_hook_sub.add_parser("install", help="Install bash hook")
    p_hook_install.set_defaults(func=cmd_hook)
    p_hook_install_ps = p_hook_sub.add_parser("install-ps", help="Install PowerShell transcript hook")
    p_hook_install_ps.set_defaults(func=cmd_hook)

    p_win = sub.add_parser("win-events", help="Collect Windows Event Log entries")
    p_win.add_argument("--minutes", type=int, default=60, help="Look back window (default 60)")
    p_win.add_argument("--channels", nargs="*", help="Specific channels (Security, System, Application, PowerShell)")
    p_win.set_defaults(func=cmd_win_events)

    p_search = sub.add_parser("search", help="Search captured commands")
    p_search.add_argument("query")
    p_search.add_argument("--limit", type=int, default=50)
    p_search.add_argument("--since", type=float, default=0, help="Minutes back")
    p_search.set_defaults(func=cmd_search)

    p_stats = sub.add_parser("stats", help="Show aggregate stats")
    p_stats.set_defaults(func=cmd_stats)

    p_detect = sub.add_parser("detect", help="Run rule-based detectors over recent records")
    p_detect.add_argument("--since", type=int, default=60, help="Look back window in minutes (default 60)")
    p_detect.add_argument("--limit", type=int, default=2000, help="Max rows to evaluate (default 2000)")
    p_detect.add_argument("--init-config", action="store_true", help="Write example detectors.json and exit")
    p_detect.set_defaults(func=cmd_detect)


    p_web = sub.add_parser("web", help="Start local-only web UI (127.0.0.1)")
    p_web.add_argument("--port", type=int, default=8765)
    p_web.set_defaults(func=cmd_web)

    p_watch = sub.add_parser("watch", help="Watch daemon: periodic ingest + detect")
    p_watch.add_argument("--interval", type=int, default=60, help="Seconds between cycles")
    p_watch.add_argument("--once", action="store_true", help="Run one cycle and exit")
    p_watch.set_defaults(func=cmd_watch)

    p_export = sub.add_parser("export", help="Export records")
    p_export.add_argument("--format", choices=["jsonl", "csv", "json"], default="jsonl")
    p_export.add_argument("--output", help="Output file (default: stdout)")
    p_export.set_defaults(func=cmd_export)

    args = parser.parse_args(argv)
    try:
        args.func(args)
    except KeyboardInterrupt:
        print("\nInterrupted.", file=sys.stderr)
        return 130
    return 0


if __name__ == "__main__":
    raise SystemExit(main())