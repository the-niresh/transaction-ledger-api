import os
import sqlite3
from pathlib import Path

SCHEMA = (Path(__file__).parent / "schema.sql").read_text()


def database_path() -> str:
    # On Vercel only /tmp is writable, and it is wiped when an instance restarts.
    default = "/tmp/ledger.db" if os.environ.get("VERCEL") else "ledger.db"
    return os.environ.get("LEDGER_DB", default)


def connect(path: str) -> sqlite3.Connection:
    # isolation_level=None: we open every transaction ourselves with BEGIN IMMEDIATE.
    conn = sqlite3.connect(path, isolation_level=None, check_same_thread=False, timeout=10)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA busy_timeout = 10000")
    return conn


def init(path: str) -> None:
    conn = connect(path)
    try:
        conn.execute("PRAGMA journal_mode = WAL")
        conn.executescript(SCHEMA)
    finally:
        conn.close()
