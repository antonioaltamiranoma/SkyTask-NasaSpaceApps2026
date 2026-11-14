"""Choosing the tasks of a challenge, and scoring a submission. Pure functions (no I/O), unit-tested.

Rules (CLAUDE.md):
- every challenge has >= 1 control and at most `science_share` (50%) unsettled science tasks;
- science: uncertain first (few weighted votes, score near 0.5); settled science only as ~5% re-checks;
- controls: Moving / Not moving by a fair coin per slot (so the mix never hints at the answers);
- one task per field per participant (and so also within a challenge);
- final order shuffled.
"""
from __future__ import annotations

import math

SETTLED_ANSWER = {"confirmed": "moving", "rejected": "not_moving"}


class NotEnoughTasks(Exception):
    pass


def settled_answer(task_id, consensus: dict):
    c = consensus.get(task_id)
    return SETTLED_ANSWER.get(c["status"]) if c else None


def pick_tasks(pool, consensus, seen_fields, count, rng, science_share=0.5, recheck_rate=0.05):
    """-> list of (task, settled_answer_or_None) in random order. Raises NotEnoughTasks."""
    used = set(seen_fields)
    avail = [t for t in pool if t["field_id"] not in used]
    science = [t for t in avail if t["kind"] == "science"]
    unsettled = [t for t in science if settled_answer(t["task_id"], consensus) is None]
    rechecks = [t for t in science if settled_answer(t["task_id"], consensus) is not None
                and rng.random() < recheck_rate]

    def priority(t):
        c = consensus.get(t["task_id"]) or {}
        votes = c.get("weighted_votes") or 0.0
        score = c.get("score")
        return (math.floor(votes), abs(score - 0.5) if score is not None else 0.0, rng.random())

    unsettled.sort(key=priority)
    rechecks.sort(key=lambda t: rng.random())

    max_unsettled = min(int(math.floor(count * science_share)), count - 1)
    max_science = count - 1  # always leave room for >= 1 control
    chosen = []

    def take(t, settled):
        chosen.append((t, settled))
        used.add(t["field_id"])

    for t in unsettled:
        if sum(1 for _, s in chosen if s is None) >= max_unsettled:
            break
        if t["field_id"] not in used:
            take(t, None)
    for t in rechecks:
        if len(chosen) >= max_science:
            break
        if t["field_id"] not in used:
            take(t, settled_answer(t["task_id"], consensus))

    controls = {k: [t for t in avail if t["kind"] == k] for k in ("control_pos", "control_neg")}
    n_controls = 0
    while len(chosen) < count:
        first = "control_pos" if rng.random() < 0.5 else "control_neg"
        second = "control_neg" if first == "control_pos" else "control_pos"
        pick = None
        for kind in (first, second):
            opts = [t for t in controls[kind] if t["field_id"] not in used]
            if opts:
                pick = opts[int(rng.integers(len(opts)))]
                break
        if pick is None:
            break
        take(pick, None)
        n_controls += 1
    if n_controls < 1 or len(chosen) < count:
        raise NotEnoughTasks(f"only {len(chosen)} tasks ({n_controls} controls) available for this participant")
    order = rng.permutation(len(chosen))
    return [chosen[i] for i in order]


def grade(items, rng):
    """items: dicts with kind, holdout, expected_answer, settled (or None), choice.
    -> (per-item dict with correct / counts_for_reliability / point, score percent 0-100 in 25% steps,
        reliability delta (d_alpha, d_beta))."""
    out, points, da, db = [], 0, 0, 0
    for it in items:
        is_control = it["kind"] in ("control_pos", "control_neg")
        correct = None
        if is_control:
            correct = it["choice"] == it["expected_answer"]
            point = int(correct)
        elif it.get("settled"):
            point = int(it["choice"] == it["settled"])
        else:
            point = int(rng.random() < 0.5)  # unsettled science: random draw (nothing to compare with yet)
        counts = bool(is_control and not it["holdout"])  # holdout controls only measure accuracy
        if counts:
            da, db = (da + 1, db) if correct else (da, db + 1)
        points += point
        out.append({**it, "correct": None if correct is None else int(correct),
                    "counts_for_reliability": int(counts), "point": point})
    pct = int(math.floor(points / len(items) * 4 + 0.5)) * 25 if items else 0
    return out, pct, (da, db)
