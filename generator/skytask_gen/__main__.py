"""Command line: python -m skytask_gen {run,status,export,retire,preview,import-legacy} [--settings FILE]"""
from __future__ import annotations
import argparse
import sys

from . import config


def main(argv=None):
    ap = argparse.ArgumentParser(prog="skytask_gen", description="SPHEREx micro-task generator")
    ap.add_argument("--settings", help="settings file (default: settings.toml next to the package)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("run", help="keep generating tasks (Ctrl+C stops; re-run resumes)")
    r.add_argument("--max-fields", type=int, default=None)
    r.add_argument("--minutes", type=float, default=None)
    sub.add_parser("status", help="print counts")
    e = sub.add_parser("export", help="write public/server JSONL + one rendered variant per active task")
    e.add_argument("--out", default=None)
    e.add_argument("--seed", type=int, default=None)
    rt = sub.add_parser("retire", help="retire tasks (a reserve control of the same kind/split replaces each)")
    rt.add_argument("task_ids", nargs="+")
    pv = sub.add_parser("preview", help="review sheet WITH answers: masters + request-time variants")
    pv.add_argument("--n", type=int, default=12)
    pv.add_argument("--variants", type=int, default=3)
    pv.add_argument("--out", default=None)
    sub.add_parser("import-legacy", help="import pipeline-prep tasks_v8 controls (also done automatically once)")
    a = ap.parse_args(argv)
    cfg = config.load(a.settings)

    if a.cmd == "run":
        from .run import Generator
        from .watchdog import Watchdog, install_default_http_timeout
        install_default_http_timeout()
        stall_min = float(cfg.get("run", {}).get("stall_restart_minutes", 15))
        if stall_min > 0:
            Watchdog(stall_s=stall_min * 60).start()
        Generator(cfg).run(max_fields=a.max_fields, max_minutes=a.minutes)
    elif a.cmd == "status":
        from .status import status
        print(status(cfg))
    elif a.cmd == "export":
        from .export import export
        out, summary = export(cfg, a.out, a.seed)
        print(f"exported to {out}: {summary}")
    elif a.cmd == "retire":
        from .db import DB
        from .store import make_store
        db = DB(cfg["paths"]["db"]); st = make_store(cfg, db)
        for tid in a.task_ids:
            t = db.one("SELECT * FROM tasks WHERE task_id=?", (tid,))
            if not t:
                print(f"{tid}: not found"); continue
            st.set_status(tid, "retired")
            msg = f"{tid}: retired"
            if t["kind"] != "science" and t["status"] == "active":
                rep = db.one("SELECT task_id FROM tasks WHERE kind=? AND holdout=? AND status='reserve' ORDER BY created_at LIMIT 1",
                             (t["kind"], t["holdout"]))
                if rep:
                    st.set_status(rep["task_id"], "active"); msg += f", replaced by reserve {rep['task_id']}"
            print(msg)
    elif a.cmd == "preview":
        from .preview import preview
        print("review sheet (contains answers, do not serve):", preview(cfg, a.n, a.variants, a.out))
    elif a.cmd == "import-legacy":
        from .run import import_legacy, setup_logging
        from .db import DB
        from .store import make_store
        setup_logging(cfg)
        db = DB(cfg["paths"]["db"])
        print(import_legacy(cfg, db, make_store(cfg, db)), "legacy controls imported")
    return 0


if __name__ == "__main__":
    sys.exit(main())
