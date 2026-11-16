"""Main loop: keep picking fields (survey or MOST top-up), process them, store tasks. Restartable:
everything lives in SQLite; a field interrupted by Ctrl+C / the time budget goes back to the queue, and cached
cutouts / JPL answers make the retry cheap."""
from __future__ import annotations
import json
import logging
import logging.handlers
import shutil
import time
import uuid
from pathlib import Path

import numpy as np
import astropy.units as u
from astropy.coordinates import SkyCoord

from .db import DB
from .detect import detect_link, find_pairs, label
from .field import (OutOfTime, build_field, check_deadline, most_sequences, random_position, survey_sequences)
from .irsa import Irsa, IrsaUnavailable, PIX
from .jpl import Jpl, JplUnavailable
from .store import make_store
from .tasks import TaskFactory, group_key

log = logging.getLogger("skytask_gen")


class RunLock:
    """Single-instance lock on <data_dir>/run.lock. The OS releases it when the process dies, so a crash never
    leaves a stale lock (fcntl on Linux/macOS, msvcrt on Windows)."""

    def __init__(self, path):
        self.path, self.f = Path(path), None

    def __enter__(self):
        import os
        self.f = open(self.path, "a+")
        try:
            if os.name == "nt":
                import msvcrt
                self.f.seek(0)
                msvcrt.locking(self.f.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(self.f.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError:
            self.f.close()
            raise SystemExit(f"Another generator is already running on this data folder ({self.path}). "
                             "Stop it first (Ctrl+C in its window).")
        return self

    def __exit__(self, *exc):
        try:
            self.f.close()
        except OSError:
            pass


def setup_logging(cfg):
    root = logging.getLogger()
    if getattr(root, "_skytask", False):
        return
    root.setLevel(getattr(logging, str(cfg["general"]["log_level"]).upper(), logging.INFO))
    fmt = logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S")
    sh = logging.StreamHandler(); sh.setFormatter(fmt); root.addHandler(sh)
    fh = logging.handlers.RotatingFileHandler(Path(cfg["paths"]["logs"]) / "generator.log", maxBytes=5_000_000,
                                              backupCount=3, encoding="utf-8")
    fh.setFormatter(fmt); root.addHandler(fh)
    for noisy in ("astroquery", "astropy", "urllib3", "pyvo", "reproject", "reproject.common"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    root._skytask = True


# astroquery installs its own Logger class on import: import it before touching its logger (see setup_logging)
import astroquery.ipac.irsa  # noqa: E402,F401


class Generator:
    def __init__(self, cfg):
        self.cfg = cfg
        setup_logging(cfg)
        P = cfg["paths"]
        self.db = DB(P["db"])
        self.store = make_store(cfg, self.db)
        self.irsa = Irsa(cfg, P["fits"])
        self.jpl = Jpl(cfg, P["jpl"])
        seed = int(cfg["general"]["seed"] or 0)
        # a fixed seed still differs per run (run counter) so a restart doesn't re-draw the same sky
        runs = self.db.get("runs", 0)
        self.rng = np.random.default_rng([seed, runs] if seed else None)
        self.size_deg = cfg["sky"]["field_size_arcmin"] / 60

    # ================================================================ loop
    def run(self, max_fields=None, max_minutes=None):
        with RunLock(Path(self.cfg["paths"]["data"]) / "run.lock"):
            return self._run(max_fields, max_minutes)

    def _run(self, max_fields=None, max_minutes=None):
        cfg = self.cfg
        rc = cfg["run"]
        max_fields = rc["max_fields_per_run"] if max_fields is None else max_fields
        max_minutes = rc["max_runtime_minutes"] if max_minutes is None else max_minutes
        t_start = time.time()
        deadline = t_start + max_minutes * 60 if max_minutes else None
        self.db.set("runs", self.db.get("runs", 0) + 1)
        n = self.db.reset_stale(rc["max_field_attempts"])
        m = self.db.x("UPDATE fields SET status='queued' WHERE status='failed' AND attempts < ?",
                      (rc["max_field_attempts"],)).rowcount
        log.info("run started (stale fields requeued: %d, failed fields retried: %d)", n, m)
        if cfg["seed_data"]["import_legacy"] and not self.db.get("legacy_imported"):
            try:
                import_legacy(cfg, self.db, self.store)
            except Exception as e:  # noqa: BLE001
                log.warning("legacy import skipped: %s", e)
        processed, idle = 0, 0
        try:
            while True:
                if deadline and time.time() > deadline - 10:
                    log.info("time budget reached"); break
                if max_fields and processed >= max_fields:
                    log.info("max fields per run reached"); break
                # failed fields (network trouble) get another chance after 30 min
                self.db.x("UPDATE fields SET status='queued' WHERE status='failed' AND attempts < ? AND updated_at < ?",
                          (rc["max_field_attempts"], time.time() - 1800))
                f = self.db.next_queued(rc["max_field_attempts"])
                if f is None:
                    try:
                        f = self.enqueue_next(deadline)
                    except OutOfTime:
                        log.info("time budget reached while picking a field"); break
                    if f is None:
                        idle += 1
                        if not (cfg["survey"]["enabled"] or cfg["topup"]["enabled"]) or idle > 20:
                            log.warning("nothing to do (survey/top-up disabled or no sources answer); stopping"); break
                        time.sleep(min(60, 5 * idle))
                        continue
                idle = 0
                res = self.process(f, deadline)
                if res == "out_of_time":
                    break
                processed += 1
                time.sleep(rc["pause_between_fields_s"])
        except KeyboardInterrupt:
            log.info("Ctrl+C: stopping (the current field goes back to the queue)")
        finally:
            self.enforce_cache_cap()
            log.info("run finished: %d fields in %.1f min", processed, (time.time() - t_start) / 60)
        return processed

    # ================================================================ choosing the next field
    def enqueue_next(self, deadline):
        cfg, db = self.cfg, self.db
        c = cfg["controls"]
        need_pos = db.count_tasks("control_pos", "active") < c["moving_target"] + c["holdout_moving_target"]
        since = db.get("fields_since_topup", 0)
        every = max(1, int(cfg["topup"]["every_n_fields"]))
        if cfg["topup"]["enabled"] and need_pos and (not cfg["survey"]["enabled"] or since + 1 >= every):
            f = self.enqueue_most("topup", deadline)
            if f:
                db.set("fields_since_topup", 0)
                return f
        if cfg["survey"]["enabled"]:
            if time.time() < db.get("sia_fallback_until", 0):
                f = self.enqueue_most("fallback", deadline)
            else:
                f = self.enqueue_survey(deadline)
            if f:
                db.set("fields_since_topup", since + 1)
            return f
        return None

    def _stat(self, key, inc=1):
        self.db.set(key, self.db.get(key, 0) + inc)

    def enqueue_survey(self, deadline):
        cfg, sv = self.cfg, self.cfg["survey"]
        for _ in range(int(sv["positions_per_attempt"])):
            check_deadline(deadline)
            ra, dec = random_position(self.rng, cfg["sky"]["min_abs_galactic_b"], cfg["sky"]["max_abs_ecliptic_beta"])
            try:
                images = self.irsa.sia_images(ra, dec, sv["collections"])
            except IrsaUnavailable as e:
                fails = self.db.get("sia_failures", 0) + 1
                self.db.set("sia_failures", fails)
                log.warning("IRSA SIA unavailable (%s), consecutive failures %d", e, fails)
                if fails >= sv["sia_failures_before_fallback"]:
                    log.warning("switching to MOST-anchored survey fields for 30 min")
                    self.db.set("sia_fallback_until", time.time() + 1800); self.db.set("sia_failures", 0)
                return None
            self.db.set("sia_failures", 0)
            self._stat("survey_positions_tried")
            seqs = survey_sequences(images, sv)
            if not seqs:
                self._stat("survey_positions_no_sequence")
                log.debug("(%.3f, %.3f): %d images, no sequence", ra, dec, len(images))
                continue
            L = max(len(s) for s in seqs)
            seq = [s for s in seqs if len(s) == L]
            seq = seq[int(self.rng.integers(len(seq)))]
            fid = "s" + uuid.uuid4().hex[:10]
            params = {"visits": seq, "n_images": len(images), "n_sequences": len(seqs)}
            self.db.add_field(fid, "survey", ra, dec, self.size_deg, params)
            log.info("queued survey field %s at (%.3f, %+.3f): %d images, %d sequences, using %d visits",
                     fid, ra, dec, len(images), len(seqs), L)
            return self.db.one("SELECT * FROM fields WHERE field_id=?", (fid,))
        return None

    def enqueue_most(self, purpose, deadline):
        cfg, tp = self.cfg, self.cfg["topup"]
        lo, hi = (tp["asteroid_number_min"], tp["asteroid_number_max"]) if purpose == "topup" else \
            (1, cfg["survey"]["fallback_asteroid_max_number"])
        tcfg = dict(tp)
        if purpose != "topup":
            tcfg["max_vmag"] = 99.0  # fallback survey: any brightness; the target need not be detectable
        first = None
        for _ in range(6):
            check_deadline(deadline)
            num = int(self.rng.integers(lo, hi + 1))
            if self.db.one("SELECT 1 AS x FROM asteroids WHERE number=?", (num,)):
                continue
            try:
                rows = self.irsa.most(num)
            except Exception as e:  # noqa: BLE001
                log.warning("MOST query for (%d) failed: %s", num, str(e)[:150])
                continue
            seqs = most_sequences(rows, tcfg, cfg["sky"]["min_abs_galactic_b"], list(cfg["survey"]["detector_preference"]))
            seqs = seqs[:int(tp["max_fields_per_asteroid"])]
            self.db.insert("asteroids", {"number": num, "purpose": purpose, "n_fields": len(seqs),
                                         "note": f"{len(rows)} MOST images", "created_at": time.time()}, replace=True)
            for s in seqs:
                firsts = [v[0] for v in s["cands"]]
                pos = SkyCoord([r["ra_obj"] for r in firsts] * u.deg, [r["dec_obj"] for r in firsts] * u.deg)
                cen = SkyCoord(np.mean([r["ra_obj"] for r in firsts]) * u.deg, np.mean([r["dec_obj"] for r in firsts]) * u.deg)
                span = float(max(pos.separation(cen).arcsec))
                size = max((2 * span + 2 * 150) / 3600, self.size_deg)
                fid = f"m{num}-v{'-'.join(map(str, s['visits']))}"
                if self.db.field_exists(fid):
                    continue
                self.db.add_field(fid, "most", float(cen.ra.deg), float(cen.dec.deg), size,
                                  {"number": num, "purpose": purpose, "vmag": s["vmag"], "visits": s["cands"]})
                first = first or fid
            log.info("MOST (%d) [%s]: %d images, %d usable sequences", num, purpose, len(rows), len(seqs))
            if first:
                return self.db.one("SELECT * FROM fields WHERE field_id=?", (first,))
        return None

    # ================================================================ one field
    def process(self, f, deadline):
        cfg, db = self.cfg, self.db
        sc = cfg["science"]
        fid = f["field_id"]
        t0 = time.time()
        attempts = (f["attempts"] or 0) + 1
        db.update_field(fid, status="processing", attempts=attempts)
        targeted = f["source"] == "most"
        params = f["params"]
        paths = []
        jpl0 = self.jpl.calls
        n_purged = self.store.purge_field(fid)  # idempotent: a resumed field starts from a clean slate
        if n_purged:
            log.info("field %s: cleared %d tasks of an earlier attempt (unpublished deleted, published retired)", fid, n_purged)
        try:
            max_tries = 4 if targeted else int(cfg["survey"]["max_cutout_tries_per_visit"])
            S = build_field(self.irsa, f["ra"], f["dec"], f["size_deg"], params["visits"], max_tries,
                            cfg["survey"]["min_field_coverage"], targeted=targeted, deadline=deadline, paths=paths)
            nf = len(S["frames"])
            for k, m in enumerate(S["metas"]):
                db.insert("frames", {"field_id": fid, "idx": k, "image_id": m["image_id"], "image_url": m["image_url"],
                                     "mjd": m["mjd"], "detector": m["detector_used"], "coverage": m["coverage"]}, replace=True)
            if nf < cfg["tasks"]["min_frames"]:
                return self._finish(fid, t0, paths, "done", "too_few_frames", n_frames=nf, n_tasks=0)
            tdays = np.array([m["mjd"] for m in S["metas"]]) - S["metas"][0]["mjd"]
            D = detect_link(S["frames"], S["outlier"], tdays, sc)
            pairs = find_pairs(D, tdays, sc) if sc["keep_pairs"] else []
            cands = [dict(t, ctype="tracklet") for t in D["tracklets"]] + [dict(p, ctype="pair") for p in pairs]
            # known objects: MOST target prediction per frame (targeted fields), JPL only if something else moved
            kpf = [[] for _ in range(nf)]
            target = None
            if targeted:
                target = str(params["number"])
                for k, m in enumerate(S["metas"]):
                    x, y = S["W"].world_to_pixel(SkyCoord(*m["pred_target_radec"], unit="deg"))
                    kpf[k].append({"name": target, "x": float(x), "y": float(y), "source": "MOST"})
            self._label(cands, kpf, sc)
            others = [c for c in cands if not c.get("near_known")]
            if others:
                jk = self.jpl.known_per_frame(S, sc["jpl_vmag_limit"], deadline)
                for k in range(nf):
                    kpf[k] += jk[k]
                self._label(cands, kpf, sc)
            for c in cands:
                if c.get("known"):
                    c["label_source"] = "MOST" if c["known"] == target else "JPL"
            fac = TaskFactory(cfg, db, self.store, S, D, fid, self.rng)
            decisions = fac.run(cands, kpf)
            now = time.time()
            for c, dec in decisions:
                db.insert("candidates", {"field_id": fid, "ctype": c["ctype"], "n_frames": len(c["frames"]),
                                         "known": c.get("known"), "rate_deg_day": c.get("rate_deg_day"),
                                         "fit_rms_px": c.get("fit_rms_px"), "decision": dec.split(":")[0] if ":" in dec else dec,
                                         "task_id": dec.split(":")[-1] if dec.count(":") == 2 else None,
                                         "data": {k: c[k] for k in ("frames", "x", "y", "flux0", "outlier_frac") if k in c},
                                         "created_at": now})
            db.insert("detections", {"field_id": fid, "data": {"n_det": [len(d) for d in D["detections"]],
                                                               "n_transients": D["n_transients"],
                                                               "tracklets": D["tracklets"], "n_pairs": len(pairs),
                                                               "known_per_frame": kpf}}, replace=True)
            kinds = {}
            for t in fac.made:
                kinds[t["kind"]] = kinds.get(t["kind"], 0) + 1
            outcome = ", ".join(f"{k}={v}" for k, v in sorted(kinds.items())) or "no_tasks"
            return self._finish(fid, t0, paths, "done", outcome, n_frames=nf, n_tracklets=len(D["tracklets"]),
                                n_pairs=len(pairs), jpl_calls=self.jpl.calls - jpl0, n_tasks=len(fac.made))
        except OutOfTime:
            db.update_field(fid, status="queued", attempts=attempts - 1, reason="interrupted (time budget)")
            log.info("field %s interrupted by the time budget; it will resume next run", fid)
            return "out_of_time"
        except KeyboardInterrupt:
            db.update_field(fid, status="queued", attempts=attempts - 1, reason="interrupted (Ctrl+C)")
            raise
        except (IrsaUnavailable, JplUnavailable) as e:
            log.warning("field %s failed (network): %s", fid, str(e)[:200])
            return self._finish(fid, t0, paths, "failed", "network", reason=str(e)[:300])
        except Exception as e:  # noqa: BLE001
            log.exception("field %s failed", fid)
            return self._finish(fid, t0, paths, "failed", "error", reason=f"{type(e).__name__}: {str(e)[:250]}")

    @staticmethod
    def _label(cands, kpf, sc):
        tr = [c for c in cands if c["ctype"] == "tracklet"]
        pr = [c for c in cands if c["ctype"] == "pair"]
        label(tr, kpf, sc["r_known_px"], min_hits=2)
        label(pr, kpf, sc["r_known_px"], min_hits=1)

    def _finish(self, fid, t0, paths, status, outcome, reason=None, keep_cache=False, **kw):
        secs = round(time.time() - t0, 1)
        self.db.update_field(fid, status=status, outcome=outcome, reason=reason, seconds=secs, **kw)
        log.info("field %s %s in %.0f s: %s %s", fid, status, secs, outcome,
                 " ".join(f"{k}={v}" for k, v in kw.items()))
        if self.cfg["cache"]["cleanup_after_field"] and not keep_cache:
            for p in paths:
                try:
                    Path(p).unlink(missing_ok=True)
                except OSError:
                    pass
        self.enforce_cache_cap()
        return status

    def enforce_cache_cap(self):
        cap = float(self.cfg["cache"]["max_cache_mb"]) * 1e6
        files = [p for p in Path(self.cfg["paths"]["cache"]).rglob("*") if p.is_file()]
        total = sum(p.stat().st_size for p in files)
        if total <= cap:
            return
        for p in sorted(files, key=lambda p: p.stat().st_mtime):  # oldest first
            if total <= cap * 0.8:
                break
            try:
                s = p.stat().st_size; p.unlink(); total -= s
            except OSError:
                pass
        log.info("cache trimmed to %.0f MB", total / 1e6)


# ==================================================================== legacy seed (pipeline-prep tasks_v8)
def import_legacy(cfg, db, store):
    src = Path(cfg["paths"]["legacy"])
    srv = src / "tasks_server.jsonl"
    if not srv.exists():
        raise FileNotFoundError(srv)
    review = {}
    rv = src / "review_keys.jsonl"
    if rv.exists():
        for line in rv.read_text().splitlines():
            if line.strip():
                d = json.loads(line); review[d["task_id"]] = d
    n = 0
    done_ids = {(t["meta"] or {}).get("legacy_task_id") for t in db.q("SELECT meta FROM tasks WHERE legacy=1")}
    for line in srv.read_text().splitlines():
        if not line.strip():
            continue
        r = json.loads(line)
        if r["kind"] not in ("control_pos", "control_neg"):
            continue  # the single v8 science task may be a satellite-streak artefact (pipeline-prep QA): skipped
        # public ids must not reveal the kind or origin: a fresh random id; the v8 id stays in server-only meta
        if r["task_id"] in done_ids:
            continue
        tid = uuid.uuid4().hex[:16]
        group = group_key(r.get("asteroid_field") or r["field_id"])
        db.set_group_holdout(group, bool(r["holdout"]))
        kind = r["kind"]
        c = cfg["controls"]
        tgt = (c["holdout_moving_target"] if r["holdout"] else c["moving_target"]) if kind == "control_pos" else \
              (c["holdout_not_moving_target"] if r["holdout"] else c["not_moving_target"])
        status = "active" if db.count_tasks(kind, "active", bool(r["holdout"])) < tgt else "reserve"
        trk = [[p[0], p[1], i] for i, p in enumerate((review.get(r["task_id"]) or {}).get("review_track_tile_xy") or [])]
        h = r["highlight"]
        t0 = r["times_mjd"][0]
        task = {"task_id": tid, "field_id": "legacy:" + r["field_id"], "kind": kind,
                "expected_answer": r["expected_answer"], "confidence": "control", "holdout": int(bool(r["holdout"])),
                "group_key": group, "status": status, "legacy": 1, "designation": r.get("asteroid_designation"),
                "frame_size": r["frame_size"], "frame_count": r["frame_count"], "tile_px": r["frame_size"], "margin_px": 0,
                "ring": [h["x"], h["y"]], "track": trk, "fit_points": [[p[0], p[1]] for p in trk],
                "frame_offsets_h": [round((t - t0) * 24 * 2) / 2 for t in r["times_mjd"]], "times_mjd": r["times_mjd"],
                "ra": r["ra"], "dec": r["dec"], "flux": None, "detector": None,
                "meta": {"edge_px": 8, "ring_radius_px": h.get("r", 4), "legacy_strip": r["strip_file"],
                         "legacy_task_id": r["task_id"],
                         "source": "pipeline-prep/tasks_v8"}, "created_at": time.time()}
        store.save_task(task, (src / r["strip_file"]).read_bytes())
        if group.isdigit():
            db.insert("asteroids", {"number": int(group), "purpose": "legacy", "n_fields": 0, "note": "tasks_v8",
                                    "created_at": time.time()}, replace=True)
        n += 1
    db.set("legacy_imported", True)
    log.info("imported %d legacy controls from %s", n, src)
    return n
