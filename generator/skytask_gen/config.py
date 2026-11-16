"""Settings: built-in defaults merged with settings.toml (tomllib on 3.11+, tomli on 3.10)."""
from __future__ import annotations
import copy
import os
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib

ROOT = Path(__file__).resolve().parent.parent

DEFAULTS = {
    "general": {"data_dir": "data", "cache_dir": "cache", "log_level": "INFO", "store_backend": "local", "seed": 0},
    "run": {"max_runtime_minutes": 0, "max_fields_per_run": 0, "pause_between_fields_s": 2, "max_field_attempts": 3},
    "sky": {"min_abs_galactic_b": 25.0, "max_abs_ecliptic_beta": 20.0, "field_size_arcmin": 12.0},
    "survey": {"enabled": True, "collections": ["spherex_dr1", "spherex_qr3"], "min_visits": 3, "max_visits": 4,
               "min_visit_gap_min": 90.0, "max_span_hours": 14.0, "visit_split_min": 30.0,
               "detector_preference": ["D2", "D1", "D3", "D6", "D5", "D4"], "max_cutout_tries_per_visit": 2,
               "min_field_coverage": 0.9, "positions_per_attempt": 8, "sia_failures_before_fallback": 3,
               "fallback_asteroid_max_number": 200000},
    "controls": {"moving_target": 50, "not_moving_target": 50, "holdout_moving_target": 12,
                 "holdout_not_moving_target": 12, "reserve_max_per_kind": 20, "negatives_per_moving_control": 1,
                 "flux_ratio_min": 0.5, "flux_ratio_max": 2.0, "negatives_from_moverless_fields": True},
    "topup": {"enabled": True, "every_n_fields": 3, "max_vmag": 16.7, "asteroid_number_min": 1,
              "asteroid_number_max": 3000, "max_fields_per_asteroid": 2, "min_step_px": 4, "max_total_px": 50},
    "science": {"thresh_sigma": 5.0, "r_static_px": 1.0, "tol_link_px": 2.0, "rate_min_deg_day": 0.05,
                "rate_max_deg_day": 0.5, "fit_rms_max_px": 1.2, "r_known_px": 2.0, "blend_ratio": 3.0,
                "max_science_per_field": 3, "keep_pairs": True, "pair_require_outlier": True, "pair_max_flux_ratio": 4.0, "jpl_vmag_limit": 21},
    "tasks": {"window_px": 64, "ring_radius_px": 4, "master_margin_px": 16, "edge_px": 8, "min_frames": 3},
    "rate_limits": {"jpl_max_parallel": 1, "jpl_pause_s": 5.0, "jpl_timeout_s": 150, "jpl_max_retries": 4,
                    "jpl_backoff_s": 60.0, "jpl_expected_s": 75, "irsa_pause_s": 1.0, "irsa_timeout_s": 120},
    "cache": {"cleanup_after_field": True, "max_cache_mb": 2000},
    "seed_data": {"import_legacy": True, "legacy_dir": "../pipeline-prep/tasks_v8"},
}


def _merge(base: dict, over: dict) -> dict:
    out = copy.deepcopy(base)
    for k, v in over.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


def _resolve(p: str, base: Path) -> Path:
    q = Path(os.path.expanduser(str(p)))
    return q if q.is_absolute() else (base / q).resolve()


def load(path: str | os.PathLike | None = None) -> dict:
    """Read settings. Order: explicit path > $SKYTASK_SETTINGS > <generator>/settings.toml."""
    path = Path(path or os.environ.get("SKYTASK_SETTINGS") or ROOT / "settings.toml")
    user = {}
    if path.exists():
        with open(path, "rb") as f:
            user = tomllib.load(f)
    cfg = _merge(DEFAULTS, user)
    base = path.resolve().parent
    cfg["paths"] = {
        "settings": path.resolve(),
        "data": _resolve(cfg["general"]["data_dir"], base),
        "cache": _resolve(cfg["general"]["cache_dir"], base),
        "legacy": _resolve(cfg["seed_data"]["legacy_dir"], base),
    }
    p = cfg["paths"]
    p["db"] = p["data"] / "skytask.sqlite3"
    p["masters"] = p["data"] / "masters"
    p["exports"] = p["data"] / "exports"
    p["logs"] = p["data"] / "logs"
    p["fits"] = p["cache"] / "fits"
    p["jpl"] = p["cache"] / "jpl"
    for k in ("data", "masters", "exports", "logs", "fits", "jpl"):
        p[k].mkdir(parents=True, exist_ok=True)
    tk = cfg["tasks"]
    need = tk["window_px"] + 2 * tk["master_margin_px"] + 8
    if cfg["sky"]["field_size_arcmin"] * 60 / 6.15 < need:
        raise ValueError(f"sky.field_size_arcmin too small for window + margins (needs >= {need * 6.15 / 60:.1f}')")
    return cfg
