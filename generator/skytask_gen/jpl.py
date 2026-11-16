"""JPL Small-Body Identification API, observer = SPHEREx geocentric state from the FITS header.
Query format and parsing copied from pipeline-prep/scripts/jpl.py (verified: JPL vs IRSA MOST 0.25-0.56").
Added: polite pacing (one call at a time by default, pause between calls), retries with exponential backoff
on HTTP 429 / errors, cache in <cache_dir>/jpl. Error responses are never cached."""
from __future__ import annotations
import hashlib
import json
import logging
import threading
import time
from pathlib import Path

import requests
import astropy.units as u
from astropy.coordinates import SkyCoord, Angle
from astropy.time import Time

from .field import check_deadline

API = "https://ssd-api.jpl.nasa.gov/sb_ident.api"
log = logging.getLogger(__name__)


class JplUnavailable(RuntimeError):
    pass


class Jpl:
    def __init__(self, cfg, cache_dir: Path):
        rl = cfg["rate_limits"]
        self.max_parallel = max(1, min(int(rl["jpl_max_parallel"]), 2))  # >2 parallel triggers HTTP 429
        self.sem = threading.Semaphore(self.max_parallel)
        self.pause = float(rl["jpl_pause_s"])
        self.timeout = float(rl["jpl_timeout_s"])
        self.retries = int(rl["jpl_max_retries"])
        self.backoff = float(rl["jpl_backoff_s"])
        self.expected = float(rl.get("jpl_expected_s", 75))
        self.cache = Path(cache_dir)
        self._last = 0.0
        self._lock = threading.Lock()
        self.calls = 0  # network calls made (cache hits not counted)

    @staticmethod
    def params(ra, dec, mjd, half_width_deg, sc_xyz_km, sc_vel_kms, vmag_lim=21):
        c = SkyCoord(ra * u.deg, dec * u.deg)
        ra_s = c.ra.to_string(unit=u.hour, sep="-", precision=2, pad=True)
        dec_s = c.dec.to_string(unit=u.deg, sep="-", precision=1, alwayssign=False, pad=True)
        if c.dec.deg < 0:  # JPL wants an "M" prefix for negative values, not "-"
            dec_s = "M" + dec_s.lstrip("-")
        t = Time(mjd, format="mjd").utc.strftime("%Y-%m-%d_%H:%M:%S")
        xobs = ",".join(f"{v:.6E}" for v in list(sc_xyz_km) + list(sc_vel_kms))
        return {"xobs": xobs, "obs-time": t, "fov-ra-center": ra_s, "fov-dec-center": dec_s,
                "fov-ra-hwidth": f"{half_width_deg:.4f}", "fov-dec-hwidth": f"{half_width_deg:.4f}",
                "two-pass": "true", "suppress-first-pass": "true", "req-elem": "false",
                "vmag-lim": str(vmag_lim), "mag-required": "true"}

    def cache_path(self, params):
        return self.cache / (hashlib.md5(json.dumps(params, sort_keys=True).encode()).hexdigest() + ".json")

    def _get(self, params, deadline=None):
        key = self.cache_path(params)
        if key.exists():
            return json.loads(key.read_text())
        last = None
        wait = self.backoff
        for i in range(self.retries):
            check_deadline(deadline, self.expected)  # a two-pass call takes ~50-90 s
            with self.sem:
                with self._lock:
                    dt = time.time() - self._last
                    if dt < self.pause:
                        time.sleep(self.pause - dt)
                    self._last = time.time()
                try:
                    self.calls += 1
                    t_call = time.time()
                    r = requests.get(API, params=params, timeout=self.timeout,
                                     headers={"User-Agent": "skytask-gen/0.1 (research micro-task generator)"})
                    if r.status_code == 429:
                        raise RuntimeError("HTTP 429 (rate limited)")
                    r.raise_for_status()
                    j = r.json()
                    # never cache an error or an unexpected payload as "no objects"
                    # ... but JPL's explicit empty answer is valid: {"warning": "no matching records", ...}
                    empty = str(j.get("warning", "")).strip().lower() == "no matching records" and "summary" in j
                    if "n_second_pass" not in j and "data_second_pass" not in j and not empty:
                        raise RuntimeError(f"unexpected JPL response: {str(j)[:300]}")
                    log.info("JPL answered in %.0f s (%s objects)", time.time() - t_call,
                             j.get("n_second_pass", 0 if "warning" in j else "?"))
                    tmp = key.with_suffix(".part")
                    tmp.write_text(json.dumps(j))
                    tmp.replace(key)
                    return j
                except Exception as e:  # noqa: BLE001
                    last = e
                    log.warning("JPL call failed (%s); retry %d/%d in %.0f s", str(e)[:120], i + 1, self.retries, wait)
            if i + 1 < self.retries:
                if deadline is not None and time.time() + wait > deadline:
                    from .field import OutOfTime
                    raise OutOfTime()
                time.sleep(wait)
                wait *= 2
        raise JplUnavailable(str(last))

    def known_objects(self, ra, dec, mjd, half_width_deg, sc_xyz_km, sc_vel_kms, vmag_lim=21, deadline=None):
        j = self._get(self.params(ra, dec, mjd, half_width_deg, sc_xyz_km, sc_vel_kms, vmag_lim), deadline)
        fields = j.get("fields_second") or []
        out = []
        for row in j.get("data_second_pass") or []:
            d = dict(zip(fields, row))
            ra_k = next((d[k] for k in d if k.startswith("Astrometric RA")), None)
            dec_k = next((d[k] for k in d if k.startswith("Astrometric Dec")), None)
            vm = next((d[k] for k in d if "magnitude" in k.lower()), None)
            try:
                pos = SkyCoord(Angle(ra_k.replace(" ", ":").replace("-", ":") if ra_k else "0", unit=u.hour),
                               Angle(dec_k.replace("'", ":").replace('"', "").replace(" ", ":") if dec_k else "0", unit=u.deg))
            except Exception:  # noqa: BLE001
                pos = None
            if pos is None:
                continue
            try:
                vm = float(vm)
            except (TypeError, ValueError):
                vm = None
            out.append({"name": d.get("Object name"), "ra": float(pos.ra.deg), "dec": float(pos.dec.deg), "vmag": vm})
        return out

    def known_per_frame(self, S, vmag_lim=21, deadline=None):
        """One call per frame (cached), field centre, half width = field/2 * 1.1 (as pipeline-prep)."""
        W = S["W"]
        res = []
        work = [(k, m) for k, m in enumerate(S["metas"])]

        def one(km):
            k, m = km
            objs = self.known_objects(S["center"].ra.deg, S["center"].dec.deg, m["mjd"], S["size_deg"] / 2 * 1.1,
                                      m["sc_xyz_km"], m["sc_vel_kms"], vmag_lim, deadline)
            lst = []
            for o in objs:
                x, y = W.world_to_pixel(SkyCoord(o["ra"] * u.deg, o["dec"] * u.deg))
                lst.append({**o, "x": float(x), "y": float(y), "source": "JPL"})
            return lst
        if self.max_parallel > 1:
            from concurrent.futures import ThreadPoolExecutor
            with ThreadPoolExecutor(self.max_parallel) as ex:
                res = list(ex.map(one, work))
        else:
            res = [one(w) for w in work]
        return res
