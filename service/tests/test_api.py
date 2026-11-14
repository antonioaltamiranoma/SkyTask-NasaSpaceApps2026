import io
import time

import numpy as np
import pytest
from fastapi.testclient import TestClient
from PIL import Image

from app.config import Settings, SiteKey
from app.main import create_app
from app.taskstore import LocalTaskStore
from conftest import FakeStore, make_pool, write_generator_db

PUBLIC_KEYS = {"task_ref", "image_url", "frame_count", "frame_size", "highlight", "frame_offsets_h", "options"}


def make_client(tmp_path, dev=False, store=None, **kw):
    tasks, masters = make_pool()
    s = Settings(service_db=tmp_path / "svc.sqlite3", static_dir=None, dev=dev,
                 site_keys={"k": SiteKey(10, ("none", "score", "token")), "narrow": SiteKey(3, ("none",))}, **kw)
    app = create_app(s, store or FakeStore(tasks, masters))
    return TestClient(app), app, tasks


def new_session(c, key="k"):
    r = c.post("/v1/sessions", json={"site_key": key})
    assert r.status_code == 200
    return r.json()["session_id"]


def test_public_task_whitelist_and_image(tmp_path):
    c, app, tasks = make_client(tmp_path)
    sid = new_session(c)
    r = c.post("/v1/challenge", json={"session_id": sid, "count": 4, "result": "none"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert set(body) == {"challenge_id", "expires_at", "tasks"}
    text = r.text
    for t in tasks:  # nothing server-side leaks: no task ids, field ids, kinds or answers
        assert t["task_id"] not in text and t["field_id"] not in text
    for word in ("control", "science", "holdout", "expected", "kind", "ra", "dec"):
        assert f'"{word}' not in text
    for t in body["tasks"]:
        assert set(t) == PUBLIC_KEYS
        assert t["options"] == ["moving", "not_moving"]
        assert set(t["highlight"]) == {"x", "y", "r"}
        assert all(h * 2 == int(h * 2) for h in t["frame_offsets_h"])
        img1 = c.get(t["image_url"])
        assert img1.status_code == 200 and img1.headers["content-type"] == "image/png"
        assert "no-store" in img1.headers["cache-control"]
        im = Image.open(io.BytesIO(img1.content))
        assert im.size == (t["frame_size"] * t["frame_count"], t["frame_size"])
        assert not im.info, "no PNG metadata chunks"
        # same image on reload (it must match the highlight already sent), bright object under the ring
        assert c.get(t["image_url"]).content == img1.content
        a = np.array(im)[:, : t["frame_size"]].astype(float)
        y, x = np.unravel_index(np.argmax(a), a.shape)
        assert np.hypot(x - t["highlight"]["x"], y - t["highlight"]["y"]) <= 2


def test_variants_differ_between_challenges(tmp_path):
    c, _, _ = make_client(tmp_path)
    hls = set()
    for _ in range(6):
        sid = new_session(c)
        body = c.post("/v1/challenge", json={"session_id": sid, "count": 10}).json()
        hls |= {(t["highlight"]["x"], t["highlight"]["y"]) for t in body["tasks"]}
    assert len(hls) > 10


def test_submit_none_and_once(tmp_path):
    c, app, _ = make_client(tmp_path)
    sid = new_session(c)
    ch = c.post("/v1/challenge", json={"session_id": sid, "count": 3, "result": "none"}).json()
    answers = [{"task_ref": t["task_ref"], "choice": "moving"} for t in ch["tasks"]]
    url = f"/v1/challenge/{ch['challenge_id']}/submit"
    assert c.post(url, json={"answers": answers[:-1]}).status_code == 422
    assert c.post(url, json={"answers": answers + answers[:1]}).status_code == 422
    r = c.post(url, json={"answers": answers})
    assert r.status_code == 200 and r.json() == {"recorded": True}
    assert c.post(url, json={"answers": answers}).status_code == 409
    rows = app.state.db.q("SELECT * FROM answers")
    assert len(rows) == 3 and all(r["elapsed_ms"] >= 0 for r in rows)


def test_score_mode_and_reliability(tmp_path):
    c, app, _ = make_client(tmp_path)
    store, db = app.state.store, app.state.db
    for _ in range(15):
        sid = new_session(c)
        ch = c.post("/v1/challenge", json={"session_id": sid, "count": 4, "result": "score"}).json()
        refs = {r["task_ref"]: store.get(r["task_id"]) for r in db.challenge_tasks(ch["challenge_id"])}
        answers = [{"task_ref": ref, "choice": t["expected_answer"] or "moving"} for ref, t in refs.items()]
        r = c.post(f"/v1/challenge/{ch['challenge_id']}/submit", json={"answers": answers})
        assert set(r.json()) == {"score"} and r.json()["score"] in (0, 25, 50, 75, 100)
        n_sci = sum(t["kind"] == "science" for t in refs.values())
        if n_sci == 0:
            assert r.json()["score"] == 100
        n_train = sum(t["kind"] != "science" and not t["holdout"] for t in refs.values())
        sess = db.session(sid)
        assert (sess["alpha"], sess["beta"]) == (3 + n_train, 1), "only non-holdout controls change reliability"


def test_one_task_per_field_across_challenges(tmp_path):
    c, app, tasks = make_client(tmp_path)
    sid = new_session(c)
    seen = []
    while True:
        r = c.post("/v1/challenge", json={"session_id": sid, "count": 2})
        if r.status_code != 200:
            assert r.status_code == 503 and r.json()["detail"]["error"] == "not_enough_tasks"
            break
        seen += [app.state.store.get(x["task_id"])["field_id"]
                 for x in app.state.db.challenge_tasks(r.json()["challenge_id"])]
    assert len(seen) == len(set(seen))


def test_errors(tmp_path):
    c, app, _ = make_client(tmp_path)
    assert c.post("/v1/sessions", json={"site_key": "nope"}).status_code == 403
    assert c.post("/v1/challenge", json={"session_id": "nope"}).status_code == 404
    sid = new_session(c)
    assert c.post("/v1/challenge", json={"session_id": sid, "count": 1}).status_code == 422
    assert c.post("/v1/challenge", json={"session_id": sid, "count": 11}).status_code == 422
    assert c.post("/v1/challenge", json={"session_id": sid, "result": "token"}).status_code == 501
    narrow = new_session(c, "narrow")
    assert c.post("/v1/challenge", json={"session_id": narrow, "result": "score"}).status_code == 403
    r = c.post("/v1/challenge", json={"session_id": narrow, "count": 10})
    assert len(r.json()["tasks"]) == 3, "count capped by the site key"
    ch = r.json()
    assert c.get(f"/v1/challenge/{ch['challenge_id']}/image/nope").status_code == 404
    app.state.db.x("UPDATE challenges SET expires_at=?", (time.time() - 1,))
    assert c.get(ch["tasks"][0]["image_url"]).status_code == 410
    answers = [{"task_ref": t["task_ref"], "choice": "moving"} for t in ch["tasks"]]
    assert c.post(f"/v1/challenge/{ch['challenge_id']}/submit", json={"answers": answers}).status_code == 410
    assert c.post(f"/v1/challenge/{ch['challenge_id']}/submit",
                  json={"answers": [{"task_ref": "x", "choice": "not_sure"}]}).status_code == 422


def test_dev_routes_absent_by_default(tmp_path):
    c, _, _ = make_client(tmp_path, dev=False)
    sid = new_session(c)
    ch = c.post("/v1/challenge", json={"session_id": sid}).json()
    assert c.get(f"/v1/dev/challenge/{ch['challenge_id']}").status_code == 404
    assert c.get("/v1/dev/pool").status_code == 404


def test_dev_routes_shape(tmp_path):
    c, _, _ = make_client(tmp_path, dev=True)
    sid = new_session(c)
    ch = c.post("/v1/challenge", json={"session_id": sid, "count": 4}).json()
    d = c.get(f"/v1/dev/challenge/{ch['challenge_id']}").json()
    assert set(d) == {"challenge_id", "mode", "mix", "tasks"}
    assert set(d["mix"]) == {"controls", "holdout_controls", "science_unsettled", "science_settled"}
    assert sum(d["mix"].values()) == 4
    assert [t["task_ref"] for t in d["tasks"]] == [t["task_ref"] for t in ch["tasks"]]
    for t in d["tasks"]:
        assert set(t) == {"task_ref", "task_id", "kind", "field_id", "holdout", "answer", "consensus_score", "votes",
                          "frame_count", "variant"}
        assert set(t["variant"]) == {"crop", "rot90", "flip"}
    p = c.get("/v1/dev/pool").json()
    assert {"by_kind", "fields", "active_tasks"} <= set(p)


def test_dev_refused_on_cloud_run(tmp_path, monkeypatch):
    monkeypatch.setenv("K_SERVICE", "skytask")
    with pytest.raises(RuntimeError):
        make_client(tmp_path, dev=True)


def test_local_store_reads_generator_db_read_only(tmp_path):
    tasks, masters = make_pool(n_control_fields=4, n_science=1)
    data = tmp_path / "gen"
    write_generator_db(data, tasks, masters)
    before = (data / "skytask.sqlite3").read_bytes()
    store = LocalTaskStore(data)
    assert {t["task_id"] for t in store.pool()} == {t["task_id"] for t in tasks}, "only active tasks"
    assert store.get("retired01")["kind"] == "control_pos"  # still readable for scoring old challenges
    con = store._connect()
    with pytest.raises(Exception):
        con.execute("DELETE FROM tasks")
    con.close()
    c, _, _ = make_client(tmp_path, dev=True, store=store)
    sid = new_session(c)
    ch = c.post("/v1/challenge", json={"session_id": sid, "count": 2}).json()
    assert c.get(ch["tasks"][0]["image_url"]).status_code == 200
    counts = c.get("/v1/dev/pool").json()
    assert counts["fields"]["total"] == 6 and counts["active_tasks"] == 9
    assert (data / "skytask.sqlite3").read_bytes() == before, "generator database untouched"
