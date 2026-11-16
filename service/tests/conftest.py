"""Synthetic fixtures only: fake fields, fake master strips, made-up answer keys. No real task data in the repo."""
import io
import json
import sqlite3
import sys
from pathlib import Path

import numpy as np
import pytest
from PIL import Image

SERVICE = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(SERVICE))

from app.taskstore import TaskStore  # noqa: E402


def fake_master(n=3, tile=96, seed=0, moving=True):
    rng = np.random.default_rng(seed)
    arr = rng.normal(1000, 20, size=(tile, n * tile))
    x0, y0 = 40.0, 44.0
    track = []
    yy, xx = np.mgrid[0:tile, 0:tile]
    for i in range(n):
        x, y = (x0 + 7 * i, y0 + 5 * i) if moving else (x0, y0)
        track.append([x, y])
        arr[:, i * tile:(i + 1) * tile] += 30000 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / 2.9)
    buf = io.BytesIO()
    Image.fromarray(arr.clip(0, 65535).astype(np.uint16)).save(buf, format="PNG")
    fit = track if moving else [track[0], [x0 + 14, y0 + 10]]
    return buf.getvalue(), track, fit


def make_task(task_id, field_id, kind, holdout=False, n=3):
    png, track, fit = fake_master(n=n, seed=hash(task_id) % 1000, moving=(kind != "control_neg"))
    answer = {"control_pos": "moving", "control_neg": "not_moving"}.get(kind)
    return {"task_id": task_id, "field_id": field_id, "kind": kind, "expected_answer": answer, "holdout": holdout,
            "master_path": f"masters/{task_id}.png", "frame_size": 64, "frame_count": n, "tile_px": 96,
            "ring": track[0], "track": track, "fit_points": fit, "frame_offsets_h": [0.0, 1.6, 3.3][:n],
            "meta": {"edge_px": 8, "ring_radius_px": 4}}, png


def make_pool(n_control_fields=12, n_science=4, holdout_every=4):
    tasks, masters = [], {}
    for i in range(n_control_fields):
        h = (i % holdout_every == 0)
        for kind in ("control_pos", "control_neg"):
            t, png = make_task(f"{kind[8:]}{i:02d}", f"F{i:02d}", kind, holdout=h)
            tasks.append(t); masters[t["task_id"]] = png
    for j in range(n_science):
        t, png = make_task(f"sci{j:02d}", f"S{j:02d}", "science")
        tasks.append(t); masters[t["task_id"]] = png
    return tasks, masters


class FakeStore(TaskStore):
    def __init__(self, tasks, masters):
        self.tasks = {t["task_id"]: t for t in tasks}
        self.masters = masters

    def pool(self):
        return list(self.tasks.values())

    def get(self, task_id):
        return self.tasks.get(task_id)

    def master_bytes(self, task):
        return self.masters[task["task_id"]]

    def counts(self):
        return {"by_kind": [], "fields": {"total": 0, "with_active_tasks": 0}}


@pytest.fixture
def pool():
    return make_pool()


def write_generator_db(data_dir: Path, tasks, masters, extra_inactive=True):
    """A minimal generator-style database (same column names as generator/skytask_gen/db.py)."""
    (data_dir / "masters").mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(str(data_dir / "skytask.sqlite3"))
    con.executescript("""
      CREATE TABLE fields (field_id TEXT PRIMARY KEY, status TEXT);
      CREATE TABLE tasks (task_id TEXT PRIMARY KEY, field_id TEXT, kind TEXT, expected_answer TEXT, holdout INTEGER,
        status TEXT, master_path TEXT, frame_size INTEGER, frame_count INTEGER, tile_px INTEGER, ring TEXT,
        track TEXT, fit_points TEXT, frame_offsets_h TEXT, meta TEXT, ra REAL, dec REAL);""")
    rows = [(t, "active") for t in tasks]
    if extra_inactive:
        t, png = make_task("retired01", "R01", "control_pos")
        masters = {**masters, "retired01": png}
        rows.append((t, "retired"))
    for t, status in rows:
        con.execute("INSERT OR IGNORE INTO fields VALUES (?, 'done')", (t["field_id"],))
        con.execute("INSERT INTO tasks VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (t["task_id"], t["field_id"], t["kind"], t["expected_answer"], int(t["holdout"]), status,
                     t["master_path"], 64, t["frame_count"], 96, json.dumps(t["ring"]), json.dumps(t["track"]),
                     json.dumps(t["fit_points"]), json.dumps(t["frame_offsets_h"]), json.dumps(t["meta"]), 12.3, -4.5))
        (data_dir / t["master_path"]).write_bytes(masters[t["task_id"]])
    con.commit()
    con.close()
