"""Durable answer writes to Firestore `answers`, off the request path, with retries.

One background thread drains a queue. A failed batch goes back to the front and is retried with backoff, so a
short Firestore outage loses nothing (up to `max_pending` rows, then the oldest are dropped and logged).
Document ids are `<challenge_id>_<task_ref>`, so a retried write never duplicates an answer.
"""
from __future__ import annotations

import collections
import hashlib
import logging
import threading
import time
from datetime import datetime, timezone

log = logging.getLogger("skytask.answers")


def answer_rows(ch: dict, graded: list[dict], site_key: str | None) -> list[tuple[str, dict]]:
    sess = hashlib.sha256(ch["session_id"].encode()).hexdigest()[:20]
    now = datetime.now(timezone.utc).isoformat()
    return [(f"{ch['challenge_id']}_{g['task_ref']}",
             {"challenge_id": ch["challenge_id"], "session": sess, "site_key": site_key, "mode": ch["mode"],
              "task_id": g["task_id"], "kind": g["kind"], "holdout": bool(g["holdout"]), "choice": g["choice"],
              "correct": g["correct"], "counts_for_reliability": bool(g["counts_for_reliability"]),
              "elapsed_ms": int(g["elapsed_ms"]), "created_at": now}) for g in graded]


class AnswerWriter:
    def __init__(self, client, max_pending: int = 20000, backoff=(1, 2, 5, 10, 30, 60), start: bool = True):
        self.c, self.max_pending, self.backoff = client, max_pending, backoff
        self._q: collections.deque = collections.deque()
        self._cv = threading.Condition()
        self.written = self.dropped = 0
        self._fails = 0
        if start:
            threading.Thread(target=self._run, daemon=True, name="answer-writer").start()

    def put(self, rows: list[tuple[str, dict]]):
        with self._cv:
            self._q.extend(rows)
            while len(self._q) > self.max_pending:
                self._q.popleft()
                self.dropped += 1
            self._cv.notify()

    def pending(self) -> int:
        with self._cv:
            return len(self._q)

    def flush_once(self) -> bool:
        """Write up to 400 queued rows. True on success (or nothing to do)."""
        with self._cv:
            batch = [self._q.popleft() for _ in range(min(400, len(self._q)))]
        if not batch:
            return True
        try:
            self.c.commit([{"set": ("answers", did, row)} for did, row in batch])
        except Exception:
            with self._cv:
                self._q.extendleft(reversed(batch))  # keep order, retry later
            log.exception("Firestore answer write failed; %d rows waiting", self.pending())
            return False
        self.written += len(batch)
        return True

    def _run(self):
        while True:
            with self._cv:
                while not self._q:
                    self._cv.wait()
            if self.flush_once():
                self._fails = 0
            else:
                time.sleep(self.backoff[min(self._fails, len(self.backoff) - 1)])
                self._fails += 1


def summarize(rows: list[dict], now: datetime, delay_s: int = 600, min_holdout: int = 50) -> dict:
    """Public results from stored answers. Only answers older than `delay_s` count, accuracy is a whole percent
    and appears only after `min_holdout` holdout answers: together with the 10-minute refresh this keeps a client
    from diffing the number to learn which of its own tasks were controls."""
    cutoff = now.timestamp() - delay_s
    old = []
    for r in rows:
        try:
            ts = datetime.fromisoformat(str(r.get("created_at"))).timestamp()
        except ValueError:
            continue
        if ts <= cutoff:
            old.append(r)
    hold = [r for r in old if r.get("holdout") and r.get("correct") is not None]
    acc = round(100 * sum(1 for r in hold if r["correct"]) / len(hold)) if len(hold) >= min_holdout else None
    return {"volunteers": len({r.get("session") for r in rows if r.get("session")}),
            "holdout_accuracy_pct": acc,
            "results_updated_at": now.replace(microsecond=0).isoformat().replace("+00:00", "Z")}
