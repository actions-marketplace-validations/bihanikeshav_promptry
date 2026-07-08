import json
import os
import sys
import threading
import time

import pytest
from unittest.mock import patch, MagicMock

from promptry import scheduler


def _wait_until(pred, timeout=10.0, interval=0.02):
    deadline = time.monotonic() + timeout
    val = pred()
    while not val and time.monotonic() < deadline:
        time.sleep(interval)
        val = pred()
    return val


class TestScheduler:

    @pytest.fixture(autouse=True)
    def _temp_dir(self, tmp_path, monkeypatch):
        monkeypatch.setattr(scheduler, "_PROMPTRY_DIR", tmp_path)
        monkeypatch.setattr(scheduler, "_PID_FILE", tmp_path / "monitor.pid")
        monkeypatch.setattr(scheduler, "_LOG_FILE", tmp_path / "monitor.log")
        monkeypatch.setattr(scheduler, "_STATE_FILE", tmp_path / "monitor.json")
        self.tmp = tmp_path

    def test_is_running_no_pid_file(self):
        assert not scheduler.is_running()

    def test_is_running_stale_pid(self):
        (self.tmp / "monitor.pid").write_text("9999999")
        assert not scheduler.is_running()

    def test_stop_no_monitor(self):
        with pytest.raises(RuntimeError, match="No monitor running"):
            scheduler.stop()

    @patch("subprocess.Popen")
    def test_start_creates_pid_and_state(self, mock_popen):
        mock_proc = MagicMock()
        mock_proc.pid = 12345
        mock_popen.return_value = mock_proc

        pid = scheduler.start("my_suite", "my_module", interval=60)

        assert pid == 12345
        assert (self.tmp / "monitor.pid").read_text() == "12345"
        state = json.loads((self.tmp / "monitor.json").read_text())
        assert state["suite"] == "my_suite"
        assert state["interval_minutes"] == 60

    @patch("subprocess.Popen")
    def test_start_when_already_running(self, mock_popen, monkeypatch):
        (self.tmp / "monitor.pid").write_text("12345")
        monkeypatch.setattr(scheduler, "is_running", lambda: True)

        with pytest.raises(RuntimeError, match="already running"):
            scheduler.start("suite", "module")

    @patch("os.kill")
    def test_stop_removes_pid_file(self, mock_kill):
        (self.tmp / "monitor.pid").write_text("12345")

        pid = scheduler.stop()
        assert pid == 12345
        assert not (self.tmp / "monitor.pid").exists()


_PIPELINE_SRC = "def run(x):\n    return 'hello there world'\n"

_PASS_SUITE = """
suites:
  - name: sched-suite
    pipeline: schedpipe:run
    cases:
      - input: "hi"
        expect:
          - contains: "hello"
"""

_FAIL_SUITE = """
suites:
  - name: sched-fail
    pipeline: schedpipe:run
    cases:
      - input: "hi"
        expect:
          - contains: "zzzzz-never-present"
"""


class TestSchedulerLoop:
    """Exercise _run_loop / status() with a real (local) suite + real storage."""

    @pytest.fixture(autouse=True)
    def _env(self, tmp_path, monkeypatch):
        # Redirect all scheduler state files into tmp.
        monkeypatch.setattr(scheduler, "_PROMPTRY_DIR", tmp_path)
        monkeypatch.setattr(scheduler, "_PID_FILE", tmp_path / "monitor.pid")
        monkeypatch.setattr(scheduler, "_STATE_FILE", tmp_path / "monitor.json")
        monkeypatch.setattr(scheduler, "_LOG_FILE", tmp_path / "monitor.log")

        # Point the storage singleton at a throwaway db so run_suite + the
        # DriftMonitor both write to / read from a real sqlite we control.
        import promptry.storage as storage_mod
        from promptry.storage import Storage, reset_storage
        db = Storage(db_path=tmp_path / "sched.db")
        storage_mod._storage_instance = db
        self.storage = db
        self.tmp = tmp_path

        # signal.signal() only works on the main thread; the loop runs in a
        # worker thread here, so neutralize registration (a collaborator, not
        # the loop logic under test).
        monkeypatch.setattr(scheduler.signal, "signal", lambda *a, **k: None)

        # Replace the *name* `time` inside the scheduler module with a fast
        # stand-in so the inter-run sleep is instant. This rebinds scheduler's
        # namespace only -- the real time module (used by this test's polling)
        # is untouched.
        class _FastTime:
            sleep = staticmethod(lambda *a, **k: None)
        monkeypatch.setattr(scheduler, "time", _FastTime)

        scheduler._shutdown = False
        yield
        scheduler._shutdown = True
        reset_storage()

    def _write_suite(self, text):
        (self.tmp / "schedpipe.py").write_text(_PIPELINE_SRC, encoding="utf-8")
        path = self.tmp / "evals.yaml"
        path.write_text(text, encoding="utf-8")
        sys.path.insert(0, str(self.tmp))
        return str(path)

    def _run_loop_thread(self, suite, path, interval=1):
        t = threading.Thread(
            target=scheduler._run_loop, args=(suite, path, interval), daemon=True,
        )
        t.start()
        return t

    def _cleanup_path(self):
        if str(self.tmp) in sys.path:
            sys.path.remove(str(self.tmp))
        sys.modules.pop("schedpipe", None)

    def test_loop_runs_suite_each_interval_and_writes_results(self):
        path = self._write_suite(_PASS_SUITE)
        t = self._run_loop_thread("sched-suite", path)
        try:
            # Two eval runs proves the loop iterated more than once.
            ran = _wait_until(
                lambda: len(self.storage.get_eval_runs("sched-suite")) >= 2, timeout=10
            )
            scheduler._shutdown = True
            t.join(timeout=5)
            assert ran, "loop did not run the suite at least twice"
            assert not t.is_alive(), "loop did not exit after shutdown"
            runs = self.storage.get_eval_runs("sched-suite")
            assert len(runs) >= 2
            assert all(r.overall_pass for r in runs), "passing suite recorded as fail"
        finally:
            scheduler._shutdown = True
            t.join(timeout=5)
            self._cleanup_path()

    def test_loop_updates_state_file_and_status_reports_it(self):
        path = self._write_suite(_PASS_SUITE)
        # Make is_running() true so status() will read our state file: point the
        # PID file at this live test process.
        (self.tmp / "monitor.pid").write_text(str(os.getpid()))

        t = self._run_loop_thread("sched-suite", path)
        try:
            got = _wait_until(
                lambda: (self.tmp / "monitor.json").exists()
                and "last_run" in json.loads((self.tmp / "monitor.json").read_text()),
                timeout=10,
            )
            scheduler._shutdown = True
            t.join(timeout=5)
            assert got, "loop never wrote last_run into the state file"

            st = scheduler.status()
            assert st is not None
            assert "last_run" in st
            assert st["last_pass"] is True
            assert st["last_score"] == pytest.approx(1.0)
        finally:
            scheduler._shutdown = True
            t.join(timeout=5)
            self._cleanup_path()

    def test_loop_notifies_on_regression(self, monkeypatch):
        calls = []
        import promptry.notifications as notifs
        monkeypatch.setattr(
            notifs, "notify_regression",
            lambda result, details="": calls.append((result.overall_pass, details)),
        )
        path = self._write_suite(_FAIL_SUITE)
        t = self._run_loop_thread("sched-fail", path)
        try:
            got = _wait_until(lambda: len(calls) >= 1, timeout=10)
            scheduler._shutdown = True
            t.join(timeout=5)
            assert got, "failing suite did not trigger notify_regression"
            # It was called because the suite failed, not because of drift.
            assert calls[0][0] is False
        finally:
            scheduler._shutdown = True
            t.join(timeout=5)
            self._cleanup_path()

    def test_shutdown_stops_loop_cleanly(self):
        path = self._write_suite(_PASS_SUITE)
        t = self._run_loop_thread("sched-suite", path)
        try:
            assert _wait_until(
                lambda: len(self.storage.get_eval_runs("sched-suite")) >= 1, timeout=10
            )
            scheduler._shutdown = True
            t.join(timeout=5)
            assert not t.is_alive(), "loop thread still running after shutdown flag"
        finally:
            scheduler._shutdown = True
            t.join(timeout=5)
            self._cleanup_path()
