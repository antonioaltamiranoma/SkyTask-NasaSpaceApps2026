"""Keep `run` from hanging forever on a network call that never answers.

1. Every requests call without a timeout (pyvo and astroquery make some) gets one: 30 s to connect, 300 s between
   bytes. Calls that set their own timeout keep it.
2. A watchdog thread: if nothing is logged for `stall_s` seconds (a field normally logs every 30-80 s), it logs an
   error and exits with code 3. start.bat / start.sh restart the generator on code 3; the database and the
   single-instance lock make the restart safe (the unfinished field is retried).
"""
from __future__ import annotations

import logging
import os
import threading
import time

log = logging.getLogger("skytask_gen")
STALL_EXIT_CODE = 3


def install_default_http_timeout(connect: float = 30, read: float = 300):
    import requests.sessions
    orig = requests.sessions.Session.request
    if getattr(orig, "_skytask_timeout", False):
        return

    def request(self, method, url, **kw):
        if kw.get("timeout") is None:
            kw["timeout"] = (connect, read)
        return orig(self, method, url, **kw)
    request._skytask_timeout = True
    requests.sessions.Session.request = request


class Watchdog(logging.Handler):
    def __init__(self, stall_s: float = 900, check_s: float = 30, exit_fn=None):
        super().__init__(level=logging.DEBUG)
        self.stall_s, self.check_s = stall_s, check_s
        self.last = time.monotonic()
        self.exit_fn = exit_fn or (lambda: os._exit(STALL_EXIT_CODE))
        self._stop = threading.Event()

    def emit(self, record):  # any log line counts as progress
        self.last = time.monotonic()

    def start(self):
        logging.getLogger().addHandler(self)
        threading.Thread(target=self._run, daemon=True, name="watchdog").start()
        return self

    def stop(self):
        self._stop.set()
        logging.getLogger().removeHandler(self)

    def _run(self):
        while not self._stop.wait(self.check_s):
            idle = time.monotonic() - self.last
            if idle > self.stall_s:
                log.error("no progress for %d min (a network call is probably stuck); restarting", idle // 60)
                for h in logging.getLogger().handlers:
                    try:
                        h.flush()
                    except Exception:
                        pass
                self.exit_fn()
                return
