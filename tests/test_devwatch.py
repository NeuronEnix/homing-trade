# tests/test_devwatch.py — the pure file-watching core of the dev reloader.
import os
import sys

from homing_trade import devwatch


def _touch(path, content="x", mtime_ns=None):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        f.write(content)
    if mtime_ns is not None:
        os.utime(path, ns=(mtime_ns, mtime_ns))


def test_list_sources_finds_py_and_dotenv_ignores_noise(tmp_path):
    root = tmp_path / "pkg"
    _touch(str(root / "a.py"))
    _touch(str(root / "sub" / "b.py"))
    _touch(str(root / "data" / "ignored.py"))        # data/ is pruned
    _touch(str(root / "__pycache__" / "c.py"))        # __pycache__ is pruned
    _touch(str(root / ".hidden" / "d.py"))            # dot-dirs are pruned
    _touch(str(root / "notes.txt"))                   # non-.py ignored
    env = tmp_path / ".env"
    _touch(str(env), "K=V")

    cwd = os.getcwd()
    os.chdir(tmp_path)                                 # extra_files (.env) resolve against cwd
    try:
        found = devwatch.list_sources(str(root))
    finally:
        os.chdir(cwd)

    names = {os.path.basename(p) for p in found}
    assert names == {"a.py", "b.py", ".env"}


def test_changed_files_detects_modify_add_remove():
    old = {"/x/a.py": 1, "/x/b.py": 2}
    same = {"/x/a.py": 1, "/x/b.py": 2}
    assert devwatch.changed_files(old, same) == []
    # modified
    assert devwatch.changed_files(old, {"/x/a.py": 9, "/x/b.py": 2}) == ["/x/a.py"]
    # added + removed
    assert devwatch.changed_files(old, {"/x/a.py": 1, "/x/c.py": 3}) == ["/x/b.py", "/x/c.py"]


def test_snapshot_skips_missing_and_tracks_mtime(tmp_path):
    f = tmp_path / "a.py"
    _touch(str(f), mtime_ns=123_000_000_000)
    snap = devwatch.snapshot([str(f), str(tmp_path / "gone.py")])
    assert snap == {str(f): 123_000_000_000}


def test_snapshot_reflects_an_edit(tmp_path):
    f = str(tmp_path / "a.py")
    _touch(f, "v1", mtime_ns=1_000_000_000)
    before = devwatch.snapshot([f])
    _touch(f, "v2", mtime_ns=2_000_000_000)
    after = devwatch.snapshot([f])
    assert devwatch.changed_files(before, after) == [f]


def test_build_command_default_and_custom():
    assert devwatch.build_command([], python="/py") == ["/py", "-m", "homing_trade.web"]
    assert devwatch.build_command(["homing_trade.daemon"], python="/py") == \
        ["/py", "-m", "homing_trade.daemon"]
    assert devwatch.build_command(["homing_trade.web", "--no-browser"], python="/py") == \
        ["/py", "-m", "homing_trade.web", "--no-browser"]
    # default interpreter is the current one
    assert devwatch.build_command([])[0] == sys.executable


def test_reloader_restarts_on_change(tmp_path, monkeypatch):
    # Drive the loop with fake spawn/stop + a fake sleep that mutates a watched file once,
    # then raises KeyboardInterrupt to end the run. Asserts exactly one restart happened.
    f = str(tmp_path / "a.py")
    _touch(f, "v1", mtime_ns=1_000_000_000)
    events = []

    class FakeProc:
        returncode = None
        pid = 4242

        def poll(self):
            return None                      # child stays "alive" the whole test

    r = devwatch.Reloader([sys.executable, "-m", "x"], watch_root=str(tmp_path),
                          extra_files=(), interval=0.0, log=lambda *a: None)

    r._spawn = lambda: (events.append("spawn"), setattr(r, "proc", FakeProc()))[-1]
    r._stop_child = lambda: events.append("stop")

    step = {"n": 0}

    def fake_sleep(_):
        step["n"] += 1
        if step["n"] == 1:
            _touch(f, "v2", mtime_ns=2_000_000_000)   # edit after the first poll cycle
        elif step["n"] >= 3:
            raise KeyboardInterrupt
    monkeypatch.setattr(devwatch.time, "sleep", fake_sleep)

    r.run()
    # initial spawn, then stop+spawn for the restart, then a final stop in the finally block
    assert events == ["spawn", "stop", "spawn", "stop"]
