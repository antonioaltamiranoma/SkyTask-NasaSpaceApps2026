"""Request-time task variants from a master strip.

Pure numpy + Pillow: no astronomy dependencies, so the cloud service can copy/import this file alone.

A master strip = frame_count tiles side by side, each tile_px x tile_px (window + 2*margin), north up,
background-subtracted, stored as 16-bit linear PNG (new tasks) or 8-bit already-stretched PNG (legacy tasks).
meta (server-only) holds, in TILE pixel coordinates (x right, y down, pixel centres at integers):
  ring        [x, y]  object position in the first tile (ring stays fixed on the sky in every frame)
  fit_points  [[x, y], ...]  points that must stay inside the window (ring + real track for movers;
              ring + a plausible fake track for stars and candidates -> same placement rule for every kind)
  track       [[x, y], ...]  real positions per frame (review only, may be empty)
  frame_count, tile_px, frame_size (window, 64), edge_px (8), ring_radius_px (4)

make_variant crops one random window that keeps every fit point >= edge_px from its border (the rule of
pipeline-prep 04_tasks.window_origin), applies the same random rot90/flip to every frame, stretches every
frame on its own (pipeline-prep common.robust_stretch) and returns the PNG plus the transformed ring.
"""
from __future__ import annotations
import io
import math
from pathlib import Path

import numpy as np
from PIL import Image


def robust_stretch(img, lo=1.0, hi=99.7):
    v = img[np.isfinite(img)]
    if v.size == 0:
        return np.zeros_like(img)
    a, b = np.percentile(v, [lo, hi])
    x = np.clip((img - a) / max(b - a, 1e-9), 0, 1)
    x = np.arcsinh(10 * x) / np.arcsinh(10)
    return np.nan_to_num(x)


def load_master(master) -> np.ndarray:
    if isinstance(master, np.ndarray):
        return master
    if isinstance(master, (bytes, bytearray)):
        im = Image.open(io.BytesIO(master))
    else:
        im = Image.open(Path(master))
    if im.mode in ("I;16", "I;16B", "I"):
        return np.array(im).astype(np.uint16)
    return np.array(im.convert("L"))


def window_range(points, tile, win, edge):
    """Feasible window origins along x and y (inclusive), clipped to the tile."""
    xs = [p[0] for p in points]; ys = [p[1] for p in points]
    out = []
    for vs in (xs, ys):
        lo = max(int(math.ceil(max(vs) - (win - edge))), 0)
        hi = min(int(math.floor(min(vs) - edge)), tile - win)
        if lo > hi:  # cannot satisfy the edge rule (legacy tiles): keep the ring as central as possible
            c = int(round(min(max(vs[0] - win / 2, 0), tile - win)))
            lo = hi = c
        out.append((lo, hi))
    return out


def transform_point(x, y, n, k, flip):
    """Point transform matching transform_tile (np.rot90 k times counter-clockwise, then left-right flip)."""
    for _ in range(int(k) % 4):
        x, y = y, n - 1 - x
    if flip:
        x = n - 1 - x
    return x, y


def transform_tile(a, k, flip):
    a = np.rot90(a, int(k) % 4)
    if flip:
        a = a[:, ::-1]
    return np.ascontiguousarray(a)


def make_variant_debug(master, meta, rng):
    arr = load_master(master)
    n = int(meta["frame_count"]); tile = int(meta["tile_px"]); win = int(meta.get("frame_size", 64))
    edge = int(meta.get("edge_px", 8))
    ring = meta["ring"]
    pts = [ring] + [p for p in (meta.get("fit_points") or [])]
    (lx, hx), (ly, hy) = window_range(pts, tile, win, edge)
    u0 = int(rng.integers(lx, hx + 1)); v0 = int(rng.integers(ly, hy + 1))
    k = int(rng.integers(4)); flip = int(rng.integers(2))
    tiles = []
    for i in range(n):
        t = arr[v0:v0 + win, i * tile + u0:i * tile + u0 + win]
        if t.dtype != np.uint8:
            t = (robust_stretch(t.astype(np.float64)) * 255).astype(np.uint8)
        tiles.append(transform_tile(t, k, flip))
    strip = np.concatenate(tiles, axis=1)
    buf = io.BytesIO()
    Image.fromarray(strip).save(buf, format="PNG")  # uint8 2-D -> mode "L" (mode= is deprecated in Pillow 11.3+)
    hx_, hy_ = transform_point(ring[0] - u0, ring[1] - v0, win, k, flip)
    # whole pixels: the ring's sub-pixel fraction is the same in every variant of a task and would fingerprint it
    highlight = {"x": int(round(float(hx_))), "y": int(round(float(hy_))), "r": int(meta.get("ring_radius_px", 4))}
    track = [transform_point(p[0] - u0, p[1] - v0, win, k, flip) for p in (meta.get("track") or [])]
    return buf.getvalue(), highlight, {"origin": (u0, v0), "k": k, "flip": flip, "track": track}


def make_variant(master, meta, rng):
    """-> (png_bytes, highlight {x, y, r}) for one random window/orientation. rng: numpy Generator."""
    png, highlight, _ = make_variant_debug(master, meta, rng)
    return png, highlight
