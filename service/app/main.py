"""Sky Task API (docs/api-contract.yaml), local-first.

Run locally: service/start_local.bat (Windows) or service/start_local.sh
(uvicorn --factory app.main:create_app). Serves the API under /v1 and the
explorer/ folder (demo pages + widget) at /, so one command gives http://localhost:8080.
"""
from __future__ import annotations

import os
import secrets
import sys
import time
from datetime import datetime, timezone
from typing import List, Literal

import numpy as np
from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import Response
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from .config import REPO_DIR, Settings, load_settings
from .serving import NotEnoughTasks, grade, pick_tasks
from .state import State
from .taskstore import LocalTaskStore, TaskStore, variant_meta

sys.path.insert(0, str(REPO_DIR / "generator"))
from skytask_gen.variants import make_variant_debug  # noqa: E402  (single source of truth for variants)

OPTIONS = ["moving", "not_moving"]


class SessionIn(BaseModel):
    site_key: str


class ChallengeIn(BaseModel):
    session_id: str
    count: int = Field(2, ge=2, le=10)
    result: Literal["none", "score", "token"] = "none"


class AnswerIn(BaseModel):
    task_ref: str
    choice: Literal["moving", "not_moving"]


class SubmitIn(BaseModel):
    answers: List[AnswerIn]


def _err(status, code, msg=None):
    return HTTPException(status, {"error": code, **({"message": msg} if msg else {})})


def _iso(ts):
    return datetime.fromtimestamp(ts, tz=timezone.utc).isoformat().replace("+00:00", "Z")


def _render(store: TaskStore, task: dict, seed: int):
    png, hl, dbg = make_variant_debug(store.master_bytes(task), variant_meta(task), np.random.default_rng(seed))
    variant = {"crop": [int(dbg["origin"][0]), int(dbg["origin"][1])], "rot90": int(dbg["k"]), "flip": bool(dbg["flip"])}
    return png, hl, variant


def create_app(settings: Settings | None = None, store: TaskStore | None = None) -> FastAPI:
    s = settings or load_settings()
    if s.dev and os.environ.get("K_SERVICE"):
        raise RuntimeError("SKYTASK_DEV=1 must never be set on Cloud Run (it exposes answer keys)")
    if store is None:
        store = LocalTaskStore(s.generator_data_dir, s.pool_refresh_s)
        if not store.db_path.exists():
            raise SystemExit(f"Sky Task: generator database not found at {store.db_path}\n"
                             "Set generator_data_dir in service/settings.local.toml (see the README).")
    state = State(s.service_db)

    app = FastAPI(title="Sky Task API", version="0.1.0")
    app.state.settings, app.state.store, app.state.db = s, store, state
    app.add_middleware(CORSMiddleware, allow_origins=s.cors_origins, allow_methods=["GET", "POST"],
                       allow_headers=["Content-Type"])

    @app.post("/v1/sessions")
    def create_session(body: SessionIn):
        if body.site_key not in s.site_keys:
            raise _err(403, "invalid_site_key")
        sid = secrets.token_urlsafe(16)
        state.add_session(sid, body.site_key)
        return {"session_id": sid}

    @app.post("/v1/challenge")
    def create_challenge(body: ChallengeIn, request: Request):
        sess = state.session(body.session_id)
        if not sess:
            raise _err(404, "unknown_session")
        key = s.site_keys.get(sess["site_key"])
        if key is None:
            raise _err(403, "invalid_site_key")
        if body.result not in key.modes:
            raise _err(403, "mode_not_allowed", f"site key allows {list(key.modes)}")
        if body.result == "token":
            raise _err(501, "not_implemented", "token mode arrives with card #9")
        count = min(body.count, key.max_count)
        rng = np.random.default_rng(secrets.randbits(64))
        try:
            picked = pick_tasks(store.pool(), state.consensus(), state.seen_fields(body.session_id), count, rng,
                                s.science_share, s.recheck_rate)
        except NotEnoughTasks as e:
            raise _err(503, "not_enough_tasks", str(e))
        cid = secrets.token_urlsafe(16)
        items = []
        for pos, (t, settled) in enumerate(picked):
            seed = secrets.randbits(63)
            _, hl, variant = _render(store, t, seed)
            items.append({"task_ref": secrets.token_urlsafe(9), "position": pos, "task_id": t["task_id"],
                          "settled": settled, "seed": seed, "highlight": hl, "variant": variant, "task": t})
        expires = state.save_challenge(cid, body.session_id, body.result, s.challenge_ttl_s, items,
                                       {it["task"]["field_id"] for it in items})
        return {"challenge_id": cid, "expires_at": _iso(expires), "tasks": [{
            "task_ref": it["task_ref"],
            "image_url": str(request.url_for("challenge_image", challenge_id=cid, task_ref=it["task_ref"])),
            "frame_count": int(it["task"]["frame_count"]),
            "frame_size": int(it["task"]["frame_size"]),
            "highlight": it["highlight"],
            "frame_offsets_h": [round(float(h) * 2) / 2 for h in it["task"]["frame_offsets_h"]],
            "options": OPTIONS,
        } for it in items]}

    def _open_challenge(challenge_id):
        ch = state.challenge(challenge_id)
        if not ch:
            raise _err(404, "unknown_challenge")
        if time.time() > ch["expires_at"]:
            raise _err(410, "challenge_expired")
        return ch

    @app.get("/v1/challenge/{challenge_id}/image/{task_ref}", name="challenge_image")
    def challenge_image(challenge_id: str, task_ref: str):
        _open_challenge(challenge_id)
        ct = next((r for r in state.challenge_tasks(challenge_id) if r["task_ref"] == task_ref), None)
        task = store.get(ct["task_id"]) if ct else None
        if not ct or not task:
            raise _err(404, "unknown_task")
        png, _, _ = _render(store, task, ct["seed"])  # same seed -> same crop/orientation as the highlight sent
        return Response(png, media_type="image/png", headers={"Cache-Control": "private, no-store"})

    @app.post("/v1/challenge/{challenge_id}/submit")
    def submit(challenge_id: str, body: SubmitIn):
        ch = _open_challenge(challenge_id)
        if ch["submitted_at"]:
            raise _err(409, "already_submitted")
        cts = {r["task_ref"]: r for r in state.challenge_tasks(challenge_id)}
        refs = [a.task_ref for a in body.answers]
        if len(refs) != len(set(refs)) or set(refs) != set(cts):
            raise _err(422, "answers_mismatch", "answer every task of the challenge exactly once")
        elapsed_ms = int((time.time() - ch["created_at"]) * 1000)
        items = []
        for a in body.answers:
            ct = cts[a.task_ref]
            t = store.get(ct["task_id"]) or {}
            items.append({"task_ref": a.task_ref, "task_id": ct["task_id"], "kind": t.get("kind", "science"),
                          "holdout": bool(t.get("holdout")), "expected_answer": t.get("expected_answer"),
                          "settled": ct["settled"], "choice": a.choice, "elapsed_ms": elapsed_ms})
        graded, pct, delta = grade(items, np.random.default_rng(secrets.randbits(64)))
        result = {"recorded": True} if ch["mode"] == "none" else {"score": pct}
        if not state.record_submission(ch, graded, delta, result):
            raise _err(409, "already_submitted")
        return result

    if s.dev:
        @app.get("/v1/dev/challenge/{challenge_id}")
        def dev_challenge(challenge_id: str):
            ch = state.challenge(challenge_id)
            if not ch:
                raise _err(404, "unknown_challenge")
            cons = state.consensus()
            tasks, mix = [], {"controls": 0, "holdout_controls": 0, "science_unsettled": 0, "science_settled": 0}
            for ct in state.challenge_tasks(challenge_id):
                t = store.get(ct["task_id"]) or {}
                kind = t.get("kind")
                if kind == "science":
                    mix["science_settled" if ct["settled"] else "science_unsettled"] += 1
                elif t.get("holdout"):
                    mix["holdout_controls"] += 1
                else:
                    mix["controls"] += 1
                c = cons.get(ct["task_id"]) or {}
                tasks.append({"task_ref": ct["task_ref"], "task_id": ct["task_id"], "kind": kind,
                              "field_id": t.get("field_id"), "holdout": bool(t.get("holdout")),
                              "answer": t.get("expected_answer") or ct["settled"],
                              "consensus_score": c.get("score"), "votes": state.votes(ct["task_id"]),
                              "frame_count": t.get("frame_count"), "variant": ct["variant"]})
            return {"challenge_id": challenge_id, "mode": ch["mode"], "mix": mix, "tasks": tasks}

        @app.get("/v1/dev/pool")
        def dev_pool():
            return {**store.counts(), "active_tasks": len(store.pool()),
                    "consensus_rows": len(state.consensus())}

    if s.static_dir and s.static_dir.is_dir():
        app.mount("/", StaticFiles(directory=str(s.static_dir), html=True), name="static")
    return app

