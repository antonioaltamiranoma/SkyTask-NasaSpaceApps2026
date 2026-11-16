"""Task storage backends.

Public vs server-only split (architecture doc, pipeline-prep 05_export.py):
  PUBLIC (browser-visible): task_id, frame_count, frame_size, frame_offsets_h, plus per request the variant PNG and
           its highlight {x, y, r}. Never RA/Dec, absolute times, field ids, image ids, designations or answers.
  SERVER-ONLY: everything else (kind, expected_answer, holdout, group_key, ring/track/fit_points in master
           coordinates, master strip, field_id, ra/dec, times_mjd, designation, flux, meta).
"""
from __future__ import annotations
import time
from pathlib import Path

PUBLIC_FIELDS = ("task_id", "frame_count", "frame_size", "frame_offsets_h")


def public_record(task: dict) -> dict:
    return {k: task[k] for k in PUBLIC_FIELDS}


def variant_meta(task: dict) -> dict:
    """What variants.make_variant needs (server-only)."""
    m = task.get("meta") or {}
    return {"frame_count": task["frame_count"], "tile_px": task["tile_px"], "frame_size": task["frame_size"],
            "edge_px": m.get("edge_px", 8), "ring_radius_px": m.get("ring_radius_px", 4), "ring": task["ring"],
            "fit_points": task.get("fit_points") or [], "track": task.get("track") or []}


class Store:
    """Interface. The generator only calls these methods."""

    def save_task(self, task: dict, master_png: bytes) -> None:
        raise NotImplementedError

    def set_status(self, task_id: str, status: str) -> None:
        """status: active | reserve | retired"""
        raise NotImplementedError

    def master_bytes(self, task: dict) -> bytes:
        raise NotImplementedError

    def purge_field(self, field_id: str) -> int:
        """Before (re)processing a field: drop its tasks that were never uploaded; retire uploaded ones."""
        raise NotImplementedError


class LocalStore(Store):
    """Masters in <data_dir>/masters/<task_id>.png, records in SQLite. Every task that becomes ACTIVE gets an
    'upload' entry in sync_queue, every retired task a 'delete' entry: a future uploader drains that queue."""

    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db
        self.data = Path(cfg["paths"]["data"])
        self.masters = Path(cfg["paths"]["masters"])

    def save_task(self, task, master_png):
        rel = f"masters/{task['task_id']}.png"
        tmp = self.data / (rel + ".part")
        tmp.write_bytes(master_png)
        tmp.replace(self.data / rel)
        task["master_path"] = rel
        self.db.insert("tasks", task)
        if task["status"] == "active":
            self._enqueue(task["task_id"], "upload")

    def set_status(self, task_id, status):
        row = self.db.one("SELECT status FROM tasks WHERE task_id=?", (task_id,))
        if row is None:
            raise KeyError(task_id)
        old = row["status"]
        if old == status:
            return
        now = time.time()
        self.db.x("UPDATE tasks SET status=?, retired_at=? WHERE task_id=?",
                  (status, now if status == "retired" else None, task_id))
        if status == "active":
            self._enqueue(task_id, "upload")
        elif old == "active":
            self._enqueue(task_id, "delete")

    def master_bytes(self, task):
        return (self.data / task["master_path"]).read_bytes()

    def _uploaded(self, task_id):
        return self.db.one("SELECT 1 AS x FROM sync_queue WHERE task_id=? AND action='upload' AND state='done'",
                           (task_id,)) is not None

    def purge_field(self, field_id):
        n = 0
        for t in self.db.q("SELECT task_id, master_path FROM tasks WHERE field_id=? AND status != 'retired'", (field_id,)):
            if self._uploaded(t["task_id"]):
                self.set_status(t["task_id"], "retired")  # already public: retire (queues a delete)
            else:
                self.db.x("DELETE FROM sync_queue WHERE task_id=?", (t["task_id"],))
                self.db.x("DELETE FROM tasks WHERE task_id=?", (t["task_id"],))
                try:
                    (self.data / t["master_path"]).unlink(missing_ok=True)
                except OSError:
                    pass
            n += 1
        for table in ("candidates", "detections", "frames"):
            self.db.x(f"DELETE FROM {table} WHERE field_id=?", (field_id,))
        return n

    def _enqueue(self, task_id, action):
        now = time.time()
        # an upload still pending for a task that is now deleted is simply superseded
        n = self.db.x("UPDATE sync_queue SET state='superseded', updated_at=? WHERE task_id=? AND state='pending'",
                      (now, task_id)).rowcount
        if action == "delete" and not self._uploaded(task_id):
            return  # never uploaded: nothing to delete remotely
        self.db.insert("sync_queue", {"task_id": task_id, "action": action, "state": "pending",
                                      "created_at": now, "updated_at": now})


class FirebaseStore(Store):
    """STUB for the event build. Intended mapping (architecture doc "Data model"):

    Cloud Storage (private bucket, service account only):
        masters/<task_id>.png          the 16-bit master strip; the service calls variants.make_variant per
                                       request and returns only the cropped 8-bit PNG + highlight.
    Firestore:
        tasks/<task_id>                PUBLIC subset: public_record(task) + {"active": bool}. Readable by clients.
        answer_keys/<task_id>          SERVER-ONLY (security rules deny all client reads): kind, expected_answer,
                                       holdout, group_key, field_id, confidence, designation, variant_meta(task).
        fields/<field_id>              SERVER-ONLY: ra/dec, times, image ids (for the explorer / follow-up).
    The uploader drains the local sync_queue: 'upload' -> write the three records + master; 'delete' -> set
    tasks/<id>.active=false and remove the master (keep answer_keys for scoring history). Mark rows done/failed.
    Credentials: a service-account JSON path from an environment variable, never in settings.toml or git.
    """

    def __init__(self, cfg, db):
        self.cfg, self.db = cfg, db

    def save_task(self, task, master_png):
        raise NotImplementedError("FirebaseStore is a stub: use store_backend = 'local' and a separate uploader")

    def set_status(self, task_id, status):
        raise NotImplementedError("FirebaseStore is a stub")

    def master_bytes(self, task):
        raise NotImplementedError("FirebaseStore is a stub")

    def purge_field(self, field_id):
        raise NotImplementedError("FirebaseStore is a stub")


def make_store(cfg, db) -> Store:
    backend = cfg["general"]["store_backend"]
    if backend == "local":
        return LocalStore(cfg, db)
    if backend == "firebase":
        return FirebaseStore(cfg, db)
    raise ValueError(f"unknown store_backend {backend!r}")
