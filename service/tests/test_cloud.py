"""Card #4: cloud task store, answer writer and the Firestore codec, against an in-memory fake (no network)."""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent))
from fake_cloud import FakeCloud  # noqa: E402

from app.answers import AnswerWriter, answer_rows  # noqa: E402
from app.gcp import decode_fields, encode_fields  # noqa: E402
from app.taskstore import CloudTaskStore, doc_to_task, task_to_doc  # noqa: E402

TASK = {"task_id": "t1", "field_id": "f1", "kind": "control_pos", "expected_answer": "moving", "holdout": 0,
        "master_path": "masters/t1.png", "frame_size": 64, "frame_count": 3, "tile_px": 96,
        "ring": {"x": 40.0, "y": 41.5, "r": 4}, "track": [[1.0, 2.0], [3.0, 4.0]], "fit_points": [[1, 2]],
        "frame_offsets_h": [0, 1.5, 3], "meta": {"edge_px": 8}}


def test_doc_roundtrip_and_codec():
    doc = task_to_doc(TASK)
    assert isinstance(doc["track"], str)  # Firestore has no nested arrays
    assert decode_fields(encode_fields({**doc, "status": "active", "n": None})) == {**doc, "status": "active", "n": None}
    t = doc_to_task(doc)
    assert t["track"] == TASK["track"] and t["ring"] == TASK["ring"] and t["holdout"] is False


def _seed(c, tid, status="active", **kw):
    c.cols.setdefault("tasks", {})[tid] = {**task_to_doc({**TASK, "task_id": tid, "master_path": f"masters/{tid}.png", **kw}),
                                           "status": status}
    c.objects[f"masters/{tid}.png"] = b"png-" + tid.encode()


def test_store_pool_refresh_retired_and_cache():
    c = FakeCloud()
    _seed(c, "a"); _seed(c, "b"); _seed(c, "old", status="retired")
    s = CloudTaskStore(c, refresh_s=0)
    assert {t["task_id"] for t in s.pool()} == {"a", "b"}
    assert s.get("old")["task_id"] == "old"          # retired tasks still grade old challenges
    assert s.master_bytes(s.get("a")) == b"png-a"
    n = c.calls
    s.master_bytes(s.get("a"))
    assert c.objects and c.calls >= n                 # strip served from cache (no extra object read)
    _seed(c, "new")
    assert "new" in {t["task_id"] for t in s.pool()}  # new tasks go live on refresh


def test_store_keeps_last_pool_when_firestore_fails():
    c = FakeCloud()
    _seed(c, "a")
    s = CloudTaskStore(c, refresh_s=0)
    assert len(s.pool()) == 1
    c.fail = 5
    assert [t["task_id"] for t in s.pool()] == ["a"]


def test_store_first_load_failure_raises():
    c = FakeCloud(); c.fail = 1
    with pytest.raises(RuntimeError):
        CloudTaskStore(c).pool()


def _graded(n=2):
    return [{"task_ref": f"r{i}", "task_id": f"t{i}", "kind": "control_pos", "holdout": i == 1, "choice": "moving",
             "correct": 1, "counts_for_reliability": i != 1, "elapsed_ms": 1200} for i in range(n)]


def test_answer_rows_hash_session_and_stable_ids():
    rows = answer_rows({"challenge_id": "C", "session_id": "secret-session", "mode": "none"}, _graded(), "demo")
    assert [r[0] for r in rows] == ["C_r0", "C_r1"]
    assert all("secret-session" not in str(r[1]) for r in rows)


def test_answer_writer_retries_without_loss_or_duplicates():
    c = FakeCloud()
    w = AnswerWriter(c, start=False)
    w.put(answer_rows({"challenge_id": "C", "session_id": "s", "mode": "none"}, _graded(3), "demo"))
    c.fail = 2
    assert w.flush_once() is False and w.flush_once() is False
    assert w.pending() == 3 and not c.cols.get("answers")
    assert w.flush_once() is True
    assert sorted(c.cols["answers"]) == ["C_r0", "C_r1", "C_r2"] and w.pending() == 0
    w.put(answer_rows({"challenge_id": "C", "session_id": "s", "mode": "none"}, _graded(3), "demo"))
    w.flush_once()
    assert len(c.cols["answers"]) == 3                # same ids: a re-sent answer overwrites, never duplicates


def test_answer_writer_caps_memory():
    w = AnswerWriter(FakeCloud(), max_pending=5, start=False)
    w.put([(f"id{i}", {}) for i in range(8)])
    assert w.pending() == 5 and w.dropped == 3
