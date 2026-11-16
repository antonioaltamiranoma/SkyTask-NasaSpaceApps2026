"""SQLite task database (stdlib sqlite3). One file: <data_dir>/skytask.sqlite3.

Tables
  fields          one row per field (survey position or MOST sequence); status queued/processing/done/failed
  frames          the exposures chosen for a field (one per visit)
  detections      per-field detection + linking result (JSON blob)
  candidates      every tracklet / 2-frame pair found, with its label and what became of it
  tasks           controls and science tasks (server-side record; public fields are a subset)
  holdout_groups  holdout decision per asteroid (or per field for star-only fields)
  asteroids       asteroids already used for MOST top-up / fallback fields
  sync_queue      upload/delete actions for a future cloud uploader (Firebase / Cloud Storage)
  kv              small counters (e.g. fields since last top-up)
"""
from __future__ import annotations
import json
import sqlite3
import time
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS fields (
  field_id TEXT PRIMARY KEY, source TEXT NOT NULL, ra REAL, dec REAL, size_deg REAL,
  params TEXT, status TEXT NOT NULL DEFAULT 'queued', outcome TEXT, reason TEXT, attempts INTEGER DEFAULT 0,
  n_frames INTEGER, n_tracklets INTEGER, n_pairs INTEGER, jpl_calls INTEGER, n_tasks INTEGER,
  seconds REAL, created_at REAL, updated_at REAL, refunds INTEGER DEFAULT 0);
CREATE INDEX IF NOT EXISTS fields_status ON fields(status);
CREATE TABLE IF NOT EXISTS frames (
  field_id TEXT, idx INTEGER, image_id TEXT, image_url TEXT, mjd REAL, detector TEXT, coverage REAL,
  PRIMARY KEY (field_id, idx));
CREATE TABLE IF NOT EXISTS detections (field_id TEXT PRIMARY KEY, data TEXT);
CREATE TABLE IF NOT EXISTS candidates (
  cand_id INTEGER PRIMARY KEY AUTOINCREMENT, field_id TEXT, ctype TEXT, n_frames INTEGER, known TEXT,
  rate_deg_day REAL, fit_rms_px REAL, decision TEXT, task_id TEXT, data TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS tasks (
  task_id TEXT PRIMARY KEY, field_id TEXT, kind TEXT NOT NULL, expected_answer TEXT, confidence TEXT,
  holdout INTEGER DEFAULT 0, group_key TEXT, status TEXT NOT NULL, legacy INTEGER DEFAULT 0,
  designation TEXT, master_path TEXT, frame_size INTEGER, frame_count INTEGER, tile_px INTEGER, margin_px INTEGER,
  ring TEXT, track TEXT, fit_points TEXT, frame_offsets_h TEXT, times_mjd TEXT, ra REAL, dec REAL,
  flux REAL, detector TEXT, meta TEXT, created_at REAL, retired_at REAL);
CREATE INDEX IF NOT EXISTS tasks_kind ON tasks(kind, status, holdout);
CREATE TABLE IF NOT EXISTS holdout_groups (group_key TEXT PRIMARY KEY, holdout INTEGER);
CREATE TABLE IF NOT EXISTS asteroids (number INTEGER PRIMARY KEY, purpose TEXT, n_fields INTEGER, note TEXT, created_at REAL);
CREATE TABLE IF NOT EXISTS sync_queue (
  id INTEGER PRIMARY KEY AUTOINCREMENT, task_id TEXT, action TEXT, state TEXT DEFAULT 'pending',
  error TEXT, created_at REAL, updated_at REAL);
CREATE INDEX IF NOT EXISTS sync_state ON sync_queue(state);
CREATE TABLE IF NOT EXISTS kv (key TEXT PRIMARY KEY, value TEXT);
"""

JSON_COLS = {"params", "ring", "track", "fit_points", "frame_offsets_h", "times_mjd", "meta", "data"}


class DB:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.con = sqlite3.connect(str(self.path), timeout=30)
        self.con.row_factory = sqlite3.Row
        try:  # WAL: readers (status/export) never block the running generator
            self.con.execute("PRAGMA journal_mode=WAL")
        except sqlite3.DatabaseError:
            pass
        self.con.executescript(SCHEMA)
        cols = {r[1] for r in self.con.execute("PRAGMA table_info(fields)")}
        if "refunds" not in cols:  # migration for databases created by v0.1
            self.con.execute("ALTER TABLE fields ADD COLUMN refunds INTEGER DEFAULT 0")
        self.con.commit()

    # ---- generic helpers
    def q(self, sql, args=()):
        return [self._row(r) for r in self.con.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        r = self.con.execute(sql, args).fetchone()
        return self._row(r) if r else None

    def x(self, sql, args=()):
        cur = self.con.execute(sql, args)
        self.con.commit()
        return cur

    @staticmethod
    def _row(r):
        d = dict(r)
        for k in JSON_COLS & d.keys():
            if isinstance(d[k], str):
                try:
                    d[k] = json.loads(d[k])
                except ValueError:
                    pass
        return d

    def insert(self, table: str, row: dict, replace=False):
        row = {k: (json.dumps(v, default=float) if k in JSON_COLS and v is not None and not isinstance(v, str) else v)
               for k, v in row.items()}
        cols = ",".join(row)
        qs = ",".join("?" * len(row))
        verb = "INSERT OR REPLACE" if replace else "INSERT"
        return self.x(f"{verb} INTO {table} ({cols}) VALUES ({qs})", tuple(row.values()))

    # ---- kv
    def get(self, key, default=None):
        r = self.one("SELECT value FROM kv WHERE key=?", (key,))
        return json.loads(r["value"]) if r else default

    def set(self, key, value):
        self.x("INSERT OR REPLACE INTO kv (key, value) VALUES (?, ?)", (key, json.dumps(value)))

    # ---- fields
    def add_field(self, field_id, source, ra, dec, size_deg, params):
        now = time.time()
        self.insert("fields", {"field_id": field_id, "source": source, "ra": ra, "dec": dec, "size_deg": size_deg,
                               "params": params, "status": "queued", "created_at": now, "updated_at": now},
                    replace=False)

    def field_exists(self, field_id):
        return self.one("SELECT 1 AS x FROM fields WHERE field_id=?", (field_id,)) is not None

    def update_field(self, field_id, **kw):
        kw["updated_at"] = time.time()
        sets = ",".join(f"{k}=?" for k in kw)
        self.x(f"UPDATE fields SET {sets} WHERE field_id=?", (*kw.values(), field_id))

    def reset_stale(self, max_attempts=3, max_refunds=3):
        """Fields left 'processing' by a hard stop go back to the queue and get their attempt back, at most
        max_refunds times (a field that crashes Python every time must not loop forever: it is marked failed)."""
        self.x("UPDATE fields SET status='failed', outcome='error', reason='interrupted too often (crash?)', "
               "attempts=? WHERE status='processing' AND COALESCE(refunds,0) >= ?", (max_attempts, max_refunds))
        return self.x("UPDATE fields SET status='queued', attempts=MAX(COALESCE(attempts,1)-1,0), "
                      "refunds=COALESCE(refunds,0)+1 WHERE status='processing'").rowcount

    def next_queued(self, max_attempts):
        return self.one("SELECT * FROM fields WHERE status='queued' AND attempts < ? ORDER BY created_at LIMIT 1",
                        (max_attempts,))

    # ---- holdout groups
    def group_holdout(self, key):
        r = self.one("SELECT holdout FROM holdout_groups WHERE group_key=?", (key,))
        return None if r is None else bool(r["holdout"])

    def set_group_holdout(self, key, holdout: bool):
        self.x("INSERT OR IGNORE INTO holdout_groups (group_key, holdout) VALUES (?, ?)", (key, int(holdout)))

    # ---- pool counts
    def count_tasks(self, kind, status="active", holdout=None):
        sql, args = "SELECT COUNT(*) AS n FROM tasks WHERE kind=? AND status=?", [kind, status]
        if holdout is not None:
            sql += " AND holdout=?"; args.append(int(holdout))
        return self.one(sql, args)["n"]

    def close(self):
        self.con.close()
