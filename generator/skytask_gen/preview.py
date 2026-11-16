"""`python -m skytask_gen preview`: review sheet WITH ANSWERS (never serve it). For each recent task: the master
strip (stretched, ring in red, real track in green) and N request-time variants with the returned highlight ring."""
from __future__ import annotations
import time
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw

from .db import DB
from .store import make_store, variant_meta
from .variants import load_master, make_variant_debug, robust_stretch

SCALE = 3


def _stretch_master(arr, n, tile):
    if arr.dtype == np.uint8:
        return arr
    out = np.zeros(arr.shape, np.uint8)
    for i in range(n):
        t = arr[:, i * tile:(i + 1) * tile].astype(float)
        out[:, i * tile:(i + 1) * tile] = (robust_stretch(t) * 255).astype(np.uint8)
    return out


def _ring(d, x, y, r, off_x, color):
    cx, cy = (off_x + x + 0.5) * SCALE, (y + 0.5) * SCALE
    d.ellipse([cx - r * SCALE, cy - r * SCALE, cx + r * SCALE, cy + r * SCALE], outline=color, width=2)


def preview(cfg, n_tasks=12, n_variants=3, out=None, seed=0):
    db = DB(cfg["paths"]["db"]); store = make_store(cfg, db)
    rows = db.q("SELECT * FROM tasks ORDER BY legacy, created_at DESC LIMIT ?", (n_tasks,))
    rng = np.random.default_rng(seed)
    panels = []
    for r in rows:
        meta = variant_meta(r)
        raw = store.master_bytes(r)
        arr = load_master(raw)
        n, T = r["frame_count"], r["tile_px"]
        m8 = _stretch_master(arr, n, T)
        im = Image.fromarray(m8).convert("RGB").resize((m8.shape[1] * SCALE, m8.shape[0] * SCALE), Image.NEAREST)
        d = ImageDraw.Draw(im)
        for i in range(n):
            _ring(d, r["ring"][0], r["ring"][1], 4, i * T, (255, 60, 60))
        for p in r["track"] or []:
            if len(p) >= 3:
                _ring(d, p[0], p[1], 2, int(p[2]) * T, (60, 255, 60))
        rows_img = [im]
        for _ in range(n_variants):
            png, hl, dbg = make_variant_debug(raw, meta, rng)
            v = Image.open(__import__("io").BytesIO(png)).convert("RGB")
            v = v.resize((v.width * SCALE, v.height * SCALE), Image.NEAREST)
            dv = ImageDraw.Draw(v)
            for i in range(n):
                _ring(dv, hl["x"], hl["y"], hl["r"], i * 64, (255, 60, 60))
            rows_img.append(v)
        W = max(x.width for x in rows_img)
        H = sum(x.height + 4 for x in rows_img) + 18
        panel = Image.new("RGB", (W, H), (30, 30, 30))
        ImageDraw.Draw(panel).text((4, 3), f"{r['task_id']} {r['kind']} {r['expected_answer'] or r['confidence']}"
                                           f"{' holdout' if r['holdout'] else ''} {r['status']}{' legacy' if r['legacy'] else ''}"
                                           f" {r['designation'] or ''}", fill=(255, 255, 255))
        y = 18
        for x in rows_img:
            panel.paste(x, (0, y)); y += x.height + 4
        panels.append(panel)
    if not panels:
        return None
    W = max(p.width for p in panels)
    sheet = Image.new("RGB", (W, sum(p.height + 8 for p in panels)), (0, 0, 0))
    y = 0
    for p in panels:
        sheet.paste(p, (0, y)); y += p.height + 8
    out = Path(out) if out else Path(cfg["paths"]["data"]) / "review" / f"preview_{time.strftime('%Y%m%d-%H%M%S')}.png"
    out.parent.mkdir(parents=True, exist_ok=True)
    sheet.save(out)
    db.close()
    return out
