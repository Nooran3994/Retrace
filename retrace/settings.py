"""Settings & control-plane for Retrace.

Phase 4: everything you used to do on the CLI is now reachable from the
web UI. This module is the single bridge between the HTTP handlers and
the underlying engines (config, detectors, remotes, collectors, watch).

Design rules:
  - No HTTP imports here. This module is pure logic; webui.py calls it.
  - Every function returns JSON-serializable data (dict/list) — the UI
    renders them directly.
  - Long operations (collect, ingest, detect) run in a background
    thread pool so the UI never blocks.
"""

from __future__ import annotations

import json
import os
import threading
import time
from pathlib import Path
from typing import Any

from .db import connect, default_db_path

# ---------------------------------------------------------------------------
# Config (models.config.json) — the master settings file
# ---------------------------------------------------------------------------


def config_path() -> Path:
    from .models import config_path as _cp

    return _cp()


def load_config() -> dict:
    from .models import load_config as _lc

    return _lc()


def save_config(cfg: dict) -> dict:
    """Write config atomically (tmp + rename). Returns the saved config."""
    p = config_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(cfg, indent=2), encoding="utf-8")
    tmp.replace(p)
    if os.name != "nt":
        try:
            os.chmod(p, 0o600)
        except OSError:
            pass
    return cfg


def get_config() -> dict:
    return load_config()


def update_config(patch: dict) -> dict:
    """Deep-merge a patch into the config and persist it."""
    cfg = load_config()

    def _merge(base: dict, upd: dict) -> dict:
        for k, v in upd.items():
            if isinstance(v, dict) and isinstance(base.get(k), dict):
                base[k] = _merge(base[k], v)
            else:
                base[k] = v
        return base

    cfg = _merge(cfg, patch)
    return save_config(cfg)


def reset_config() -> dict:
    from .models import DEFAULTS

    return save_config(json.loads(json.dumps(DEFAULTS)))


# ---------------------------------------------------------------------------
# Detectors (detectors.json) — CRUD over the rule set
# ---------------------------------------------------------------------------


def detectors_path() -> Path:
    from .detectors import DEFAULT_CONFIG_PATH

    return DEFAULT_CONFIG_PATH


def get_detectors() -> dict:
    """Full detector view: built-ins + user overrides, with effective state."""
    from .detectors import BUILTIN_RULES, load_rules

    rules = load_rules()
    by_name = {r["name"]: r for r in rules}
    out = []
    for r in by_name.values():
        out.append(
            {
                "name": r["name"],
                "severity": r.get("severity", "info"),
                "description": r.get("description", ""),
                "match": r.get("match", ""),
                "window_s": r.get("window_s"),
                "threshold": r.get("threshold", 1),
                "min_duration_ms": r.get("min_duration_ms"),
                "message": r.get("message", ""),
                "enabled": r.get("enabled", True),
            }
        )
    return {"rules": out, "path": str(detectors_path())}


def save_detectors(rules: list[dict]) -> dict:
    """Persist the user rule file. Built-ins are merged by name; a rule
    with enabled:false disables the built-in; unknown names become new
    custom rules."""
    p = detectors_path()
    p.parent.mkdir(parents=True, exist_ok=True)
    # Only persist what differs from built-ins to keep the file minimal.
    from .detectors import BUILTIN_RULES

    builtin_names = {r["name"] for r in BUILTIN_RULES}
    user_rules = []
    for r in rules:
        name = r.get("name", "")
        if name in builtin_names:
            base = next(b for b in BUILTIN_RULES if b["name"] == name)
            patch = {k: v for k, v in r.items() if v != base.get(k)}
            if r.get("enabled") is False:
                user_rules.append({"name": name, "enabled": False})
            elif patch:
                user_rules.append({"name": name, **patch})
        else:
            user_rules.append(r)
    tmp = p.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(user_rules, indent=2), encoding="utf-8")
    tmp.replace(p)
    return {"saved": len(user_rules), "path": str(p)}


def add_detector(rule: dict) -> dict:
    rules = get_detectors()["rules"]
    name = rule.get("name", "").strip()
    if not name:
        return {"error": "rule name is required"}
    if any(r["name"] == name for r in rules):
        return {"error": f"rule '{name}' already exists"}
    rule["name"] = name
    rules.append(rule)
    save_detectors(rules)
    return {"ok": True, "name": name}


def update_detector(name: str, patch: dict) -> dict:
    rules = get_detectors()["rules"]
    for r in rules:
        if r["name"] == name:
            r.update(patch)
            save_detectors(rules)
            return {"ok": True}
    return {"error": f"rule '{name}' not found"}


def delete_detector(name: str) -> dict:
    rules = get_detectors()["rules"]
    rules = [r for r in rules if r["name"] != name]
    save_detectors(rules)
    return {"ok": True}


def test_detector(match: str, sample: str) -> dict:
    """Live regex test: does this pattern fire on a sample command?"""
    import re

    try:
        pat = re.compile(match, re.IGNORECASE)
    except re.error as exc:
        return {"ok": False, "error": f"invalid regex: {exc}"}
    hit = bool(pat.search(sample or ""))
    return {"ok": True, "match": hit, "sample": sample}


# ---------------------------------------------------------------------------
# Remotes (hosts.json) — CRUD + connection test + collect
# ---------------------------------------------------------------------------


def get_remotes() -> list[dict]:
    from . import remote

    out = []
    for rec in remote.load_hosts():
        out.append(
            {
                "name": rec.get("name", rec.get("host")),
                "host": rec.get("host", ""),
                "user": rec.get("user"),
                "port": rec.get("port", 22),
            }
        )
    return out


def add_remote(name: str, host: str, user: str = "", port: int = 22) -> dict:
    from . import remote

    try:
        rec = remote.add_host(name, host, user=user or None, port=port)
    except ValueError as exc:
        return {"error": str(exc)}
    return {"ok": True, "remote": rec}


def remove_remote(name: str) -> dict:
    from . import remote

    ok = remote.remove_host(name)
    return {"ok": ok}


def test_remote(name: str) -> dict:
    from . import remote

    try:
        rec = remote.get_host(name)
    except KeyError as exc:
        return {"error": str(exc)}
    ok, msg = remote.test_connection(rec)
    return {"ok": ok, "message": msg}


# ---------------------------------------------------------------------------
# Background job runner (long ops never block the HTTP thread)
# ---------------------------------------------------------------------------

_jobs: dict[str, dict] = {}
_jobs_lock = threading.Lock()
_job_id = 0


def _run_job(job_id: str, fn, *args, **kwargs) -> None:
    try:
        result = fn(*args, **kwargs)
        with _jobs_lock:
            _jobs[job_id]["status"] = "done"
            _jobs[job_id]["result"] = result
    except Exception as exc:
        with _jobs_lock:
            _jobs[job_id]["status"] = "error"
            _jobs[job_id]["error"] = str(exc)


def start_job(name: str, fn, *args, **kwargs) -> dict:
    global _job_id
    with _jobs_lock:
        _job_id += 1
        jid = f"{name}-{_job_id}"
        _jobs[jid] = {"name": name, "status": "running", "started": time.time(), "result": None, "error": None}
    t = threading.Thread(target=_run_job, args=(jid, fn, *args), kwargs=kwargs, daemon=True)
    t.start()
    return {"job_id": jid, "status": "running"}


def job_status(job_id: str) -> dict:
    with _jobs_lock:
        job = _jobs.get(job_id)
        if job is None:
            return {"error": "unknown job"}
        return dict(job)


# ---------------------------------------------------------------------------
# Control-plane actions (the CLI commands, callable from the UI)
# ---------------------------------------------------------------------------


def action_ingest(history: bool = False, flight: bool = False, ps: bool = False) -> dict:
    """Mirror of `retrace ingest [--history|--flight|--ps]`."""
    conn = connect()
    results: dict = {}
    try:
        if history:
            from .collectors.history import ingest_history

            results["history"] = ingest_history(conn)
        if flight:
            from .flight_recorder import ingest_spool

            spool = ingest_spool(conn)
            results["flight"] = {"loaded": spool["loaded"], "skipped": spool["skipped"]}
        if ps:
            from .ps_transcript import ingest_transcripts

            ps = ingest_transcripts(conn)
            results["ps"] = {"loaded": ps["loaded"], "skipped": ps["skipped"], "files": ps["files"]}
        if not (history or flight or ps):
            from .collectors.history import ingest_history

            results["history"] = ingest_history(conn)
        conn.commit()
    finally:
        conn.close()
    return results


def action_detect(since_minutes: int = 60, limit: int = 5000) -> dict:
    """Mirror of `retrace detect` — run detectors, persist alerts."""
    conn = connect()
    from .detectors import detect
    from .webui import insert_alert

    try:
        alerts = detect(conn, since_minutes=since_minutes, limit=limit)
        inserted = []
        for a in alerts:
            rid = insert_alert(conn, a)
            inserted.append({**a, "id": rid})
        conn.commit()
        return {"alerts": inserted, "count": len(inserted)}
    finally:
        conn.close()


def action_collect_remote(name: str | None = None, all_hosts: bool = False) -> dict:
    """Mirror of `retrace remote collect [--all|<name>]`."""
    conn = connect()
    from . import remote

    try:
        if all_hosts:
            results = remote.collect_all(db_conn=conn)
            out = {k: (v if "error" in v else {"loaded": v["loaded"], "skipped": v["skipped"]}) for k, v in results.items()}
            return out
        if not name:
            return {"error": "no remote name given"}
        rec = remote.get_host(name)
        res = remote.collect_host(rec, db_conn=conn)
        if "error" in res:
            return res
        return {"loaded": res["loaded"], "skipped": res["skipped"]}
    except KeyError as exc:
        return {"error": str(exc)}
    finally:
        conn.close()


def action_win_events(minutes: int = 60, channels: list[str] | None = None) -> dict:
    """Mirror of `retrace win-events`."""
    conn = connect()
    from .collectors.windows_events import collect

    try:
        res = collect(conn, minutes=minutes, channels=channels)
        if "error" in res:
            return res
        return {"counts": res, "total": sum(res.values())}
    finally:
        conn.close()


def action_export(format: str = "jsonl", days: int = 30) -> dict:
    """Mirror of `retrace export --format <fmt>` (bounded to recent days)."""
    conn = connect()
    since = time.time() - days * 86400
    rows = conn.execute(
        "SELECT * FROM commands WHERE ts >= ? ORDER BY ts", (since,)
    ).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM commands LIMIT 0").description]
    records = [dict(zip(cols, r)) for r in rows]
    conn.close()

    if format == "jsonl":
        payload = "\n".join(json.dumps(r) for r in records) + ("\n" if records else "")
    elif format == "csv":
        import csv
        import io

        buf = io.StringIO()
        writer = csv.DictWriter(buf, fieldnames=cols)
        writer.writeheader()
        writer.writerows(records)
        payload = buf.getvalue()
    else:
        payload = json.dumps(records, indent=2)
    return {"format": format, "records": len(records), "payload": payload}


def action_stats() -> dict:
    from .db import stats

    conn = connect()
    try:
        return stats(conn)
    finally:
        conn.close()


def action_agent_cycle(include_model: bool = True) -> dict:
    """Run one agent cycle (capture + detect + optional model)."""
    conn = connect()
    from .agent import run_cycle

    try:
        return run_cycle(conn, verbose=False, include_model=include_model)
    finally:
        conn.close()


def action_remote_script() -> str:
    from . import remote

    return remote.script_only()