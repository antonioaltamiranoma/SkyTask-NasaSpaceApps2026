"""Blind detection, static/transient split, linking, labeling, 2-frame pairs.
Logic copied from pipeline-prep/scripts/03_detect_link.py (steps 1-4) and 04_tasks.py (pairs) without
changing the verified rules; parameters come from settings [science]. New for pairs: checked against known
objects (only UNKNOWN pairs become science tasks), both ends must carry SPHEREx's OUTLIER hint and have
similar flux (settings pair_require_outlier / pair_max_flux_ratio)."""
from __future__ import annotations
import itertools

import numpy as np
import sep

from .irsa import PIX


def detect_link(frames, outlier, tdays, sc):
    nf = len(frames)
    thresh, r_static, tol_link = sc["thresh_sigma"], sc["r_static_px"], sc["tol_link_px"]
    rate_min, rate_max, fit_rms_max, blend = sc["rate_min_deg_day"], sc["rate_max_deg_day"], sc["fit_rms_max_px"], sc["blend_ratio"]
    # 1) detect
    dets = []
    for k, arr in enumerate(frames):
        mask = ~np.isfinite(arr)
        data = np.ascontiguousarray(np.where(mask, np.nanmedian(arr), arr))
        bkg = sep.Background(data, mask=mask, bw=16, bh=16)
        d = data - bkg.back()
        o = sep.extract(d, thresh, err=bkg.globalrms, mask=mask, minarea=3)
        keep = []  # drop detections touching the invalid border
        for i in range(len(o)):
            xi, yi = int(round(o["x"][i])), int(round(o["y"][i]))
            win = mask[max(0, yi - 3):yi + 4, max(0, xi - 3):xi + 4]
            keep.append(not win.any())
        o = o[np.array(keep, bool)] if len(o) else o
        outl = outlier[k]
        of = np.array([outl[max(0, int(round(y)) - 1):int(round(y)) + 2, max(0, int(round(x)) - 1):int(round(x)) + 2].any()
                       for x, y in zip(o["x"], o["y"])], bool) if len(o) else np.zeros(0, bool)
        dets.append({"x": np.array(o["x"]), "y": np.array(o["y"]), "flux": np.array(o["flux"]), "outlier": of})
    # 2) static vs transient (1 px, 3x blend rule, coverage rule)
    trans = []
    for k in range(nf):
        xs, ys, fl = dets[k]["x"], dets[k]["y"], dets[k]["flux"]
        is_static = np.zeros(len(xs), bool)
        for j in range(nf):
            if j == k or len(dets[j]["x"]) == 0 or len(xs) == 0:
                continue
            dx = xs[:, None] - dets[j]["x"][None, :]; dy = ys[:, None] - dets[j]["y"][None, :]
            close = np.hypot(dx, dy) < r_static
            ratio_ok = fl[:, None] <= blend * np.maximum(dets[j]["flux"][None, :], 1e-9)
            is_static |= (close & ratio_ok).any(axis=1)
        covered = np.array([any(np.isfinite(frames[j][int(round(y)), int(round(x))]) for j in range(nf) if j != k)
                            for x, y in zip(xs, ys)], bool) if len(xs) else np.zeros(0, bool)
        trans.append(np.where(~is_static & covered)[0])
    # 3) link
    px_per_day = lambda degday: degday * 3600 / PIX
    tracklets = []
    for fi, fj in itertools.combinations(range(nf), 2):
        dt = tdays[fj] - tdays[fi]
        if dt <= 0:
            continue
        for a in trans[fi]:
            for b in trans[fj]:
                vx = (dets[fj]["x"][b] - dets[fi]["x"][a]) / dt; vy = (dets[fj]["y"][b] - dets[fi]["y"][a]) / dt
                rate = np.hypot(vx, vy)
                if not (px_per_day(rate_min) <= rate <= px_per_day(rate_max)):
                    continue
                pts = {fi: a, fj: b}
                for k in range(nf):
                    if k in pts or len(trans[k]) == 0:
                        continue
                    px = dets[fi]["x"][a] + vx * (tdays[k] - tdays[fi]); py = dets[fi]["y"][a] + vy * (tdays[k] - tdays[fi])
                    cand = trans[k]
                    dd = np.hypot(dets[k]["x"][cand] - px, dets[k]["y"][cand] - py)
                    if len(dd) and dd.min() < tol_link:
                        pts[k] = cand[int(np.argmin(dd))]
                if len(pts) < 3:
                    continue
                ks = sorted(pts); t_ = tdays[ks]
                X = np.array([dets[k]["x"][pts[k]] for k in ks]); Y = np.array([dets[k]["y"][pts[k]] for k in ks])
                A = np.vstack([np.ones_like(t_), t_]).T
                cx, *_ = np.linalg.lstsq(A, X, rcond=None); cy, *_ = np.linalg.lstsq(A, Y, rcond=None)
                rms = float(np.sqrt(np.mean((A @ cx - X) ** 2 + (A @ cy - Y) ** 2)))
                if rms > fit_rms_max:
                    continue
                tracklets.append({"frames": [int(k) for k in ks], "det_idx": [int(pts[k]) for k in ks],
                                  "x": X.tolist(), "y": Y.tolist(),
                                  "rate_deg_day": float(np.hypot(cx[1], cy[1]) * PIX / 3600), "fit_rms_px": round(rms, 2),
                                  "outlier_frac": float(np.mean([dets[k]["outlier"][pts[k]] for k in ks]))})
    tracklets.sort(key=lambda t: (-len(t["frames"]), t["fit_rms_px"]))
    used, uniq = set(), []
    for t in tracklets:
        key = {(k, i) for k, i in zip(t["frames"], t["det_idx"])}
        if key & used:
            continue
        used |= key; uniq.append(t)
    static_flags = []
    for k in range(nf):
        st = np.ones(len(dets[k]["x"]), bool); st[trans[k]] = False; static_flags.append(st)
    detections = [[{"x": float(dets[k]["x"][i]), "y": float(dets[k]["y"][i]), "flux": float(dets[k]["flux"][i]),
                    "static": bool(static_flags[k][i]), "outlier": bool(dets[k]["outlier"][i])}
                   for i in range(len(dets[k]["x"]))] for k in range(nf)]
    for t in uniq:
        t["flux0"] = detections[t["frames"][0]][t["det_idx"][0]]["flux"]
    return {"detections": detections, "n_transients": [int(len(t)) for t in trans], "tracklets": uniq}


def find_pairs(D, tdays, sc):
    """2-frame transient pairs at a plausible rate, not part of any tracklet (pipeline-prep 04_tasks.py)."""
    dets, nf = D["detections"], len(D["detections"])
    used = {(k, i) for t in D["tracklets"] for k, i in zip(t["frames"], t["det_idx"])}
    pxday = lambda v: v * 3600 / PIX
    pairs = []
    for fi, fj in itertools.combinations(range(nf), 2):
        dt = tdays[fj] - tdays[fi]
        if dt <= 0:
            continue
        for i, a in enumerate(dets[fi]):
            if a["static"] or (fi, i) in used:
                continue
            for j, b in enumerate(dets[fj]):
                if b["static"] or (fj, j) in used:
                    continue
                rate = np.hypot(b["x"] - a["x"], b["y"] - a["y"]) / dt
                if not (pxday(sc["rate_min_deg_day"]) <= rate <= pxday(sc["rate_max_deg_day"])):
                    continue
                # new screens (not in pipeline-prep): random 5-sigma noise peaks pair up easily in a 12' field
                if sc.get("pair_require_outlier", True) and not (a["outlier"] and b["outlier"]):
                    continue
                fr = max(a["flux"], b["flux"]) / max(min(a["flux"], b["flux"]), 1e-9)
                if fr > sc.get("pair_max_flux_ratio", 4.0):
                    continue
                pairs.append({"frames": [fi, fj], "det_idx": [i, j], "x": [a["x"], b["x"]], "y": [a["y"], b["y"]],
                              "rate_deg_day": round(rate * PIX / 3600, 3), "flux0": a["flux"],
                              "outlier_frac": float(np.mean([a["outlier"], b["outlier"]]))})
    return pairs


def label(cands, known_per_frame, r_known, min_hits=2):
    """A candidate is 'known' if >= min_hits of its points lie within r_known px of that frame's prediction
    (03_detect_link.py rule; pairs use min_hits=1 so a partly detected known object never becomes science)."""
    for t in cands:
        hits = {}
        for k, x, y in zip(t["frames"], t["x"], t["y"]):
            for o in known_per_frame[k]:
                if np.hypot(o["x"] - x, o["y"] - y) < r_known:
                    hits[o["name"]] = hits.get(o["name"], 0) + 1
        t["known"] = max(hits, key=hits.get) if hits and max(hits.values()) >= min_hits else None
        t["near_known"] = bool(hits)
    return cands
