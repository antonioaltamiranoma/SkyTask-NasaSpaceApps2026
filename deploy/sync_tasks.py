"""Keep the cloud task pool in step with the generator's local database(s).

Run on the PC that runs the generator (needs only Python 3.10+ and the Google Cloud CLI, logged in):
    python deploy\\sync_tasks.py --loop 600

Every pass:
  - active local tasks missing in the cloud: strip -> private bucket (masters/<task_id>.png), record -> Firestore `tasks`
  - cloud tasks no longer active locally: marked `retired` in Firestore (never deleted: old answers point at them)
Idempotent: re-running uploads nothing twice. The live API picks changes up within a minute.
"""
from __future__ import annotations

import argparse
import sqlite3
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "service"))
from app.gcp import Client  # noqa: E402
from app.taskstore import POOL_COLS, task_to_doc, JSON_COLS  # noqa: E402

import json  # noqa: E402

DEFAULT_DBS = [REPO.parent / "generator" / "data" / "skytask.sqlite3",
               REPO.parent / "generator" / "sample_data" / "skytask.sqlite3"]


def local_tasks(db: Path) -> dict[str, dict]:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True, timeout=30)
    con.row_factory = sqlite3.Row
    try:
        rows = con.execute(f"SELECT {','.join(POOL_COLS)} FROM tasks WHERE status='active'").fetchall()
    finally:
        con.close()
    out = {}
    for r in rows:
        t = dict(r)
        for k in JSON_COLS:
            if isinstance(t.get(k), str):
                t[k] = json.loads(t[k])
        t["_src"] = (db.parent / t["master_path"].replace("\\", "/")).resolve()
        t["_db"] = str(db.parent.name)
        out[t["task_id"]] = t
    return out


def one_pass(c: Client, dbs: list[Path], allow_empty: bool) -> str:
    local: dict[str, dict] = {}
    for db in dbs:
        local.update(local_tasks(db))
    remote = {d["_id"]: d.get("status") for d in c.query("tasks", select=["status"])}
    add = [t for tid, t in local.items() if remote.get(tid) != "active"]
    retire = [tid for tid, st in remote.items() if st == "active" and tid not in local]
    if not local and retire and not allow_empty:
        return "no active local tasks found; refusing to retire the whole cloud pool (use --allow-empty)"
    now = datetime.now(timezone.utc).isoformat()
    added = 0
    for t in add:
        if not t["_src"].is_file():
            print(f"  skip {t['task_id']}: strip missing ({t['_src']})")
            continue
        obj = f"masters/{t['task_id']}.png"
        c.put_object(obj, t["_src"].read_bytes())
        doc = task_to_doc({**t, "master_path": obj})
        c.commit([{"set": ("tasks", t["task_id"], {**doc, "status": "active", "source": t["_db"], "synced_at": now})}])
        added += 1
    if retire:
        c.commit([{"patch": ("tasks", tid, {"status": "retired", "synced_at": now})} for tid in retire])
    active = len([1 for tid, st in remote.items() if st == "active" and tid in local]) + added
    return f"local active {len(local)} | uploaded {added} | retired {len(retire)} | cloud active {active}"


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--db", action="append", help="generator database (repeatable); default: generator/data + sample_data")
    ap.add_argument("--project", default="sky-task-c4048")
    ap.add_argument("--bucket", default="sky-task-c4048-masters")
    ap.add_argument("--loop", type=int, default=0, help="seconds between passes (0 = run once)")
    ap.add_argument("--allow-empty", action="store_true")
    a = ap.parse_args()
    dbs = [Path(p) for p in a.db] if a.db else [p for p in DEFAULT_DBS if p.exists()]
    if not dbs:
        sys.exit("No generator database found. Pass --db PATH\\TO\\skytask.sqlite3")
    print("Syncing from:", *dbs, sep="\n  ")
    c = Client(a.project, a.bucket)
    while True:
        try:
            print(time.strftime("%H:%M:%S"), one_pass(c, dbs, a.allow_empty), flush=True)
        except Exception as e:  # keep looping through network hiccups
            print(time.strftime("%H:%M:%S"), "sync failed:", e, flush=True)
            if not a.loop:
                raise
        if not a.loop:
            break
        time.sleep(a.loop)


if __name__ == "__main__":
    main()
