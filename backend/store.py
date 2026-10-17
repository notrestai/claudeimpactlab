"""SQLite storage. The SST JSON is the record; every save is a new version."""
from __future__ import annotations
import json, pathlib, sqlite3, threading
from . import sstlib

DB_PATH = sstlib.ROOT / "data" / "sst.sqlite"
_lock = threading.Lock()


def _conn():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(DB_PATH, check_same_thread=False)
    c.row_factory = sqlite3.Row
    return c


def init_db():
    with _conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS referrals (
          id TEXT PRIMARY KEY, created_at TEXT, updated_at TEXT, packet_path TEXT, page_count INTEGER, status TEXT, label TEXT);
        CREATE TABLE IF NOT EXISTS sst_versions (
          referral_id TEXT, version INTEGER, json TEXT, updated_at TEXT, updated_by_role TEXT, note TEXT,
          PRIMARY KEY (referral_id, version));
        CREATE TABLE IF NOT EXISTS runs (
          id INTEGER PRIMARY KEY AUTOINCREMENT, referral_id TEXT, stage TEXT, started_at TEXT, ended_at TEXT, model TEXT,
          input_tokens INTEGER, cache_read_tokens INTEGER, cache_write_tokens INTEGER, output_tokens INTEGER, ok INTEGER, error TEXT, detail TEXT);
        """)


def create_referral(sst: dict, packet_path: str | None, label: str | None = None) -> str:
    rid = sst["meta"]["referral_id"]
    with _lock, _conn() as c:
        c.execute("INSERT INTO referrals (id, created_at, updated_at, packet_path, page_count, status, label) VALUES (?,?,?,?,?,?,?)",
                  (rid, sst["meta"]["created_at"], sst["meta"]["updated_at"], packet_path, None, "created", label))
        c.execute("INSERT INTO sst_versions VALUES (?,?,?,?,?,?)", (rid, 1, json.dumps(sst), sst["meta"]["updated_at"], "system", "created"))
    return rid


def list_referrals() -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT r.*, (SELECT MAX(version) FROM sst_versions v WHERE v.referral_id = r.id) AS version FROM referrals r ORDER BY created_at DESC").fetchall()
    return [dict(r) for r in rows]


def get_referral(rid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT * FROM referrals WHERE id = ?", (rid,)).fetchone()
    return dict(r) if r else None


def update_referral(rid: str, **fields):
    if not fields:
        return
    cols = ", ".join(f"{k} = ?" for k in fields)
    with _lock, _conn() as c:
        c.execute(f"UPDATE referrals SET {cols} WHERE id = ?", (*fields.values(), rid))


def load_sst(rid: str) -> dict | None:
    with _conn() as c:
        r = c.execute("SELECT json FROM sst_versions WHERE referral_id = ? ORDER BY version DESC LIMIT 1", (rid,)).fetchone()
    return json.loads(r["json"]) if r else None


def save_sst(rid: str, sst: dict, by_role: str, note: str | None = None) -> int:
    errors = sstlib.validate(sst)
    if errors:
        raise ValueError("SST failed validation: " + "; ".join(errors[:5]))
    with _lock, _conn() as c:
        v = c.execute("SELECT COALESCE(MAX(version), 0) + 1 FROM sst_versions WHERE referral_id = ?", (rid,)).fetchone()[0]
        c.execute("INSERT INTO sst_versions VALUES (?,?,?,?,?,?)", (rid, v, json.dumps(sst), sst["meta"]["updated_at"], by_role, note))
        c.execute("UPDATE referrals SET updated_at = ? WHERE id = ?", (sst["meta"]["updated_at"], rid))
    return v


def add_run(rid: str, stage: str, started_at: str, ended_at: str, model: str | None, usage: dict | None, ok: bool,
            error: str | None = None, detail: str | None = None):
    u = usage or {}
    with _lock, _conn() as c:
        c.execute("INSERT INTO runs (referral_id, stage, started_at, ended_at, model, input_tokens, cache_read_tokens, cache_write_tokens, output_tokens, ok, error, detail) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                  (rid, stage, started_at, ended_at, model, u.get("input_tokens"), u.get("cache_read_input_tokens"),
                   u.get("cache_creation_input_tokens"), u.get("output_tokens"), 1 if ok else 0, error, detail))


def list_runs(rid: str) -> list[dict]:
    with _conn() as c:
        rows = c.execute("SELECT * FROM runs WHERE referral_id = ? ORDER BY id", (rid,)).fetchall()
    return [dict(r) for r in rows]
