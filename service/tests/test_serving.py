import numpy as np
import pytest

from app.serving import NotEnoughTasks, grade, pick_tasks


def kinds(picked):
    return [t["kind"] for t, _ in picked]


@pytest.mark.parametrize("count", range(2, 11))
def test_mix_rules(pool, count):
    tasks, _ = pool
    for seed in range(50):
        picked = pick_tasks(tasks, {}, set(), count, np.random.default_rng(seed))
        k = kinds(picked)
        assert len(picked) == count
        assert sum(x.startswith("control") for x in k) >= 1
        assert sum(x == "science" for x in k) <= count // 2
        fields = [t["field_id"] for t, _ in picked]
        assert len(fields) == len(set(fields)), "one task per field"


def test_seen_fields_excluded(pool):
    tasks, _ = pool
    seen = {f"F{i:02d}" for i in range(10)}
    for seed in range(30):
        picked = pick_tasks(tasks, {}, seen, 2, np.random.default_rng(seed))
        assert not ({t["field_id"] for t, _ in picked} & seen)


def test_not_enough_tasks(pool):
    tasks, _ = pool
    with pytest.raises(NotEnoughTasks):
        pick_tasks(tasks, {}, {t["field_id"] for t in tasks}, 2, np.random.default_rng(0))
    science_only = [t for t in tasks if t["kind"] == "science"]
    with pytest.raises(NotEnoughTasks):  # never a challenge without a control
        pick_tasks(science_only, {}, set(), 2, np.random.default_rng(0))


def test_uncertain_science_first(pool):
    tasks, _ = pool
    cons = {"sci00": {"status": "open", "score": 0.7, "weighted_votes": 5.0},
            "sci01": {"status": "open", "score": 0.5, "weighted_votes": 0.0},
            "sci02": {"status": "open", "score": 0.6, "weighted_votes": 5.0},
            "sci03": {"status": "open", "score": 0.52, "weighted_votes": 5.0}}
    for seed in range(20):
        picked = pick_tasks(tasks, cons, set(), 2, np.random.default_rng(seed))
        sci = [t["task_id"] for t, _ in picked if t["kind"] == "science"]
        assert sci == ["sci01"]
        picked = pick_tasks(tasks, cons, {"S01"}, 2, np.random.default_rng(seed))
        assert [t["task_id"] for t, _ in picked if t["kind"] == "science"] == ["sci03"]


def test_settled_only_as_rechecks(pool):
    tasks, _ = pool
    cons = {f"sci{j:02d}": {"status": "confirmed", "score": 0.9, "weighted_votes": 9} for j in range(4)}
    for seed in range(30):
        picked = pick_tasks(tasks, cons, set(), 4, np.random.default_rng(seed), recheck_rate=0.0)
        assert "science" not in kinds(picked)
    picked = pick_tasks(tasks, cons, set(), 4, np.random.default_rng(1), recheck_rate=1.0)
    settled = [s for t, s in picked if t["kind"] == "science"]
    assert settled and all(s == "moving" for s in settled)
    assert sum(k.startswith("control") for k in kinds(picked)) >= 1


def test_controls_balanced(pool):
    tasks, _ = pool
    c = {"control_pos": 0, "control_neg": 0}
    for seed in range(400):
        for t, _ in pick_tasks(tasks, {}, set(), 2, np.random.default_rng(seed)):
            if t["kind"] in c:
                c[t["kind"]] += 1
    assert abs(c["control_pos"] - c["control_neg"]) / sum(c.values()) < 0.12


def test_order_shuffled(pool):
    tasks, _ = pool
    first = {pick_tasks(tasks, {}, set(), 4, np.random.default_rng(s))[0][0]["kind"] for s in range(60)}
    assert "science" in first and len(first) >= 2


def item(kind, choice, expected=None, holdout=False, settled=None):
    return {"kind": kind, "choice": choice, "expected_answer": expected, "holdout": holdout, "settled": settled}


def test_grade_steps_and_reliability():
    rng = np.random.default_rng(0)
    out, pct, delta = grade([item("control_pos", "moving", "moving"), item("control_neg", "moving", "not_moving")], rng)
    assert pct == 50 and delta == (1, 1)
    assert [o["counts_for_reliability"] for o in out] == [1, 1]
    _, pct, delta = grade([item("control_pos", "moving", "moving", holdout=True),
                           item("control_neg", "not_moving", "not_moving")], rng)
    assert pct == 100 and delta == (1, 0), "holdout controls never change reliability"
    _, pct, _ = grade([item("control_pos", "moving", "moving"), item("control_pos", "moving", "moving"),
                       item("control_neg", "moving", "not_moving")], rng)
    assert pct == 75  # 2/3 -> nearest 25% step
    _, pct, delta = grade([item("control_pos", "moving", "moving"),
                           item("science", "not_moving", settled="not_moving")], rng)
    assert pct == 100 and delta == (1, 0), "settled science scored by consensus, not reliability"


def test_grade_unsettled_science_random():
    vals = set()
    for s in range(40):
        _, pct, _ = grade([item("control_pos", "moving", "moving"), item("science", "moving")],
                          np.random.default_rng(s))
        vals.add(pct)
    assert vals == {50, 100}
    for p in vals:
        assert p % 25 == 0
