"""Turn one processed field into tasks: Moving controls (known movers), Not-moving controls (flux-matched static
stars) and science candidates (unknown tracklets = high confidence, unknown 2-frame pairs = low confidence).

Rendering rules are pipeline-prep/scripts/04_tasks.py's (kinds must be indistinguishable):
  - ring at the object's FIRST shown position, fixed on the sky; the ring's frame is the first tile
  - a mover's first shown step must clear the ring (>= RING_R + 2 px); earlier frames are dropped
  - window placement: movers so the whole track fits; stars and candidates as if a plausible track from the same
    field had to fit (fit_points), same edge rule for every kind
  - a frame is used only if it covers >= 90% of the window; >= 3 frames
New: we store a MASTER strip (window + margin on each side, 16-bit, background-subtracted, north up) and the
ring/track/fit points in master-tile coordinates; variants.make_variant crops/rotates at request time."""
from __future__ import annotations
import io
import logging
import time
import uuid

import numpy as np
import sep
from PIL import Image

log = logging.getLogger(__name__)


def group_key(name: str) -> str:
    """Holdout group = asteroid number if numbered ("420 Bertholda (A896 RC)" -> "420"), else the full name."""
    s = str(name).strip()
    first = s.split()[0] if s else s
    return first if first.isdigit() else s


class MasterRenderer:
    def __init__(self, S, cfg, rng):
        tk = cfg["tasks"]
        self.frames, self.npix = S["frames"], S["npix"]
        self.win, self.edge, self.ring_r = tk["window_px"], tk["edge_px"], tk["ring_radius_px"]
        self.margin, self.min_frames = tk["master_margin_px"], tk["min_frames"]
        self.rng = rng

    def window_origin(self, pts):
        """04_tasks.window_origin: random window keeping every point >= EDGE px from the border."""
        WIN, EDGE, npix = self.win, self.edge, self.npix
        xs, ys = [p[0] for p in pts], [p[1] for p in pts]
        lo_x = int(np.ceil(max(xs) - (WIN - EDGE))); hi_x = int(np.floor(min(xs) - EDGE))
        lo_y = int(np.ceil(max(ys) - (WIN - EDGE))); hi_y = int(np.floor(min(ys) - EDGE))
        lo_x, hi_x = max(lo_x, 0), min(hi_x, npix - WIN); lo_y, hi_y = max(lo_y, 0), min(hi_y, npix - WIN)
        if lo_x > hi_x or lo_y > hi_y:
            return None
        return int(self.rng.integers(lo_x, hi_x + 1)), int(self.rng.integers(lo_y, hi_y + 1))

    def render(self, x, y, fit_track=None, ring_frame=None, min_frame=0, tries=30, exact=None, n_frames=None,
               must_include=()):
        """Random feasible window (window_origin); new: up to `tries` draws until enough frames cover it
        (pipeline-prep drew once and gave up). Same rule for every kind, so it carries no answer signal.
        exact: the shown frames must be exactly this list (Not-moving paired with a Moving control of the field);
        n_frames: show exactly the first n covering frames (frame-count matching); must_include: frames that must
        be shown (e.g. the second point of a science pair)."""
        WIN, npix = self.win, self.npix
        if npix < WIN:
            return None
        fit = [(x, y)] + list(fit_track or [])
        M = self.margin
        if npix < WIN + 2 * M:
            M = (npix - WIN) // 2
        T = WIN + 2 * M
        for _ in range(tries):
            o = self.window_origin(fit)
            if o is None:
                return None
            x0, y0 = o
            mx0 = int(np.clip(x0 - M, 0, npix - T)); my0 = int(np.clip(y0 - M, 0, npix - T))
            ok = []
            cand_frames = list(exact) if exact is not None else range(min_frame, len(self.frames))
            for k in cand_frames:
                arr = self.frames[k]
                if np.mean(np.isfinite(arr[y0:y0 + WIN, x0:x0 + WIN])) < 0.9:
                    continue  # drop a frame that doesn't cover the window (04_tasks rule)
                if np.mean(np.isfinite(arr[my0:my0 + T, mx0:mx0 + T])) < 0.8:
                    continue  # new: the margin must be mostly covered too
                ok.append(k)
            if n_frames:
                ok = ok[:n_frames]
            good = len(ok) >= self.min_frames and (ring_frame is None or ok[0] == ring_frame)
            good = good and (exact is None or ok == list(exact)) and (not n_frames or len(ok) == n_frames)
            good = good and all(k in ok for k in must_include)
            if good:
                break
        else:
            return None
        tiles, used = [], []
        for k in ok:
            cut = self.frames[k][my0:my0 + T, mx0:mx0 + T]
            m = ~np.isfinite(cut)
            c2 = np.ascontiguousarray(np.where(m, np.nanmedian(cut), cut))
            c2 = c2 - sep.Background(c2, mask=m, bw=16, bh=16, fw=3, fh=3).back()
            c2[m] = 0.0
            tiles.append(c2[::-1])  # flip: north up
            used.append(k)
        if len(tiles) < self.min_frames:
            return None
        if ring_frame is not None and used[0] != ring_frame:
            return None
        enc = []
        for t in tiles:  # 16-bit linear per tile; variants stretch per window (percentile-based -> scale-free)
            lo, hi = float(t.min()), float(t.max())
            enc.append(np.round((t - lo) / max(hi - lo, 1e-12) * 65535).astype(np.uint16))
        master = np.concatenate(enc, axis=1)
        buf = io.BytesIO()
        Image.fromarray(master).save(buf, format="PNG")
        conv = lambda px, py: [round(float(px - mx0), 2), round(float(T - 1 - (py - my0)), 2)]
        return {"png": buf.getvalue(), "tile": T, "margin": M, "used": used, "conv": conv,
                "fit_points": [conv(px, py) for px, py in fit], "ring": conv(x, y)}


class TaskFactory:
    def __init__(self, cfg, db, store, S, D, field_id, rng):
        self.cfg, self.db, self.store, self.S, self.D, self.field_id, self.rng = cfg, db, store, S, D, field_id, rng
        self.R = MasterRenderer(S, cfg, rng)
        self.metas = S["metas"]
        self.made = []

    # ---------------------------------------------------------------- pools
    def control_slot(self, kind, group):
        c = self.cfg["controls"]
        tgt = {"control_pos": (c["moving_target"], c["holdout_moving_target"]),
               "control_neg": (c["not_moving_target"], c["holdout_not_moving_target"])}[kind]
        n_train = self.db.count_tasks(kind, "active", False); n_hold = self.db.count_tasks(kind, "active", True)
        h = self.db.group_holdout(group)
        if h is None:  # new group: give it to the split that is least full (proportional filling)
            fill_t = n_train / tgt[0] if tgt[0] else float("inf")
            fill_h = n_hold / tgt[1] if tgt[1] else float("inf")
            h = fill_h < fill_t
        full = (n_hold >= tgt[1]) if h else (n_train >= tgt[0])
        if not full:
            return "active", h
        if self.db.count_tasks(kind, "reserve") < c["reserve_max_per_kind"]:
            return "reserve", h
        return None, h

    def pools_need(self, kind):
        c = self.cfg["controls"]
        if kind == "control_pos":
            return self.db.count_tasks(kind, "active") < c["moving_target"] + c["holdout_moving_target"]
        return self.db.count_tasks(kind, "active") < c["not_moving_target"] + c["holdout_not_moving_target"]

    # ---------------------------------------------------------------- save
    def save(self, kind, r, x, y, expected, status, holdout, group, confidence, designation=None, track=None,
             flux=None, extra=None):
        tid = uuid.uuid4().hex[:16]
        used = r["used"]
        times = [float(self.metas[k]["mjd"]) for k in used]
        tr = []
        if track:
            for k, px, py in track:
                if k in used:
                    tr.append(r["conv"](px, py) + [used.index(k)])
        sky = self.S["W"].pixel_to_world(x, y)
        task = {"task_id": tid, "field_id": self.field_id, "kind": kind, "expected_answer": expected,
                "confidence": confidence, "holdout": int(bool(holdout)), "group_key": group, "status": status,
                "legacy": 0, "designation": designation, "frame_size": self.R.win, "frame_count": len(used),
                "tile_px": r["tile"], "margin_px": r["margin"], "ring": r["ring"], "track": tr,
                "fit_points": r["fit_points"],
                "frame_offsets_h": [round((t - times[0]) * 24 * 2) / 2 for t in times], "times_mjd": times,
                "ra": float(sky.ra.deg), "dec": float(sky.dec.deg), "flux": flux,
                "detector": self.metas[used[0]].get("detector_used"),
                "meta": {"frames_used": used, "image_ids": [self.metas[k].get("image_id") for k in used],
                         "edge_px": self.R.edge, "ring_radius_px": self.R.ring_r, **(extra or {})},
                "created_at": time.time()}
        if group is not None and kind != "science":
            self.db.set_group_holdout(group, bool(holdout))
        self.store.save_task(task, r["png"])
        self.made.append(task)
        return task

    # ---------------------------------------------------------------- fake tracks (04_tasks.fake_track)
    def stored_offsets(self):
        """Real Moving-control track vectors (first -> last shown point) from the database, in field orientation
        (master y points south, field y north). Cached per field."""
        if not hasattr(self, "_stored_offs"):
            offs = []
            for t in self.db.q("SELECT track FROM tasks WHERE kind='control_pos' AND legacy=0"):
                tr = t["track"] or []
                if len(tr) >= 2:
                    offs.append((tr[-1][0] - tr[0][0], -(tr[-1][1] - tr[0][1])))
            self._stored_offs = offs
        return self._stored_offs

    def fake_track(self, x, y):
        """04_tasks.fake_track: a plausible track from this field's movers; new: otherwise from stored real
        control tracks (random orientation); uniform 15-45 px only while no control exists yet."""
        offs = [(t["x"][-1] - t["x"][0], t["y"][-1] - t["y"][0]) for t in self.D["tracklets"]]
        if not offs and self.stored_offsets():
            dx, dy = self.stored_offsets()[int(self.rng.integers(len(self.stored_offsets())))]
            ang = self.rng.uniform(0, 2 * np.pi)
            L = float(np.hypot(dx, dy))
            return [(x, y), (x + L * np.cos(ang), y + L * np.sin(ang))]
        if offs:
            dx, dy = offs[int(self.rng.integers(len(offs)))]
        else:
            ang, L = self.rng.uniform(0, 2 * np.pi), self.rng.uniform(15, 45)
            dx, dy = L * np.cos(ang), L * np.sin(ang)
        return [(x, y), (x + dx, y + dy)]

    # ---------------------------------------------------------------- main
    def run(self, cands, known_per_frame):
        """cands: tracklets + pairs with labels (detect.label). Returns list of (candidate, decision)."""
        sc, c = self.cfg["science"], self.cfg["controls"]
        dets = self.D["detections"]
        nf = len(dets)
        ring_r = self.R.ring_r
        decisions = []
        pos_groups = []
        # 1) Moving controls: known tracklets (>= 3 frames), ring at first point whose next step clears the ring
        for t in [t for t in cands if t["ctype"] == "tracklet" and t.get("known")]:
            group = group_key(t["known"])
            if not self.pools_need("control_pos") and self.db.count_tasks("control_pos", "reserve") >= c["reserve_max_per_kind"]:
                decisions.append((t, "known_pool_full")); continue
            status, h = self.control_slot("control_pos", group)
            if status is None:
                decisions.append((t, "known_pool_full")); continue
            start = next((i for i in range(len(t["frames"]) - 2)
                          if np.hypot(t["x"][i + 1] - t["x"][i], t["y"][i + 1] - t["y"][i]) >= ring_r + 2), None)
            if start is None:
                decisions.append((t, "known_step_too_small")); continue
            fr, xs, ys = t["frames"][start:], t["x"][start:], t["y"][start:]
            r = self.R.render(xs[0], ys[0], fit_track=list(zip(xs, ys)), ring_frame=fr[0], min_frame=fr[0])
            if r is None:
                decisions.append((t, "known_no_render")); continue
            task = self.save("control_pos", r, xs[0], ys[0], "moving", status, h, group, "control", t["known"],
                             track=list(zip(fr, xs, ys)), flux=t.get("flux0"),
                             extra={"source": t.get("label_source"), "rate_deg_day": t["rate_deg_day"]})
            decisions.append((t, f"control_pos:{status}:{task['task_id']}"))
            pos_groups.append((group, h, t.get("flux0"), r["used"]))
        # 2) Not-moving controls
        ref_fluxes = [t["flux0"] for t in cands if t["ctype"] == "tracklet" and t.get("known") and t.get("flux0")]
        ref = float(np.median(ref_fluxes)) if ref_fluxes else None
        n_neg = c["negatives_per_moving_control"] * len(pos_groups)
        group = pos_groups[0][0] if pos_groups else None
        if not pos_groups and c["negatives_from_moverless_fields"] and self.pools_need("control_neg") \
                and self.db.count_tasks("control_neg", "active") < self.db.count_tasks("control_pos", "active"):
            ref = self.reference_flux_from_db()
            n_neg, group = (1 if ref else 0), f"field:{self.field_id}"
        plan = pos_groups[0][3] if pos_groups else None
        if n_neg and ref:
            self.make_negatives(n_neg, ref, group, cands, known_per_frame, plan)
        # 3) Science: unknown tracklets (high), unknown pairs (low)
        n_sci, seen = 0, []
        for t in cands:
            if t.get("known"):
                if t["ctype"] == "pair":
                    decisions.append((t, "pair_known"))
                continue
            if t.get("near_known"):
                decisions.append((t, "ambiguous_near_known")); continue
            if t["ctype"] == "pair" and not sc["keep_pairs"]:
                decisions.append((t, "pair_skipped")); continue
            if n_sci >= sc["max_science_per_field"]:
                decisions.append((t, "science_cap")); continue
            # new: same rule as Moving controls - start at the first point whose next step clears the ring
            start = next((i for i in range(len(t["frames"]) - 1)
                          if np.hypot(t["x"][i + 1] - t["x"][i], t["y"][i + 1] - t["y"][i]) >= ring_r + 2), None)
            if start is None or (t["ctype"] == "tracklet" and len(t["frames"]) - start < 3):
                decisions.append((t, "science_step_too_small")); continue
            t = dict(t, frames=t["frames"][start:], x=t["x"][start:], y=t["y"][start:])
            x, y = t["x"][0], t["y"][0]
            if any(np.hypot(x - sx, y - sy) < 3 for sx, sy in seen):
                decisions.append((t, "duplicate")); continue
            trk = list(zip(t["x"], t["y"]))
            need = t["frames"][:2]  # the ring frame and the next detection must be shown
            r, matched = None, False
            for attempt in range(6):  # new: redraw the fake track before giving up; first try frame-count matching
                fit = trk + self.fake_track(x, y)[1:]
                n = self.sample_frame_count() if attempt < 3 else None
                r = self.R.render(x, y, fit_track=fit, ring_frame=t["frames"][0], min_frame=t["frames"][0],
                                  n_frames=n, must_include=need)
                if r is not None:
                    matched = n is not None
                    break
            if r is None:
                decisions.append((t, "science_no_render")); continue
            conf = "high" if t["ctype"] == "tracklet" else "low"
            task = self.save("science", r, x, y, None, "active", False, None, conf,
                             track=list(zip(t["frames"], t["x"], t["y"])), flux=t.get("flux0"),
                             extra={"candidate_type": t["ctype"], "rate_deg_day": t["rate_deg_day"],
                                    "fit_rms_px": t.get("fit_rms_px"), "outlier_frac": t.get("outlier_frac"),
                                    "low_confidence_flag": conf == "low", "frame_count_matched": matched})
            seen.append((x, y)); n_sci += 1
            decisions.append((t, f"science:{conf}:{task['task_id']}"))
        return decisions

    def sample_frame_count(self):
        """Frame counts of Moving controls (stored + this field's), so other kinds can match the distribution."""
        counts = [t["frame_count"] for t in self.db.q("SELECT frame_count FROM tasks WHERE kind='control_pos' AND legacy=0")]
        if not counts:
            return None
        return int(counts[int(self.rng.integers(len(counts)))])

    def reference_flux_from_db(self):
        det = self.metas[0].get("detector_used")
        rows = self.db.q("SELECT flux FROM tasks WHERE kind='control_pos' AND legacy=0 AND flux IS NOT NULL AND detector=?", (det,))
        if not rows:
            rows = self.db.q("SELECT flux FROM tasks WHERE kind='control_pos' AND legacy=0 AND flux IS NOT NULL")
        if not rows:
            return None
        return float(rows[int(self.rng.integers(len(rows)))]["flux"])

    def make_negatives(self, n_wanted, ref, group, cands, known_per_frame, plan=None):
        """04_tasks negatives: static in every frame, flux 0.5-2x the asteroid, >= 6 px from any mover / pair /
        known-object prediction, away from edges; closest flux first.
        New: shown with exactly the frames of the field's Moving control (plan), so frame count and time offsets
        carry no answer signal; in fields without a Moving control the frame count is drawn from the stored Moving
        controls' distribution."""
        c = self.cfg["controls"]
        dets, npix, EDGE = self.D["detections"], self.S["npix"], self.R.edge
        nf = len(dets)
        avoid = [(x, y) for t in cands for x, y in zip(t["x"], t["y"])]
        avoid += [(o["x"], o["y"]) for objs in known_per_frame for o in objs]
        frames = list(plan) if plan else list(range(nf))
        base = frames[0]
        pool = []
        for d in dets[base]:
            if not d["static"]:
                continue
            if not (EDGE <= d["x"] < npix - EDGE and EDGE <= d["y"] < npix - EDGE):
                continue
            if not all(any(np.hypot(e["x"] - d["x"], e["y"] - d["y"]) < 1.0 for e in dets[k]) for k in frames[1:]):
                continue
            if any(np.hypot(mx - d["x"], my - d["y"]) < 6 for mx, my in avoid):
                continue
            ratio = d["flux"] / ref
            if not (c["flux_ratio_min"] <= ratio <= c["flux_ratio_max"]):
                continue
            pool.append((abs(np.log(ratio)), ratio, d))
        pool.sort(key=lambda p: p[0])
        made = 0
        for _, ratio, d in pool:
            if made >= n_wanted:
                break
            status, h = self.control_slot("control_neg", group)
            if status is None:
                break
            r = None
            for _ in range(3):
                if plan:
                    r = self.R.render(d["x"], d["y"], fit_track=self.fake_track(d["x"], d["y"]), ring_frame=base,
                                      exact=frames)
                else:
                    r = self.R.render(d["x"], d["y"], fit_track=self.fake_track(d["x"], d["y"]), ring_frame=base,
                                      n_frames=self.sample_frame_count())
                if r is not None:
                    break
            if r is None:
                continue
            self.save("control_neg", r, d["x"], d["y"], "not_moving", status, h, group, "control", None,
                      flux=d["flux"], extra={"flux_ratio_to_asteroid": round(float(ratio), 2)})
            made += 1
        return made
