"""Stuck-network protection: default HTTP timeout and the stall watchdog (no network needed)."""
import logging
import time

import pytest

from skytask_gen.watchdog import Watchdog, install_default_http_timeout


def test_watchdog_fires_only_without_log_lines():
    fired = []
    w = Watchdog(stall_s=0.3, check_s=0.05, exit_fn=lambda: fired.append(1)).start()
    try:
        for _ in range(8):  # steady logging keeps it quiet
            logging.getLogger("skytask_gen").warning("progress")
            time.sleep(0.05)
        assert not fired
        time.sleep(0.6)     # silence -> fires once
        assert fired == [1]
    finally:
        w.stop()


def test_requests_without_timeout_get_one():
    requests = pytest.importorskip("requests")
    seen = {}

    class Stop(Exception):
        pass

    def fake_send(self, request, **kw):
        seen["timeout"] = kw.get("timeout")
        raise Stop
    install_default_http_timeout(connect=7, read=11)
    orig = requests.Session.send
    requests.Session.send = fake_send
    try:
        with pytest.raises(Stop):
            requests.Session().get("http://example.invalid/")
        assert seen["timeout"] == (7, 11)
        with pytest.raises(Stop):
            requests.Session().get("http://example.invalid/", timeout=3)
        assert seen["timeout"] == 3
    finally:
        requests.Session.send = orig
