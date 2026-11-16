"""Field construction: pick exposures (one per visit), cut out, reproject to a common 6.15"/px TAN grid.
build_field generalises pipeline-prep/scripts/field.py:build_sequence (same per-visit detector choice and
coverage rule for targeted fields); sequence selection follows 01b_episodes.py / 02a_sequences.py."""
from __future__ import annotations
import logging
import time

import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord, GeocentricTrueEcliptic
from astropy.wcs import WCS
from reproject import reproject_interp

from .irsa import PIX, load_frame

log = logging.getLogger(__name__)


class OutOfTime(Exception):
    """Raised between network steps when the run's time budget is used up (field goes back to the queue)."""


def check_deadline(deadline, need_s=0.0):
    """Stop before a step that would not finish in time (need_s = typical duration of the next step)."""
    if deadline is not None and time.time() + need_s > deadline:
        raise OutOfTime()


def ref_wcs(ra, dec, npix):
    w = WCS(naxis=2)
    w.wcs.ctype = ["RA---TAN", "DEC--TAN"]
    w.wcs.crval = [ra, dec]
    w.wcs.crpix = [(npix + 1) / 2, (npix + 1) / 2]
    w.wcs.cdelt = [-PIX / 3600, PIX / 3600]
    return w


# ---------------------------------------------------------------- sky selection
def random_position(rng, min_b, max_beta):
    """Uniform on the sphere within |ecliptic beta| <= max_beta, rejecting |galactic b| < min_b."""
    for _ in range(10000):
        lam = rng.uniform(0, 360)
        beta = np.degrees(np.arcsin(rng.uniform(-np.sin(np.radians(max_beta)), np.sin(np.radians(max_beta)))))
        c = SkyCoord(lam * u.deg, beta * u.deg, frame=GeocentricTrueEcliptic(equinox="J2000")).icrs
        if abs(c.galactic.b.deg) >= min_b:
            return float(c.ra.deg), float(c.dec.deg)
    raise RuntimeError("sky selection is empty")


def sky_ok(ra, dec, min_b, max_beta=None):
    c = SkyCoord(ra * u.deg, dec * u.deg)
    if abs(c.galactic.b.deg) < min_b:
        return False
    if max_beta is not None:
        e = c.transform_to(GeocentricTrueEcliptic(equinox="J2000"))
        if abs(e.lat.deg) > max_beta:
            return False
    return True


# ---------------------------------------------------------------- visits / sequences
def group_visits(images, split_min):
    """Exposures closer than split_min minutes belong to one visit (pipeline-prep 01b)."""
    if not images:
        return []
    images = sorted(images, key=lambda r: r["mjd"])
    visits, cur = [], [images[0]]
    for r in images[1:]:
        if (r["mjd"] - cur[-1]["mjd"]) * 1440 > split_min:
            visits.append(cur); cur = [r]
        else:
            cur.append(r)
    visits.append(cur)
    return visits


def rank_detector(pref):
    return lambda r: (pref.index(r["detector"]) if r["detector"] in pref else 99, r["mjd"])


def survey_sequences(images, scfg):
    """Sequences of >= min_visits visits, consecutive picks >= min_visit_gap_min apart, all within max_span_hours.
    Each visit becomes a candidate list: the first exposure of every detector, best detector first."""
    pref = list(scfg["detector_preference"])
    visits = group_visits(images, scfg["visit_split_min"])
    vcands = []
    for v in visits:
        per_det = {}
        for r in sorted(v, key=lambda r: r["mjd"]):
            per_det.setdefault(r["detector"], r)
        vcands.append(sorted(per_det.values(), key=rank_detector(pref)))
    seqs = []
    for i in range(len(vcands)):
        seq, t0 = [i], vcands[i][0]["mjd"]
        for j in range(i + 1, len(vcands)):
            tj = min(r["mjd"] for r in vcands[j])
            if (tj - t0) * 24 > scfg["max_span_hours"]:
                break
            tl = min(r["mjd"] for r in vcands[seq[-1]])
            if (tj - tl) * 1440 >= scfg["min_visit_gap_min"]:
                seq.append(j)
                if len(seq) >= scfg["max_visits"]:
                    break
        if len(seq) >= scfg["min_visits"]:
            seqs.append([vcands[k] for k in seq])
    return seqs


def most_sequences(rows, tcfg, min_b, pref, split_min=30):
    """pipeline-prep 01b + 02a: visits (<30 min) -> episodes (<1 day) -> 3-4 consecutive visits with every step
    >= min_step_px, total <= max_total_px, |b| >= min_b, V <= max_vmag. Non-overlapping, 4-visit sequences first."""
    visits = group_visits(rows, split_min)
    rank = rank_detector(pref)
    V = []
    for v in visits:
        V.append({"mjd": v[0]["mjd"], "ra": v[0]["ra_obj"], "dec": v[0]["dec_obj"], "vmag": v[0]["vmag"],
                  "cands": sorted(v, key=rank)[:4]})
    if not V:
        return []
    vm = np.array([v["mjd"] for v in V])
    ep = np.concatenate([[0], np.cumsum(np.diff(vm) > 1.0)])
    out = []
    for e in np.unique(ep):
        ids = list(np.where(ep == e)[0])
        for L in (4, 3):
            for i in range(len(ids) - L + 1):
                seq = ids[i:i + L]
                c = SkyCoord([V[s]["ra"] for s in seq] * u.deg, [V[s]["dec"] for s in seq] * u.deg)
                steps = [c[j].separation(c[j + 1]).arcsec / PIX for j in range(L - 1)]
                total = c[0].separation(c[-1]).arcsec / PIX
                if min(steps) < tcfg["min_step_px"] or total > tcfg["max_total_px"]:
                    continue
                if abs(c[0].galactic.b.deg) < min_b or V[seq[0]]["vmag"] > tcfg["max_vmag"]:
                    continue
                out.append({"visits": [int(s) for s in seq], "total_px": float(total), "vmag": V[seq[0]]["vmag"],
                            "cands": [V[s]["cands"] for s in seq]})
    out.sort(key=lambda s: (-len(s["visits"]), abs(s["total_px"] - 25)))
    used, keep = set(), []
    for s in out:
        if set(s["visits"]) & used:
            continue
        used |= set(s["visits"]); keep.append(s)
    return keep


# ---------------------------------------------------------------- aligned field
def build_field(irsa, ra, dec, size_deg, visits, max_tries, min_cov, targeted=False, deadline=None, paths=None):
    """visits: list of candidate lists (best detector first). Returns the aligned stack.
    targeted=True keeps pipeline-prep's rule: best detector whose cutout fully covers the target (15x15 px
    around the MOST prediction) and >= 60% of the field. Survey: first detector covering >= min_cov."""
    npix = int(np.ceil(size_deg * 3600 / PIX)) | 1
    W = ref_wcs(ra, dec, npix)
    frames, outl, metas = [], [], []
    paths = [] if paths is None else paths  # caller's list: filled even if we raise (cache cleanup)
    for cands in visits:
        best, best_score = None, None
        for r in cands[:max_tries]:
            check_deadline(deadline, 20)
            path = irsa.fetch_cutout(r["url"], ra, dec, size_deg * 1.2)
            paths.append(path)
            img, outlier, w, meta = load_frame(path)
            arr, foot = reproject_interp((img, w), W, shape_out=(npix, npix))
            cov = float(np.mean(foot > 0.5))
            if targeted:
                tx, ty = W.world_to_pixel(SkyCoord(r["ra_obj"] * u.deg, r["dec_obj"] * u.deg))
                tx, ty = int(round(float(tx))), int(round(float(ty)))
                win = foot[max(0, ty - 7):ty + 8, max(0, tx - 7):tx + 8]
                tcov = float(np.mean(win > 0.5)) if win.size else 0.0
                ok, score = (tcov >= 0.999 and cov >= 0.6), (tcov, cov)
            else:
                ok, score = cov >= min_cov, (cov,)
            item = (cov, r, img, outlier, w, meta, arr, foot)
            if ok:
                best = item; break
            if best is None or score > best_score:
                best, best_score = item, score
        if best is None:
            continue
        cov, r, img, outlier, w, meta, arr, foot = best
        if not targeted and cov < 0.5:
            continue
        arr = arr.copy(); arr[foot < 0.5] = np.nan
        o, _ = reproject_interp((outlier, w), W, shape_out=(npix, npix), order="nearest-neighbor")
        frames.append(arr); outl.append(np.nan_to_num(o) > 0.5)
        meta.update({"coverage": round(cov, 3), "image_id": r["image_id"], "image_url": r["url"],
                     "detector_used": r["detector"]})
        if targeted:
            meta["pred_target_radec"] = [r["ra_obj"], r["dec_obj"]]
        metas.append(meta)
    return {"W": W, "npix": npix, "frames": frames, "outlier": outl, "metas": metas,
            "center": SkyCoord(ra * u.deg, dec * u.deg), "size_deg": size_deg, "paths": paths}
