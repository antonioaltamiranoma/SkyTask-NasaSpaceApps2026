"""`python -m skytask_gen status`: what the generator has done so far."""
from __future__ import annotations
import time
from pathlib import Path

from .db import DB


def _du(p: Path):
    return sum(f.stat().st_size for f in Path(p).rglob("*") if f.is_file()) if Path(p).exists() else 0


def status(cfg):
    P = cfg["paths"]
    db = DB(P["db"])
    L = []
    L.append(f"database: {P['db']}")
    L.append("\nFIELDS")
    for r in db.q("SELECT source, status, COUNT(*) AS n, ROUND(AVG(seconds),0) AS s FROM fields GROUP BY source, status ORDER BY source, status"):
        L.append(f"  {r['source']:7s} {r['status']:10s} {r['n']:5d}   avg {r['s'] or 0:.0f} s/field")
    for r in db.q("SELECT outcome, COUNT(*) AS n FROM fields WHERE status IN ('done','failed') GROUP BY outcome ORDER BY n DESC LIMIT 12"):
        L.append(f"    outcome {r['outcome']!s:40s} {r['n']}")
    tried = db.get("survey_positions_tried", 0); noseq = db.get("survey_positions_no_sequence", 0)
    L.append(f"  survey positions queried: {tried} (no usable >=3-visit sequence: {noseq})")
    jc = db.one("SELECT COALESCE(SUM(jpl_calls),0) AS n, COUNT(CASE WHEN jpl_calls>0 THEN 1 END) AS f FROM fields")
    L.append(f"  JPL network calls: {jc['n']} in {jc['f']} fields")
    L.append("\nCONTROLS (active / reserve / retired)")
    c = cfg["controls"]
    tg = {("control_pos", 0): c["moving_target"], ("control_pos", 1): c["holdout_moving_target"],
          ("control_neg", 0): c["not_moving_target"], ("control_neg", 1): c["holdout_not_moving_target"]}
    for kind in ("control_pos", "control_neg"):
        for h in (0, 1):
            cnt = {s: db.one("SELECT COUNT(*) AS n FROM tasks WHERE kind=? AND holdout=? AND status=?", (kind, h, s))["n"]
                   for s in ("active", "reserve", "retired")}
            leg = db.one("SELECT COUNT(*) AS n FROM tasks WHERE kind=? AND holdout=? AND status='active' AND legacy=1", (kind, h))["n"]
            name = ("Moving" if kind == "control_pos" else "Not moving") + (" holdout" if h else "")
            L.append(f"  {name:20s} {cnt['active']:4d} / target {tg[(kind, h)]:3d}   reserve {cnt['reserve']:3d}   retired {cnt['retired']:3d}   (legacy {leg})")
    L.append("\nSCIENCE TASKS")
    for r in db.q("SELECT confidence, status, COUNT(*) AS n FROM tasks WHERE kind='science' GROUP BY confidence, status"):
        L.append(f"  {r['confidence']:5s} {r['status']:8s} {r['n']}")
    if not db.one("SELECT 1 AS x FROM tasks WHERE kind='science'"):
        L.append("  (none yet)")
    L.append("\nCANDIDATES")
    for r in db.q("SELECT ctype, decision, COUNT(*) AS n FROM candidates GROUP BY ctype, decision ORDER BY ctype, n DESC"):
        L.append(f"  {r['ctype']:8s} {r['decision']:24s} {r['n']}")
    L.append("\nQUEUES")
    q = db.one("SELECT COUNT(*) AS n FROM fields WHERE status='queued'")["n"]
    L.append(f"  fields queued: {q}")
    for r in db.q("SELECT action, state, COUNT(*) AS n FROM sync_queue GROUP BY action, state"):
        L.append(f"  sync {r['action']:6s} {r['state']:10s} {r['n']}")
    L.append("\nDISK")
    nt = db.one("SELECT COUNT(*) AS n FROM tasks WHERE legacy=0")["n"]
    m = _du(P["masters"])
    L.append(f"  data dir   {_du(P['data']) / 1e6:8.1f} MB  (masters {m / 1e6:.1f} MB, {m / max(nt, 1) / 1e3:.0f} kB per new task)")
    L.append(f"  cache dir  {_du(P['cache']) / 1e6:8.1f} MB  (cap {cfg['cache']['max_cache_mb']} MB)")
    last = db.one("SELECT MAX(updated_at) AS t FROM fields")["t"]
    if last:
        L.append(f"\nlast field update: {time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(last))}")
    db.close()
    return "\n".join(L)
