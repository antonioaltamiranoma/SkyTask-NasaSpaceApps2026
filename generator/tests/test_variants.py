"""Unit test: after every random window + rotation + flip, the ring lands on the object (and the track
points land on the object in later frames). Run: python -m unittest discover -s tests"""
import io
import sys
import unittest
from pathlib import Path

import numpy as np
from PIL import Image

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skytask_gen.variants import make_variant, make_variant_debug  # noqa: E402


def synthetic(n=4, tile=96, rng=None, dtype=np.uint16):
    rng = rng or np.random.default_rng(0)
    arr = (rng.normal(1000, 20, size=(tile, n * tile))).clip(0, 65535)
    x0, y0 = rng.uniform(20, 76, 2)
    step = rng.uniform(6, 12, 2) * rng.choice([-1, 1], 2)
    track = []
    for i in range(n):
        x, y = x0 + i * step[0], y0 + i * step[1]
        if not (10 <= x <= tile - 11 and 10 <= y <= tile - 11):
            break
        track.append([x, y])
        yy, xx = np.mgrid[0:tile, 0:tile]
        arr[:, i * tile:(i + 1) * tile] += 30000 * np.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * 1.2 ** 2))
    n = len(track)
    arr = arr[:, :n * tile]
    if dtype == np.uint8:
        arr = (arr / arr.max() * 255)
    meta = {"frame_count": n, "tile_px": tile, "frame_size": 64, "edge_px": 8, "ring_radius_px": 4,
            "ring": track[0], "fit_points": track, "track": track}
    return arr.astype(dtype), meta


class TestVariants(unittest.TestCase):
    def check(self, dtype):
        for s in range(300):
            rng = np.random.default_rng(s)
            arr, meta = synthetic(rng=rng, dtype=dtype)
            if meta["frame_count"] < 2:
                continue
            png, hl, dbg = make_variant_debug(arr, meta, np.random.default_rng(1000 + s))
            img = np.array(Image.open(io.BytesIO(png)))
            n = meta["frame_count"]
            self.assertEqual(img.shape, (64, 64 * n))
            for i in range(n):
                t = img[:, i * 64:(i + 1) * 64].astype(float)
                yy, xx = np.unravel_index(np.argmax(t), t.shape)
                # centroid of the bright core (the stretch saturates the peak, argmax alone is +-1 px)
                y0, x0 = max(yy - 4, 0), max(xx - 4, 0)
                box = t[y0:yy + 5, x0:xx + 5]
                w = np.where(box >= 0.6 * box.max(), box, 0)
                gy, gx = np.mgrid[0:box.shape[0], 0:box.shape[1]]
                yy, xx = y0 + (w * gy).sum() / w.sum(), x0 + (w * gx).sum() / w.sum()
                ex, ey = (hl["x"], hl["y"]) if i == 0 else dbg["track"][i]
                tol = 1.75 if i == 0 else 1.0  # highlight is rounded to whole pixels (+-0.71 px)
                self.assertLessEqual(np.hypot(xx - ex, yy - ey), tol,
                                     f"seed {s} frame {i}: peak ({xx},{yy}) vs expected ({ex:.1f},{ey:.1f}) k={dbg['k']} flip={dbg['flip']}")
            # placement rule: ring and track stay inside [edge, win-edge] (mirrored by flips -> [edge-1, win-edge])
            for (x, y) in [(hl["x"], hl["y"])] + list(dbg["track"]):
                self.assertTrue(7 - 1e-6 <= x <= 56 + 1e-6 and 7 - 1e-6 <= y <= 56 + 1e-6, (x, y))

    def test_uint16_master(self):
        self.check(np.uint16)

    def test_uint8_master(self):
        self.check(np.uint8)

    def test_all_orientations_used(self):
        arr, meta = synthetic(rng=np.random.default_rng(3))
        seen = set()
        for s in range(200):
            _, _, d = make_variant_debug(arr, meta, np.random.default_rng(s))
            seen.add((d["k"], d["flip"]))
        self.assertEqual(len(seen), 8)

    def test_public_api_and_png_bytes(self):
        arr, meta = synthetic(rng=np.random.default_rng(5))
        buf = io.BytesIO(); Image.fromarray(arr).save(buf, format="PNG")
        png, hl = make_variant(buf.getvalue(), meta, np.random.default_rng(1))
        self.assertTrue(png.startswith(b"\x89PNG"))
        self.assertEqual(set(hl), {"x", "y", "r"})

    def test_highlight_whole_pixels(self):
        """The ring's sub-pixel fraction must not survive into the public highlight (task fingerprint)."""
        arr, meta = synthetic(rng=np.random.default_rng(7))
        meta["ring"] = [meta["ring"][0] + 0.37, meta["ring"][1] + 0.21]
        for s in range(50):
            _, hl = make_variant(arr, meta, np.random.default_rng(s))
            self.assertIsInstance(hl["x"], int)
            self.assertIsInstance(hl["y"], int)


if __name__ == "__main__":
    unittest.main()
