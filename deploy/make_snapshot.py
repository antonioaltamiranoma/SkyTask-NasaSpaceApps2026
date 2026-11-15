"""Copy the generator's task database + the master strips of active tasks into deploy/_snapshot/.

The snapshot is baked into the PRIVATE Cloud Run image (phase A of card #3). It holds answer keys,
so it is git-ignored and must never be uploaded anywhere public.

Usage: python deploy/make_snapshot.py [GENERATOR_DATA_DIR]
Default: ../generator/data (repo cloned inside Nasa2026), falling back to ../generator/sample_data.
"""
from __future__ import annotations

import shutil
import sqlite3
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
OUT = REPO / "deploy" / "_snapshot"
DB = "skytask.sqlite3"


def pick_source(arg: str | None) -> Path:
    cands = [Path(arg)] if arg else [REPO.parent / "generator" / "data", REPO.parent / "generator" / "sample_data"]
    for c in cands:
        if (c / DB).exists():
            return c.resolve()
    sys.exit("No generator database found in: " + ", ".join(str(c) for c in cands))


def main() -> None:
    src = pick_source(sys.argv[1] if len(sys.argv) > 1 else None)
    if OUT.exists():
        shutil.rmtree(OUT)
    OUT.mkdir(parents=True)

    # Consistent copy even while the generator is running (WAL): use SQLite's backup API.
    s = sqlite3.connect(f"file:{src / DB}?mode=ro", uri=True)
    d = sqlite3.connect(OUT / DB)
    s.backup(d)
    s.close()
    d.execute("PRAGMA journal_mode=DELETE")
    rows = d.execute("SELECT kind, holdout, master_path FROM tasks WHERE status='active'").fetchall()
    d.close()

    copied, missing = 0, 0
    for _, _, mp in rows:
        p = (src / mp).resolve()
        if src not in p.parents or not p.exists():
            missing += 1
            continue
        dst = OUT / mp
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(p, dst)
        copied += 1

    counts: dict[str, int] = {}
    for kind, holdout, _ in rows:
        key = kind + (" (holdout)" if holdout else "")
        counts[key] = counts.get(key, 0) + 1
    print(f"Snapshot from {src}")
    for k in sorted(counts):
        print(f"  {k:24s} {counts[k]}")
    print(f"  master strips copied: {copied}, missing: {missing}")
    controls = sum(v for k, v in counts.items() if k.startswith("control") and "holdout" not in k)
    if controls < 2 or copied == 0:
        sys.exit("Not enough active controls to serve challenges; aborting.")


if __name__ == "__main__":
    main()
