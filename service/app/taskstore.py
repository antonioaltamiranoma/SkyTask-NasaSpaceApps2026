"""Where tasks come from.

TaskStore is the seam between the API and storage. LocalTaskStore reads the generator's SQLite database and
master strips READ-ONLY (it never writes to the generator's files). Card #4 adds a Firestore + private Cloud
Storage implementation with the same three methods.

A pool entry is the server-side record of one ACTIVE task (it holds answer keys: never send it to a browser).
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from pathlib import Path

JSON_COLS = ("ring", "track", "fit_points", "frame_offsets_h", "meta")
POOL_COLS = ("task_id", "field_id", "kind", "expected_answer", "holdout", "master_path", "frame_size",
             "frame_count", "tile_px", "ring", "track", "fit_points", "frame_offsets_h", "meta")


def variant_meta(task: dict) -> dict:
    """What variants.make_variant needs (same as generator store.variant_meta)."""
    m = task.get("meta") or {}
    return {"frame_count": task["frame_count"], "tile_px": task["tile_px"], "frame_size": task["frame_size"],
            "edge_px": m.get("edge_px", 8), "ring_radius_px": m.get("ring_radius_px", 4), "ring": task["ring"],
            "fit_points": task.get("fit_points") or [], "track": task.get("track") or []}


class TaskStore:
    """Interface used by the API."""

    def pool(self) -> list[dict]:
        """All ACTIVE tasks (server-side records)."""
        raise NotImplementedError

    def get(self, task_id: str) -> dict | None:
        raise NotImplementedError

    def master_bytes(self, task: dict) -> bytes:
        raise NotImplementedError

    def counts(self) -> dict:
        """Counts by kind/holdout/status and field totals (dev inspector only)."""
        raise NotImplementedError


class LocalTaskStore(TaskStore):
    def __init__(self, data_dir: Path, refresh_s: float = 30.0):
        self.data_dir = Path(data_dir)
        self.db_path = self.data_dir / "skytask.sqlite3"
        self.refresh_s = refresh_s
        self._lock = threading.Lock()
        self._pool: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._loaded_at = 0.0

    def _connect(self):
        if not self.db_path.exists():
            raise FileNotFoundError(f"generator database not found: {self.db_path} "
                                    "(set generator_data_dir in service/settings.local.toml)")
        con = sqlite3.connect(str(self.db_path), timeout=10)
        con.row_factory = sqlite3.Row
        con.execute("PRAGMA query_only = ON")  # read-only: any write raises
        return con

    @staticmethod
    def _row(r) -> dict:
        d = dict(r)
        for k in JSON_COLS:
            if isinstance(d.get(k), str):
                d[k] = json.loads(d[k])
        d["holdout"] = bool(d.get("holdout"))
        return d

    def _refresh(self, force=False):
        with self._lock:
            if not force and self._by_id and time.monotonic() - self._loaded_at < self.refresh_s:
                return
            con = self._connect()
            try:
                rows = con.execute(f"SELECT {','.join(POOL_COLS)} FROM tasks WHERE status='active'").fetchall()
            finally:
                con.close()
            pool = [self._row(r) for r in rows]
            self._pool, self._by_id = pool, {t["task_id"]: t for t in pool}
            self._loaded_at = time.monotonic()

    def pool(self):
        self._refresh()
        return list(self._pool)

    def get(self, task_id):
        self._refresh()
        t = self._by_id.get(task_id)
        if t is None:  # retired since load, or not active: look it up directly (needed to score old challenges)
            con = self._connect()
            try:
                r = con.execute(f"SELECT {','.join(POOL_COLS)} FROM tasks WHERE task_id=?", (task_id,)).fetchone()
            finally:
                con.close()
            t = self._row(r) if r else None
        return t

    def master_bytes(self, task):
        p = (self.data_dir / task["master_path"]).resolve()
        if self.data_dir.resolve() not in p.parents:
            raise ValueError("master path outside the data folder")
        return p.read_bytes()

    def counts(self):
        con = self._connect()
        try:
            rows = con.execute("SELECT kind, holdout, status, COUNT(*) AS n FROM tasks "
                               "GROUP BY kind, holdout, status ORDER BY kind, holdout, status").fetchall()
            f_all = con.execute("SELECT COUNT(*) FROM fields").fetchone()[0]
            f_act = con.execute("SELECT COUNT(DISTINCT field_id) FROM tasks WHERE status='active'").fetchone()[0]
        finally:
            con.close()
        return {"by_kind": [{"kind": r["kind"], "holdout": bool(r["holdout"]), "status": r["status"], "count": r["n"]}
                            for r in rows],
                "fields": {"total": f_all, "with_active_tasks": f_act}}


def task_to_doc(t: dict) -> dict:
    """Server record -> Firestore document (JSON columns as strings: Firestore has no nested arrays)."""
    d = {}
    for k in POOL_COLS:
        v = t.get(k)
        if k in JSON_COLS:
            v = json.dumps(v)
        elif k == "holdout":
            v = bool(v)
        d[k] = v
    return d


def doc_to_task(d: dict) -> dict:
    t = {k: d.get(k) for k in POOL_COLS}
    for k in JSON_COLS:
        if isinstance(t.get(k), str):
            t[k] = json.loads(t[k])
    t["holdout"] = bool(t.get("holdout"))
    return t


class CloudTaskStore(TaskStore):
    """Tasks in Firestore (`tasks`, server-only: holds answer keys) + master strips in the private bucket.

    The pool is re-read every `refresh_s` seconds, so tasks uploaded by the PC's sync script go live without a
    redeploy. Master strips are cached in memory (they never change: a new strip gets a new task id)."""

    def __init__(self, client, refresh_s: float = 60.0, cache_max: int = 2000):
        self.c, self.refresh_s, self.cache_max = client, refresh_s, cache_max
        self._lock = threading.Lock()
        self._pool: list[dict] = []
        self._by_id: dict[str, dict] = {}
        self._loaded_at = 0.0
        self._png: dict[str, bytes] = {}

    def _refresh(self, force=False):
        with self._lock:
            if not force and self._loaded_at and time.monotonic() - self._loaded_at < self.refresh_s:
                return
            try:
                docs = self.c.query("tasks", where={"status": "active"})
            except Exception:  # keep serving the last good pool if Firestore hiccups
                if self._loaded_at:
                    self._loaded_at = time.monotonic() - self.refresh_s + 10
                    return
                raise
            pool = [doc_to_task(d) for d in docs]
            self._pool, self._by_id = pool, {t["task_id"]: t for t in pool}
            self._loaded_at = time.monotonic()

    def pool(self):
        self._refresh()
        return list(self._pool)

    def get(self, task_id):
        self._refresh()
        t = self._by_id.get(task_id)
        if t is None:  # retired since load: look it up directly (needed to grade older challenges)
            d = self.c.get("tasks", task_id)
            t = doc_to_task(d) if d else None
        return t

    def master_bytes(self, task):
        key = task["master_path"].replace("\\", "/")
        b = self._png.get(key)
        if b is None:
            b = self.c.get_object(key)
            with self._lock:
                if len(self._png) >= self.cache_max:
                    self._png.pop(next(iter(self._png)))
                self._png[key] = b
        return b

    def counts(self):
        by = {}
        for t in self.pool():
            k = (t["kind"], bool(t["holdout"]))
            by[k] = by.get(k, 0) + 1
        return {"by_kind": [{"kind": k, "holdout": h, "status": "active", "count": n} for (k, h), n in sorted(by.items())],
                "fields": {"with_active_tasks": len({t["field_id"] for t in self.pool()})}}
