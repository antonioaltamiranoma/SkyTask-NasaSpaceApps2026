"""Service settings.

Order: env SKYTASK_SETTINGS (a path) -> service/settings.local.toml -> service/settings.local.example.toml.
Relative paths inside the file are resolved against the file's folder. SKYTASK_DEV=1 enables the /v1/dev routes.
"""
from __future__ import annotations

import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # Python 3.10 (Antonio's PC)
    import tomli as tomllib

SERVICE_DIR = Path(__file__).resolve().parent.parent
REPO_DIR = SERVICE_DIR.parent


@dataclass
class SiteKey:
    max_count: int = 10
    modes: tuple = ("none", "score")


@dataclass
class Settings:
    generator_data_dir: Path = SERVICE_DIR / "../../generator/data"
    service_db: Path = SERVICE_DIR / "local_service.sqlite3"
    static_dir: Path | None = REPO_DIR / "explorer"
    widget_dir: Path | None = REPO_DIR / "widget"
    cors_origins: list = field(default_factory=lambda: ["http://localhost:8080", "http://127.0.0.1:8080"])
    site_keys: dict = field(default_factory=lambda: {"local-demo": SiteKey(10, ("none", "score", "token"))})
    challenge_ttl_s: int = 600
    pool_refresh_s: float = 30.0
    science_share: float = 0.5        # at most this share of a challenge is unsettled science (CLAUDE.md: <= 50%)
    recheck_rate: float = 0.05        # chance a settled science task is still served (re-check)
    dev: bool = False
    store: str = "local"              # "local" (generator SQLite) or "cloud" (Firestore + private bucket)
    gcp_project: str = ""
    gcp_bucket: str = ""


def _resolve(base: Path, p) -> Path:
    p = Path(os.path.expanduser(str(p)))
    return p if p.is_absolute() else (base / p).resolve()


def load_settings(path: str | os.PathLike | None = None) -> Settings:
    path = path or os.environ.get("SKYTASK_SETTINGS")
    if not path:
        for cand in (SERVICE_DIR / "settings.local.toml", SERVICE_DIR / "settings.local.example.toml"):
            if cand.exists():
                path = cand
                break
    s = Settings()
    if path:
        path = Path(path)
        base = path.resolve().parent
        with open(path, "rb") as f:
            raw = tomllib.load(f)
        local = raw.get("local", {})
        if "generator_data_dir" in local:
            s.generator_data_dir = _resolve(base, local["generator_data_dir"])
        if "service_db" in local:
            s.service_db = _resolve(base, local["service_db"])
        for k in ("static_dir", "widget_dir"):
            if k in local:
                setattr(s, k, _resolve(base, local[k]) if local[k] else None)
        srv = raw.get("serving", {})
        for k in ("challenge_ttl_s", "pool_refresh_s", "science_share", "recheck_rate"):
            if k in srv:
                setattr(s, k, type(getattr(s, k))(srv[k]))
        cloud = raw.get("cloud", {})
        if cloud:
            s.store = cloud.get("store", s.store)
            s.gcp_project = cloud.get("project", "")
            s.gcp_bucket = cloud.get("bucket", "")
        if "cors_origins" in raw.get("http", {}):
            s.cors_origins = list(raw["http"]["cors_origins"])
        if "site_keys" in raw:
            s.site_keys = {k: SiteKey(int(v.get("max_count", 10)), tuple(v.get("modes", ["none", "score"])))
                           for k, v in raw["site_keys"].items()}
    s.dev = os.environ.get("SKYTASK_DEV") == "1"
    return s
