"""Export ACTIVE tasks as static files for a simple service (same contract as pipeline-prep 05_export.py):
  - Moving / Not-moving controls balanced 50/50 within each split (holdout / training); science tasks all
  - global shuffle; one rendered variant per task (random window + rotation/flip) in strips/<random>.png
  - public/tasks_public.jsonl + public/strips/: the ONLY browser-visible data (no RA/Dec, no absolute times,
    no field id, no answers)
  - server/tasks_server.jsonl: everything the server needs (answer keys, holdout, master coordinates ...)
  - server/review_keys.jsonl: track positions in the exported strip (review only, never served)"""
from __future__ import annotations
import json
import time
import uuid
from collections import Counter
from pathlib import Path

import numpy as np

from .db import DB
from .store import make_store, public_record, variant_meta
from .variants import make_variant_debug


def export(cfg, out_dir=None, seed=None):
    db = DB(cfg["paths"]["db"])
    store = make_store(cfg, db)
    rng = np.random.default_rng(seed)
    out = Path(out_dir) if out_dir else Path(cfg["paths"]["exports"]) / time.strftime("%Y%m%d-%H%M%S")
    pub_dir, srv_dir = out / "public", out / "server"   # never side by side: upload public/ only
    (pub_dir / "strips").mkdir(parents=True, exist_ok=True)
    srv_dir.mkdir(parents=True, exist_ok=True)
    rows = db.q("SELECT * FROM tasks WHERE status='active'")
    keep = []
    for split in (1, 0):
        pos = [r for r in rows if r["kind"] == "control_pos" and r["holdout"] == split]
        neg = [r for r in rows if r["kind"] == "control_neg" and r["holdout"] == split]
        n = min(len(pos), len(neg))
        keep += [pos[i] for i in rng.permutation(len(pos))[:n]] + [neg[i] for i in rng.permutation(len(neg))[:n]]
    keep += [r for r in rows if r["kind"] == "science"]
    keep = [keep[i] for i in rng.permutation(len(keep))]
    pub, srv, rev = [], [], []
    for r in keep:
        png, hl, dbg = make_variant_debug(store.master_bytes(r), variant_meta(r), rng)
        fname = f"strips/{uuid.uuid4().hex}.png"
        (pub_dir / fname).write_bytes(png)
        pub.append({**public_record(r), "strip_file": fname, "highlight": hl})
        s = {k: v for k, v in r.items() if k not in ("track",)}
        srv.append({**s, "strip_file": fname, "highlight": hl})
        rev.append({"task_id": r["task_id"], "review_track_xy": [[round(float(x), 1), round(float(y), 1)] for x, y in dbg["track"]]})
    for d_, fn, data in ((pub_dir, "tasks_public.jsonl", pub), (srv_dir, "tasks_server.jsonl", srv),
                         (srv_dir, "review_keys.jsonl", rev)):
        with open(d_ / fn, "w", encoding="utf-8") as f:
            for d in data:
                f.write(json.dumps(d, default=float) + "\n")
    c = Counter((r["kind"], bool(r["holdout"])) for r in keep)
    summary = {f"{k[0]}{' (holdout)' if k[1] else ''}": v for k, v in sorted(c.items())}
    db.close()
    return out, summary
