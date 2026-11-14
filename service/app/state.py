"""The service's own state (local SQLite now; Firestore with card #4). Never the generator's database.

Tables
  sessions         one anonymous participant per session (site key, reliability alpha/beta)
  session_fields   fields already served to a session (one task per field per participant)
  challenges       mode, timestamps, submitted flag
  challenge_tasks  task_ref (opaque, per challenge) -> task_id, plus the variant seed and served highlight
  answers          one row per submitted answer, with server-side elapsed time
  consensus        per science task: score, weighted votes, status (open/confirmed/rejected) - filled by card #6
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

ALPHA0, BETA0 = 3.0, 1.0  # CLAUDE.md: prior alpha=3, beta=1

SCHEMA = """
CREATE TABLE IF NOT EXISTS sessions (
  session_id TEXT PRIMARY KEY, site_key TEXT, alpha REAL, beta REAL, created_at REAL);
CREATE TABLE IF NOT EXISTS session_fields (
  session_id TEXT, field_id TEXT, PRIMARY KEY (session_id, field_id));
CREATE TABLE IF NOT EXISTS challenges (
  challenge_id TEXT PRIMARY KEY, session_id TEXT, mode TEXT, created_at REAL, expires_at REAL,
  submitted_at REAL, result TEXT);
CREATE TABLE IF NOT EXISTS challenge_tasks (
  challenge_id TEXT, task_ref TEXT, position INTEGER, task_id TEXT, settled TEXT, seed INTEGER,
  highlight TEXT, variant TEXT, PRIMARY KEY (challenge_id, task_ref));
CREATE TABLE IF NOT EXISTS answers (
  challenge_id TEXT, task_ref TEXT, task_id TEXT, session_id TEXT, choice TEXT, correct INTEGER,
  counts_for_reliability INTEGER, elapsed_ms INTEGER, created_at REAL);
CREATE INDEX IF NOT EXISTS answers_task ON answers(task_id);
CREATE TABLE IF NOT EXISTS consensus (
  task_id TEXT PRIMARY KEY, score REAL, weighted_votes REAL, status TEXT, updated_at REAL);
"""


class State:
    def __init__(self, path: Path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(str(self.path), check_same_thread=False, timeout=30)
        self.con.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.con.executescript(SCHEMA)
            self.con.commit()

    def q(self, sql, args=()):
        with self.lock:
            return [dict(r) for r in self.con.execute(sql, args).fetchall()]

    def one(self, sql, args=()):
        rows = self.q(sql, args)
        return rows[0] if rows else None

    def x(self, sql, args=()):
        with self.lock:
            cur = self.con.execute(sql, args)
            self.con.commit()
            return cur

    # ---- sessions
    def add_session(self, session_id, site_key):
        self.x("INSERT INTO sessions VALUES (?,?,?,?,?)", (session_id, site_key, ALPHA0, BETA0, time.time()))

    def session(self, session_id):
        return self.one("SELECT * FROM sessions WHERE session_id=?", (session_id,))

    def seen_fields(self, session_id) -> set:
        return {r["field_id"] for r in self.q("SELECT field_id FROM session_fields WHERE session_id=?", (session_id,))}

    # ---- consensus (read here; written by the batch job of card #6)
    def consensus(self) -> dict:
        return {r["task_id"]: r for r in self.q("SELECT * FROM consensus")}

    def votes(self, task_id) -> int:
        return self.one("SELECT COUNT(*) AS n FROM answers WHERE task_id=?", (task_id,))["n"]

    # ---- challenges
    def save_challenge(self, challenge_id, session_id, mode, ttl_s, items, field_ids):
        now = time.time()
        with self.lock:
            self.con.execute("INSERT INTO challenges VALUES (?,?,?,?,?,?,?)",
                             (challenge_id, session_id, mode, now, now + ttl_s, None, None))
            for it in items:
                self.con.execute("INSERT INTO challenge_tasks VALUES (?,?,?,?,?,?,?,?)",
                                 (challenge_id, it["task_ref"], it["position"], it["task_id"], it["settled"],
                                  it["seed"], json.dumps(it["highlight"]), json.dumps(it["variant"])))
            for f in field_ids:
                self.con.execute("INSERT OR IGNORE INTO session_fields VALUES (?,?)", (session_id, f))
            self.con.commit()
        return now + ttl_s

    def challenge(self, challenge_id):
        return self.one("SELECT * FROM challenges WHERE challenge_id=?", (challenge_id,))

    def challenge_tasks(self, challenge_id):
        rows = self.q("SELECT * FROM challenge_tasks WHERE challenge_id=? ORDER BY position", (challenge_id,))
        for r in rows:
            r["highlight"] = json.loads(r["highlight"])
            r["variant"] = json.loads(r["variant"])
        return rows

    def record_submission(self, challenge, answer_rows, rel_delta, result):
        """One transaction: answers, reliability update, challenge marked submitted. False if already submitted."""
        now = time.time()
        with self.lock:
            cur = self.con.execute("UPDATE challenges SET submitted_at=?, result=? WHERE challenge_id=? AND "
                                   "submitted_at IS NULL", (now, json.dumps(result), challenge["challenge_id"]))
            if cur.rowcount != 1:
                self.con.rollback()
                return False
            for a in answer_rows:
                self.con.execute("INSERT INTO answers VALUES (?,?,?,?,?,?,?,?,?)",
                                 (challenge["challenge_id"], a["task_ref"], a["task_id"], challenge["session_id"],
                                  a["choice"], a["correct"], a["counts_for_reliability"], a["elapsed_ms"], now))
            da, db = rel_delta
            if da or db:
                self.con.execute("UPDATE sessions SET alpha=alpha+?, beta=beta+? WHERE session_id=?",
                                 (da, db, challenge["session_id"]))
            self.con.commit()
        return True
