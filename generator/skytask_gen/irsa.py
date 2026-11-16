"""IRSA access: SIA image search (survey), MOST (targeted fields), IBE cutouts, FITS loading.
fetch_cutout / load_frame adapted from pipeline-prep/scripts/common.py (verified there)."""
from __future__ import annotations
import hashlib
import logging
import re
import time
import warnings
from pathlib import Path

import numpy as np
import requests
from astropy.io import fits
from astropy.wcs import WCS, FITSFixedWarning

warnings.simplefilter("ignore", FITSFixedWarning)
log = logging.getLogger(__name__)

PIX = 6.15  # arcsec / pixel
# FLAGS bits we repair (fill from neighbours). Persistence (17), hot/cold (10/11) and OUTLIER (19) are NOT
# blanked: blanking them punched holes exactly where asteroids sit (pipeline-prep findings/02).
BAD_BITS = [1, 2, 6, 9]
OUTLIER_BIT = 19  # SPHEREx flags movers as outliers: a hint, never a mask
BAD_MASK = sum(1 << b for b in BAD_BITS)


class IrsaUnavailable(RuntimeError):
    pass


class Irsa:
    def __init__(self, cfg, fits_dir: Path):
        rl = cfg["rate_limits"]
        self.pause = float(rl["irsa_pause_s"])
        self.timeout = float(rl["irsa_timeout_s"])
        self.fits_dir = Path(fits_dir)
        self._last = 0.0
        self.session = requests.Session()
        self.session.headers["User-Agent"] = "skytask-gen/0.1 (research micro-task generator)"

    def _polite(self):
        dt = time.time() - self._last
        if dt < self.pause:
            time.sleep(self.pause - dt)
        self._last = time.time()

    # ---- cutouts (common.fetch_cutout)
    def cutout_path(self, url, ra, dec, size_deg) -> Path:
        key = hashlib.md5(f"{url}|{ra:.5f}|{dec:.5f}|{size_deg:.4f}".encode()).hexdigest()
        return self.fits_dir / (key + ".fits")

    def fetch_cutout(self, url, ra, dec, size_deg, retries=3) -> Path:
        path = self.cutout_path(url, ra, dec, size_deg)
        if path.exists() and path.stat().st_size > 10000:
            return path
        q = f"{url}?center={ra:.6f},{dec:.6f}&size={size_deg:.4f}deg"
        for i in range(retries):
            self._polite()
            try:
                r = self.session.get(q, timeout=self.timeout)
                if r.status_code == 200 and len(r.content) > 10000:
                    tmp = path.with_suffix(".part")
                    tmp.write_bytes(r.content)
                    tmp.replace(path)
                    return path
                log.debug("cutout HTTP %s (%d bytes)", r.status_code, len(r.content))
            except requests.RequestException as e:
                log.debug("cutout error %s", e)
            time.sleep(2 * (i + 1))
        raise IrsaUnavailable(f"cutout failed: {q}")

    # ---- SIA (survey mode)
    def sia_images(self, ra, dec, collections, retries=3):
        """All SPHEREx spectral images covering (ra, dec). Returns list of dicts sorted by time."""
        from astroquery.ipac.irsa import Irsa as AQIrsa
        from astropy.coordinates import SkyCoord
        import astropy.units as u
        c = SkyCoord(ra * u.deg, dec * u.deg)
        rows = []
        for col in collections:
            for i in range(retries):
                self._polite()
                try:
                    t = AQIrsa.query_sia(pos=(c, 2 * u.arcsec), collection=col)
                    break
                except Exception as e:  # IRSA answers 502 now and then
                    log.debug("SIA %s failed (%s), retry %d", col, str(e)[:120], i + 1)
                    time.sleep(5 * (i + 1))
            else:
                raise IrsaUnavailable(f"SIA {col} unavailable")
            for r in t:
                if str(r["dataproduct_type"]) != "image":
                    continue
                det = parse_detector(str(r["energy_bandpassname"]), str(r["access_url"]))
                if det is None:
                    continue
                rows.append({"image_id": f"{r['obs_id']}{det}", "obs_id": str(r["obs_id"]), "detector": det,
                             "mjd": float(r["t_min"]), "url": str(r["access_url"]), "collection": col})
        # same image can appear in two collections (re-processing): keep the later collection's copy
        uniq = {}
        for r in rows:
            uniq[r["image_id"]] = r
        return sorted(uniq.values(), key=lambda r: r["mjd"])

    # ---- MOST (targeted fields; pipeline-prep/scripts/01_find_data.py)
    def most(self, number):
        from astroquery.ipac.irsa import Most
        from astropy.table import Table
        self._polite()
        r = Most.query_object(catalog="spherex", input_mode="name_input", obj_name=str(number),
                              obj_type="Asteroid", output_mode="Regular")
        t = Table(r["results"])
        if len(t) == 0:
            return []
        t.sort("mjd_obs")
        out = []
        for row in t:
            iid = str(row["Image_ID"])
            out.append({"image_id": iid, "detector": iid[-2:], "mjd": float(row["mjd_obs"]),
                        "url": str(row["image_url"]), "ra_obj": float(row["ra_obj"]), "dec_obj": float(row["dec_obj"]),
                        "vmag": float(row["vmag"]) if row["vmag"] is not None else 99.0})
        return out


def parse_detector(bandname: str, url: str):
    m = re.search(r"D([1-6])\b", bandname) or re.search(r"_\d+D([1-6])_", url)
    return f"D{m.group(1)}" if m else None


def load_frame(path):
    """pipeline-prep common.load_frame + spacecraft velocity and OUTLIER plane (from field.py)."""
    with fits.open(path) as h:
        img = h["IMAGE"].data.astype(float)
        flags = h["FLAGS"].data
        hdr = h["IMAGE"].header
        w = WCS(hdr, relax=True)
        bad = ((flags & BAD_MASK) != 0) | ~np.isfinite(img)
        if bad.any():  # repair: replace with 5x5 median of good neighbours
            from scipy.ndimage import generic_filter
            tmp = np.where(bad, np.nan, img)
            med = generic_filter(tmp, np.nanmedian, size=5, mode="nearest")
            img = np.where(bad, np.nan_to_num(med, nan=np.nanmedian(tmp)), img)
        outlier = ((flags >> OUTLIER_BIT) & 1).astype(float)
        meta = {"mjd": hdr.get("MJD-AVG", hdr.get("MJD-OBS")), "date": hdr.get("DATE-OBS"),
                "detector": hdr.get("DETECTOR"), "sc_xyz_km": [hdr.get("X_SC"), hdr.get("Y_SC"), hdr.get("Z_SC")],
                "sc_vel_kms": [hdr.get("VX_SC"), hdr.get("VY_SC"), hdr.get("VZ_SC")],
                "psf_fwhm": hdr.get("PSF_FWHM")}
    return img, outlier, w, meta
