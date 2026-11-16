"""Card #3 checks that need no FastAPI: the snapshot script and the cloud settings. Run: python -m pytest deploy/tests"""
import importlib.util
import sqlite3
import sys
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location("make_snapshot", REPO / "deploy" / "make_snapshot.py")
ms = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ms)


def _db(d: Path, rows):
    (d / "masters").mkdir(parents=True)
    c = sqlite3.connect(d / "skytask.sqlite3")
    c.execute("PRAGMA journal_mode=WAL")
    c.execute("CREATE TABLE tasks(task_id, kind, holdout, status, master_path)")
    for r in rows:
        c.execute("INSERT INTO tasks VALUES(?,?,?,?,?)", r)
    c.commit()
    c.close()


def _run(monkeypatch, tmp_path, src):
    out = tmp_path / "out"
    monkeypatch.setattr(ms, "OUT", out)
    monkeypatch.setattr(sys, "argv", ["make_snapshot.py", str(src)])
    ms.main()
    return out


def test_snapshot_normalises_paths_and_drops_missing(monkeypatch, tmp_path):
    src = tmp_path / "gen"
    _db(src, [("a", "control_pos", 0, "active", "masters\\a.png"),
              ("b", "control_neg", 0, "active", "masters/b.png"),
              ("c", "science", 0, "active", "masters/c.png"),      # strip missing
              ("e", "control_pos", 0, "active", "../escape.png"),  # outside the data folder
              ("r", "control_pos", 0, "retired", "masters/r.png")])
    (src / "masters" / "a.png").write_bytes(b"a")
    (src / "masters" / "b.png").write_bytes(b"b")
    (tmp_path / "escape.png").write_bytes(b"x")
    out = _run(monkeypatch, tmp_path, src)
    c = sqlite3.connect(out / "skytask.sqlite3")
    active = dict(c.execute("SELECT task_id, master_path FROM tasks WHERE status='active'").fetchall())
    assert active == {"a": "masters/a.png", "b": "masters/b.png"}
    assert sorted(p.name for p in (out / "masters").iterdir()) == ["a.png", "b.png"]
    assert not (out / "escape.png").exists()
    assert c.execute("PRAGMA journal_mode").fetchone()[0] == "delete"


def test_snapshot_needs_both_control_kinds(monkeypatch, tmp_path):
    src = tmp_path / "gen"
    _db(src, [("a", "control_pos", 0, "active", "masters/a.png"),
              ("b", "control_neg", 1, "active", "masters/b.png")])  # only a holdout Not-moving
    for t in "ab":
        (src / "masters" / f"{t}.png").write_bytes(b"x")
    with pytest.raises(SystemExit):
        _run(monkeypatch, tmp_path, src)


def test_cloud_settings_resolve_inside_image():
    sys.path.insert(0, str(REPO / "service"))
    from app.config import load_settings
    s = load_settings(REPO / "service" / "settings.cloud.toml")
    assert s.generator_data_dir == (REPO / "data").resolve()      # /app/data in the image
    assert s.static_dir == (REPO / "explorer").resolve()
    assert s.widget_dir == (REPO / "widget").resolve()
    assert str(s.service_db).startswith("/tmp/")
    assert {"demo", "demo-game", "inspector"} <= set(s.site_keys)
    assert s.dev is False
