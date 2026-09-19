"""SQLite storage layer for Retrace.

Single local database, file permissions locked to the current user.
Schema is append-only by design: rows are immutable once written,
which keeps replay timelines honest.
"""

import os
import sqlite3
import time
import uuid
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS commands (
    id          TEXT PRIMARY KEY,
    ts          REAL NOT NULL,              -- epoch seconds (UTC)
    source      TEXT NOT NULL,              -- 'history', 'flight', 'import'
    shell       TEXT NOT NULL,              -- 'bash', 'zsh', 'powershell', 'cmd'
    command     TEXT NOT NULL,              -- redacted command text
    cwd         TEXT,
    exit_code   INTEGER,
    duration_ms INTEGER,
    session_id  TEXT,
    host        TEXT,                       -- machine name
    git_repo    TEXT,
    git_branch  TEXT,
    git_dirty   INTEGER DEFAULT 0,
    entropy     INTEGER DEFAULT 0,          -- 1 if high-entropy token flagged
    raw_output  TEXT                        -- redacted captured output (flight only)
);

CREATE INDEX IF NOT EXISTS idx_commands_ts ON commands (ts);
CREATE INDEX IF NOT EXISTS idx_commands_source ON commands (source);
CREATE INDEX IF NOT EXISTS idx_commands_git ON commands (git_repo, git_branch);
"""


def default_db_path() -> Path:
    """Platform-aware default database location."""
    if os.name == "nt":  # Windows native
        base = Path(os.environ.get("LOCALAPPDATA", Path.home() / "AppData" / "Local"))
        return base / "retrace" / "rec.db"
    # Linux / WSL
    base = Path(os.environ.get("XDG_DATA_HOME", Path.home() / ".local" / "share"))
    return base / "retrace" / "rec.db"


def connect(db_path=None) -> sqlite3.Connection:
    """Open (creating if needed) the Retrace database with locked perms."""
    path = Path(db_path) if db_path else default_db_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    if os.name != "nt":
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    conn = sqlite3.connect(str(path))
    conn.execute("PRAGMA journal_mode=WAL;")
    conn.execute("PRAGMA foreign_keys=ON;")
    conn.executescript(SCHEMA)
    conn.commit()
    return conn


def insert_command(
    conn,
    *,
    command: str,
    source: str = "history",
    shell: str = "bash",
    cwd: str | None = None,
    exit_code: int | None = None,
    duration_ms: int | None = None,
    session_id: str | None = None,
    host: str | None = None,
    git_repo: str | None = None,
    git_branch: str | None = None,
    git_dirty: int = 0,
    entropy: int = 0,
    raw_output: str | None = None,
    ts: float | None = None,
) -> str:
    """Insert one redacted command record. Returns its row id."""
    row_id = uuid.uuid4().hex[:12]
    conn.execute(
        """INSERT INTO commands
           (id, ts, source, shell, command, cwd, exit_code, duration_ms,
            session_id, host, git_repo, git_branch, git_dirty, entropy, raw_output)
           VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
        (
            row_id,
            ts if ts is not None else time.time(),
            source,
            shell,
            command,
            cwd,
            exit_code,
            duration_ms,
            session_id,
            host,
            git_repo,
            git_branch,
            git_dirty,
            entropy,
            raw_output,
        ),
    )
    conn.commit()
    return row_id


def search(conn, query: str, limit: int = 50, since: float | None = None) -> list[dict]:
    """Full-text-ish search over the commands table (LIKE on command/cwd)."""
    like = f"%{query}%"
    sql = (
        "SELECT * FROM commands WHERE command LIKE ? OR cwd LIKE ? "
        "ORDER BY ts DESC LIMIT ?"
    )
    params: list = [like, like, limit]
    if since is not None:
        sql = (
            "SELECT * FROM commands WHERE (command LIKE ? OR cwd LIKE ?) "
            "AND ts >= ? ORDER BY ts DESC LIMIT ?"
        )
        params = [like, like, since, limit]
    rows = conn.execute(sql, params).fetchall()
    cols = [d[0] for d in conn.execute("SELECT * FROM commands LIMIT 0").description]
    return [dict(zip(cols, r)) for r in rows]


def stats(conn) -> dict:
    """Quick aggregate stats for the CLI/UI."""
    total = conn.execute("SELECT COUNT(*) FROM commands").fetchone()[0]
    by_source = conn.execute(
        "SELECT source, COUNT(*) FROM commands GROUP BY source"
    ).fetchall()
    last = conn.execute("SELECT MAX(ts) FROM commands").fetchone()[0]
    return {"total": total, "by_source": dict(by_source), "last_ts": last}