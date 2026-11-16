"""Fixture: process ONE survey-mode field at a fixed position and time window (no target is passed, so any mover
must be found blind and labelled through JPL). Restartable: re-run the same command until it reports 'done'
(JPL calls are cached). Used to exercise the JPL / science paths that random fields rarely reach.

  python tests/fixture_field.py --settings S --id fx420 --ra 169.47 --dec -3.28 --mjd 61026.97 61027.55
  ... --unlabeled   pretend JPL knows nothing (forces the science path; use a throw-away data_dir!)
  ... --reprocess   queue the field again (tests that reprocessing does not duplicate tasks)
"""
import argparse
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from skytask_gen import config  # noqa: E402
from skytask_gen.field import survey_sequences  # noqa: E402
from skytask_gen.run import Generator  # noqa: E402

ap = argparse.ArgumentParser()
ap.add_argument("--settings", required=True)
ap.add_argument("--id", required=True)
ap.add_argument("--ra", type=float, required=True)
ap.add_argument("--dec", type=float, required=True)
ap.add_argument("--mjd", type=float, nargs=2, required=True)
ap.add_argument("--minutes", type=float, default=2.8)
ap.add_argument("--unlabeled", action="store_true")
ap.add_argument("--reprocess", action="store_true")
a = ap.parse_args()
cfg = config.load(a.settings)
g = Generator(cfg)
db = g.db
f = db.one("SELECT * FROM fields WHERE field_id=?", (a.id,))
if f is None:
    imgs = [r for r in g.irsa.sia_images(a.ra, a.dec, cfg["survey"]["collections"]) if a.mjd[0] <= r["mjd"] <= a.mjd[1]]
    seqs = survey_sequences(imgs, cfg["survey"])
    if not seqs:
        sys.exit(f"no sequence in window ({len(imgs)} images)")
    seq = max(seqs, key=len)
    db.add_field(a.id, "survey", a.ra, a.dec, g.size_deg, {"visits": seq, "n_images": len(imgs), "fixture": True})
    print(f"queued {a.id}: {len(imgs)} images, {len(seq)} visits")
elif a.reprocess:
    db.update_field(a.id, status="queued", attempts=0)
f = db.one("SELECT * FROM fields WHERE field_id=?", (a.id,))
if f["status"] == "done":
    print("already done:", f["outcome"]); sys.exit(0)
if a.unlabeled:
    g.jpl.known_per_frame = lambda S, *x, **k: [[] for _ in S["metas"]]
res = g.process(f, time.time() + a.minutes * 60)
f = db.one("SELECT * FROM fields WHERE field_id=?", (a.id,))
print("result:", res, "|", f["status"], f["outcome"], "jpl_calls", f["jpl_calls"], "seconds", f["seconds"])
for t in db.q("SELECT task_id, kind, confidence, status, holdout, designation, frame_count, meta FROM tasks WHERE field_id=?", (a.id,)):
    print("  task", t["task_id"], t["kind"], t["confidence"], t["status"], "holdout" if t["holdout"] else "", t["designation"],
          "frames", t["frame_count"], (t["meta"] or {}).get("frames_used"))
for c in db.q("SELECT ctype, n_frames, known, decision FROM candidates WHERE field_id=?", (a.id,)):
    print("  cand", c)
