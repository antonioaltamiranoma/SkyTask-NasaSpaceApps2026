"""Card #4: sync_tasks.one_pass against an in-memory cloud (no network)."""
import json
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO / "service"))
sys.path.insert(0, str(REPO / "service" / "tests"))
sys.path.insert(0, str(REPO / "deploy"))
from fake_cloud import FakeCloud  # noqa: E402
import sync_tasks  # noqa: E402
from app.taskstore import POOL_COLS  # noqa: E402


def _db(d: Path, ids, status="active"):
    (d / "masters").mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(d / "skytask.sqlite3")
    c.execute(f"CREATE TABLE IF NOT EXISTS tasks({','.join(POOL_COLS)}, status)")
    c.execute("DELETE FROM tasks")
    for tid in ids:
        row = {"task_id": tid, "field_id": "f-" + tid, "kind": "control_pos", "expected_answer": "moving", "holdout": 0,
               "master_path": f"masters\\{tid}.png", "frame_size": 64, "frame_count": 3, "tile_px": 96,
               "ring": json.dumps({"x": 1, "y": 2, "r": 4}), "track": "[[1,2]]", "fit_points": "[]",
               "frame_offsets_h": "[0,1,2]", "meta": "{}"}
        c.execute(f"INSERT INTO tasks VALUES ({','.join('?' * (len(POOL_COLS) + 1))})",
                  [row[k] for k in POOL_COLS] + [status])
        (d / "masters" / f"{tid}.png").write_bytes(b"png" + tid.encode())
    c.commit(); c.close()
    return d / "skytask.sqlite3"


def test_sync_uploads_once_and_retires_safely(tmp_path):
    c = FakeCloud()
    db = _db(tmp_path / "data", [f"t{i}" for i in range(10)])
    msg = sync_tasks.one_pass(c, [db], allow_empty=False)
    assert "uploaded 10" in msg and len(c.objects) == 10
    assert c.cols["tasks"]["t0"]["master_path"] == "masters/t0.png"   # Windows path normalised, object per task id
    assert "uploaded 0" in sync_tasks.one_pass(c, [db], allow_empty=False)   # idempotent

    _db(tmp_path / "data", [f"t{i}" for i in range(9)])   # generator dropped one task
    assert "retired 1" in sync_tasks.one_pass(c, [db], allow_empty=False)
    assert c.cols["tasks"]["t9"]["status"] == "retired"

    _db(tmp_path / "data", ["t0"])                          # looks like a broken/missing database
    msg = sync_tasks.one_pass(c, [db], allow_empty=False)
    assert "refusing" in msg
    assert sum(1 for d in c.cols["tasks"].values() if d["status"] == "active") == 9


def test_sample_db_dropped_once_generator_pool_is_full(tmp_path, monkeypatch):
    data = _db(tmp_path / "gen" / "data", [f"d{i}" for i in range(3)])
    sample = _db(tmp_path / "gen" / "sample_data", ["s0"])
    monkeypatch.setattr(sync_tasks, "DATA_DB", data)
    monkeypatch.setattr(sync_tasks, "SAMPLE_DB", sample)
    assert sync_tasks.default_dbs() == [data, sample]
    monkeypatch.setattr(sync_tasks, "FULL_CONTROL_POOL", 3)
    assert sync_tasks.default_dbs() == [data]
