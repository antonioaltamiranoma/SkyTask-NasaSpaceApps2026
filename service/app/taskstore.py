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
