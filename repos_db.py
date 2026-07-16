"""Persistencia simple de repos registrados (SQLite, sin ORM)."""

import sqlite3
from pathlib import Path

DB_PATH = Path(__file__).parent / "repos.db"


def _conn() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS repos (
            name TEXT PRIMARY KEY,
            source TEXT NOT NULL,
            local_path TEXT NOT NULL,
            cbm_project TEXT,
            status TEXT NOT NULL DEFAULT 'registrado',
            error TEXT,
            registered_at TEXT NOT NULL DEFAULT (datetime('now'))
        )
        """
    )
    return conn


def upsert(name: str, source: str, local_path: str, status: str = "registrado"):
    conn = _conn()
    conn.execute(
        """
        INSERT INTO repos (name, source, local_path, status)
        VALUES (?, ?, ?, ?)
        ON CONFLICT(name) DO UPDATE SET source=excluded.source, local_path=excluded.local_path
        """,
        (name, source, local_path, status),
    )
    conn.commit()
    conn.close()


def set_status(name: str, status: str, cbm_project: str | None = None, error: str | None = None):
    conn = _conn()
    conn.execute(
        "UPDATE repos SET status = ?, cbm_project = COALESCE(?, cbm_project), error = ? WHERE name = ?",
        (status, cbm_project, error, name),
    )
    conn.commit()
    conn.close()


def get(name: str) -> dict | None:
    conn = _conn()
    row = conn.execute(
        "SELECT name, source, local_path, cbm_project, status, error, registered_at FROM repos WHERE name = ?",
        (name,),
    ).fetchone()
    conn.close()
    if row is None:
        return None
    return _row_to_dict(row)


def list_all() -> list[dict]:
    conn = _conn()
    rows = conn.execute(
        "SELECT name, source, local_path, cbm_project, status, error, registered_at FROM repos ORDER BY registered_at DESC"
    ).fetchall()
    conn.close()
    return [_row_to_dict(r) for r in rows]


def delete(name: str):
    conn = _conn()
    conn.execute("DELETE FROM repos WHERE name = ?", (name,))
    conn.commit()
    conn.close()


def _row_to_dict(row) -> dict:
    return {
        "name": row[0],
        "source": row[1],
        "local_path": row[2],
        "cbm_project": row[3],
        "status": row[4],
        "error": row[5],
        "registered_at": row[6],
    }
