"""DEV ONLY mock of the Sky Task API, for building and testing the widget and demo pages.

Standard library only (no installs). It implements docs/api-contract.yaml with synthetic
star-field strips (no real SPHEREx data, no real answer keys), plus the dev routes the real
service exposes only when SKYTASK_DEV=1:

    GET /v1/dev/challenge/{challenge_id}   what was served and why (kinds, answers, mix)
    GET /v1/dev/pool                       the mock task pool

It also serves the website so everything is same-origin:

    /            -> explorer/
    /widget/...  -> widget/

Run from the repo root:   python explorer/dev/mock_server.py            (http://localhost:8080)
Options:                  --port 8080   --no-dev (hide /v1/dev routes)   --slow 0.4 (seconds per image)

Never deploy this. It is not the service (that is service/, card #5).
"""
from __future__ import annotations

import argparse
import json
import math
import mimetypes
import random
import secrets
import struct
import sys
import time
import zlib
from datetime import datetime, timedelta, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

REPO = Path(__file__).resolve().parents[2]
STATIC = {"/widget/": REPO / "widget", "/": REPO / "explorer"}
FRAME = 72          # frame_size in px
RING_R = 7.0        # ring radius in px
RNG = random.Random(2026)

# ---------------------------------------------------------------- synthetic pool
def _make_pool(n_fields: int = 40) -> list[dict]:
    pool = []
    for i in range(n_fields):
        r = random.Random(1000 + i)
        kind = ["control_pos", "control_neg", "control_pos", "control_neg", "science"][i % 5]
        n = r.choice([3, 3, 4])
        offsets = [round(k * 1.62 * 2) / 2 for k in range(n)]  # ~97 min apart, rounded to 0.5 h
        if r.random() < 0.3:
            offsets = [0.0, 1.5, 3.0, 6.5][:n]
        moving = kind == "control_pos" or (kind == "science" and r.random() < 0.5)
        speed = r.uniform(RING_R + 3, RING_R + 7) if moving else 0.0   # px per frame
        ang = r.uniform(0, 2 * math.pi)
        task = {
            "task_id": f"mock-{i:04d}",
            "field_id": f"field-{i:03d}",
            "kind": kind,
            "holdout": kind != "science" and i % 9 == 0,
            "answer": {"control_pos": "moving", "control_neg": "not_moving"}.get(kind),
            "frame_count": n,
            "frame_offsets_h": offsets,
            "seed": 5000 + i,
            "step": (speed * math.cos(ang), speed * math.sin(ang)),
            "consensus_score": round(r.uniform(0.3, 0.7), 2) if kind == "science" else None,
            "votes": r.randint(0, 4) if kind == "science" else None,
        }
        pool.append(task)
    return pool


POOL = _make_pool()
SESSIONS: dict[str, dict] = {}
CHALLENGES: dict[str, dict] = {}


# ---------------------------------------------------------------- PNG rendering (stdlib)
def _png(width: int, height: int, rows: list[bytearray]) -> bytes:
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    def chunk(tag: bytes, data: bytes) -> bytes:
        return struct.pack(">I", len(data)) + tag + data + struct.pack(">I", zlib.crc32(tag + data) & 0xFFFFFFFF)
    ihdr = struct.pack(">IIBBBBB", width, height, 8, 0, 0, 0, 0)  # 8-bit grayscale
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", ihdr) + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b"")


def _render(task: dict, variant: dict) -> tuple[bytes, dict]:
    """Render one strip: same star field in every frame, object moving by `step` if a mover."""
    fs, n = FRAME, task["frame_count"]
    r = random.Random(task["seed"])
    stars = [(r.uniform(0, fs), r.uniform(0, fs), r.uniform(15, 140), r.uniform(0.8, 1.6)) for _ in range(r.randint(18, 34))]
    # object: start near the centre, flux similar to a mid star
    cx, cy = fs / 2 + r.uniform(-8, 8), fs / 2 + r.uniform(-8, 8)
    sx, sy = task["step"]
    cx -= sx * (n - 1) / 2
    cy -= sy * (n - 1) / 2
    obj_flux = r.uniform(70, 130)
    noise = random.Random(variant["noise"])
    frames = []
    for k in range(n):
        img = [[12.0 + noise.gauss(0, 4) for _ in range(fs)] for _ in range(fs)]
        srcs = stars + [(cx + sx * k, cy + sy * k, obj_flux, 1.2)]
        for (x, y, f, s) in srcs:
            for yy in range(max(0, int(y - 4 * s)), min(fs, int(y + 4 * s) + 2)):
                for xx in range(max(0, int(x - 4 * s)), min(fs, int(x + 4 * s) + 2)):
                    img[yy][xx] += f * math.exp(-((xx - x) ** 2 + (yy - y) ** 2) / (2 * s * s))
        frames.append(img)
    # variant: rot90 / flip applied identically to every frame, ring transformed with it
    hx, hy = cx, cy
    for _ in range(variant["rot90"]):
        frames = [[list(row) for row in zip(*f[::-1])] for f in frames]  # rotate clockwise
        hx, hy = fs - 1 - hy, hx
    if variant["flip"]:
        frames = [[row[::-1] for row in f] for f in frames]
        hx = fs - 1 - hx
    rows = []
    for y in range(fs):
        row = bytearray()
        for f in frames:
            row.extend(max(0, min(255, int(v))) for v in f[y])
        rows.append(row)
    return _png(fs * n, fs, rows), {"x": round(hx, 1), "y": round(hy, 1), "r": RING_R}


# ---------------------------------------------------------------- challenge logic
def _new_challenge(session_id: str, count: int, mode: str) -> dict:
    sess = SESSIONS[session_id]
    avail = [t for t in POOL if t["field_id"] not in sess["fields"]]
    controls = [t for t in avail if t["kind"] != "science"]
    science = [t for t in avail if t["kind"] == "science"]
    if mode == "token":
        n_sci = 0 if count < 2 else RNG.randint(0, count // 2)
    else:
        n_sci = RNG.randint(0, count // 2)
    n_sci = min(n_sci, len(science))
    picked = RNG.sample(controls, max(1, count - n_sci)) + RNG.sample(science, n_sci)
    RNG.shuffle(picked)
    cid = "ch_" + secrets.token_urlsafe(10)
    tasks = []
    for t in picked:
        sess["fields"].add(t["field_id"])
        variant = {"rot90": RNG.randint(0, 3), "flip": RNG.random() < 0.5, "crop": [0, 0], "noise": RNG.randint(0, 10**9)}
        ref = secrets.token_urlsafe(8)
        png, hl = _render(t, variant)
        tasks.append({"task_ref": ref, "task": t, "variant": variant, "png": png, "highlight": hl})
    ch = {
        "challenge_id": cid, "session_id": session_id, "mode": mode, "tasks": tasks,
        "expires_at": (datetime.now(timezone.utc) + timedelta(minutes=10)).isoformat(timespec="seconds"),
        "issued": time.time(), "submitted": False,
    }
    CHALLENGES[cid] = ch
    return ch


def _public(ch: dict) -> dict:
    return {
        "challenge_id": ch["challenge_id"],
        "expires_at": ch["expires_at"],
        "tasks": [{
            "task_ref": x["task_ref"],
            "image_url": f"/v1/challenge/{ch['challenge_id']}/image/{x['task_ref']}",
            "frame_count": x["task"]["frame_count"],
            "frame_size": FRAME,
            "highlight": x["highlight"],
            "frame_offsets_h": x["task"]["frame_offsets_h"],
            "options": ["moving", "not_moving"],
        } for x in ch["tasks"]],
    }


def _score(ch: dict, answers: dict) -> dict:
    mode = ch["mode"]
    if mode == "none":
        return {"recorded": True}
    if mode == "score":
        pts = 0
        for x in ch["tasks"]:
            t, a = x["task"], answers.get(x["task_ref"])
            if t["answer"]:
                pts += a == t["answer"]
            else:  # unsettled science: random draw
                pts += RNG.random() < 0.5
        pct = 100 * pts / len(ch["tasks"])
        return {"score": int(round(pct / 25) * 25)}
    controls = [x for x in ch["tasks"] if x["task"]["answer"]]
    passed = all(answers.get(x["task_ref"]) == x["task"]["answer"] for x in controls)
    if passed:
        return {"passed": True, "token": "mock_" + secrets.token_urlsafe(24)}
    return {"passed": False, "next_challenge_id": None}


# ---------------------------------------------------------------- HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "SkyTaskMock/0.1"
    dev = True
    slow = 0.0

    def log_message(self, fmt, *args):  # quieter
        sys.stderr.write("[mock] " + (fmt % args) + "\n")

    def _send(self, code: int, body: bytes, ctype: str, extra: dict | None = None):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.send_header("Cache-Control", "no-store")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(body)

    def _json(self, code: int, obj):
        self._send(code, json.dumps(obj).encode(), "application/json")

    def _body(self) -> dict:
        n = int(self.headers.get("Content-Length") or 0)
        try:
            return json.loads(self.rfile.read(n) or b"{}")
        except json.JSONDecodeError:
            return {}

    def do_OPTIONS(self):
        self._send(204, b"", "text/plain", {"Access-Control-Allow-Methods": "GET, POST, OPTIONS"})

    def do_POST(self):
        path = urlparse(self.path).path
        b = self._body()
        if path == "/v1/sessions":
            if not b.get("site_key"):
                return self._json(400, {"error": "site_key required"})
            sid = "s_" + secrets.token_urlsafe(10)
            SESSIONS[sid] = {"site_key": b["site_key"], "fields": set()}
            return self._json(200, {"session_id": sid})
        if path == "/v1/challenge":
            sid = b.get("session_id")
            if sid not in SESSIONS:
                return self._json(401, {"error": "unknown session"})
            count = max(2, min(10, int(b.get("count") or 2)))
            mode = b.get("result") or "none"
            if mode not in ("none", "score", "token"):
                return self._json(400, {"error": "bad result mode"})
            if len([t for t in POOL if t["field_id"] not in SESSIONS[sid]["fields"]]) < count:
                SESSIONS[sid]["fields"].clear()  # mock only: recycle so demos never run dry
            return self._json(200, _public(_new_challenge(sid, count, mode)))
        parts = path.strip("/").split("/")
        if len(parts) == 4 and parts[:2] == ["v1", "challenge"] and parts[3] == "submit":
            ch = CHALLENGES.get(parts[2])
            if not ch:
                return self._json(404, {"error": "unknown challenge"})
            if ch["submitted"]:
                return self._json(409, {"error": "already submitted"})
            answers = {a.get("task_ref"): a.get("choice") for a in b.get("answers", [])}
            if set(answers) != {x["task_ref"] for x in ch["tasks"]} or not set(answers.values()) <= {"moving", "not_moving"}:
                return self._json(400, {"error": "answer every task with moving or not_moving"})
            ch["submitted"] = True
            ch["answers"] = answers
            ch["elapsed_s"] = round(time.time() - ch["issued"], 2)
            return self._json(200, _score(ch, answers))
        if path == "/v1/siteverify":
            ok = str(b.get("token", "")).startswith("mock_")
            return self._json(200, {"success": ok, "challenge_ts": datetime.now(timezone.utc).isoformat(timespec="seconds"),
                                    "hostname": "localhost", "error_codes": [] if ok else ["invalid-input-response"]})
        return self._json(404, {"error": "not found"})

    def do_GET(self):
        path = urlparse(self.path).path
        parts = path.strip("/").split("/")
        if len(parts) == 5 and parts[:2] == ["v1", "challenge"] and parts[3] == "image":
            ch = CHALLENGES.get(parts[2])
            x = next((x for x in (ch or {}).get("tasks", []) if x["task_ref"] == parts[4]), None)
            if not x:
                return self._json(404, {"error": "not found"})
            if self.slow:
                time.sleep(self.slow)
            return self._send(200, x["png"], "image/png")
        if path == "/v1/stats":
            done = [c for c in CHALLENGES.values() if c["submitted"]]
            return self._json(200, {"answers": sum(len(c["tasks"]) for c in done), "participants": len(SESSIONS),
                                    "holdout_correct": 0, "holdout_total": 0, "candidates_open": 8,
                                    "candidates_confirmed": 0, "candidates_rejected": 0})
        if path == "/v1/fields":
            return self._json(200, [])
        if path.startswith("/v1/dev/"):
            if not self.dev:
                return self._json(404, {"error": "not found"})
            if path == "/v1/dev/pool":
                return self._json(200, [{k: v for k, v in t.items() if k not in ("seed", "step")} for t in POOL])
            if len(parts) == 4 and parts[2] == "challenge":
                ch = CHALLENGES.get(parts[3])
                if not ch:
                    return self._json(404, {"error": "unknown challenge"})
                ts = [x["task"] for x in ch["tasks"]]
                return self._json(200, {
                    "challenge_id": ch["challenge_id"], "mode": ch["mode"],
                    "mix": {"controls": sum(t["kind"] != "science" and not t["holdout"] for t in ts),
                            "holdout_controls": sum(t["holdout"] for t in ts),
                            "science_unsettled": sum(t["kind"] == "science" for t in ts),
                            "science_settled": 0},
                    "tasks": [{"task_ref": x["task_ref"], "task_id": x["task"]["task_id"], "kind": x["task"]["kind"],
                               "field_id": x["task"]["field_id"], "holdout": x["task"]["holdout"],
                               "answer": x["task"]["answer"], "consensus_score": x["task"]["consensus_score"],
                               "votes": x["task"]["votes"], "frame_count": x["task"]["frame_count"],
                               "variant": {k: x["variant"][k] for k in ("crop", "rot90", "flip")},
                               "given": (ch.get("answers") or {}).get(x["task_ref"])} for x in ch["tasks"]],
                })
            return self._json(404, {"error": "not found"})
        if path.startswith("/v1/"):
            return self._json(404, {"error": "not found"})
        return self._static(path)

    do_HEAD = do_GET

    def _static(self, path: str):
        for prefix, root in STATIC.items():
            if path.startswith(prefix) or path + "/" == prefix:
                rel = path[len(prefix):] or "index.html"
                target = (root / rel).resolve()
                if target.is_dir():
                    target = target / "index.html"
                if root.resolve() not in target.parents or not target.is_file() or "dev" in target.relative_to(root.resolve()).parts[:1]:
                    return self._json(404, {"error": "not found"})
                ctype = mimetypes.guess_type(target.name)[0] or "application/octet-stream"
                return self._send(200, target.read_bytes(), ctype)
        return self._json(404, {"error": "not found"})


def main():
    ap = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    ap.add_argument("--port", type=int, default=8080)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--no-dev", action="store_true", help="hide the /v1/dev routes")
    ap.add_argument("--slow", type=float, default=0.0, help="delay per image in seconds")
    a = ap.parse_args()
    Handler.dev = not a.no_dev
    Handler.slow = a.slow
    mimetypes.add_type("text/javascript", ".js")
    srv = ThreadingHTTPServer((a.host, a.port), Handler)
    print(f"Sky Task MOCK API (dev only) on http://{a.host}:{a.port}  dev routes: {'on' if Handler.dev else 'off'}")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
