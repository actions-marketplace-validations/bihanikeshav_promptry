import threading
import time

import pytest

import promptry.storage as storage_mod
import promptry.writer as writer_mod
from promptry.config import get_config
from promptry.storage import get_storage, reset_storage
from promptry.writer import AsyncWriter


def _wait_until(pred, timeout=5.0, interval=0.01):
    """Poll pred() until truthy or timeout. Returns pred()'s last value."""
    deadline = time.monotonic() + timeout
    val = pred()
    while not val and time.monotonic() < deadline:
        time.sleep(interval)
        val = pred()
    return val


class _RunHolder:
    """Give the underlying storage an eval_run row to attach results to."""

    @staticmethod
    def new_run(storage, suite="s"):
        return storage.save_eval_run(suite_name=suite)


class TestAsyncWriter:

    def test_write_and_flush(self, storage):
        writer = AsyncWriter(storage)
        writer.save_prompt("test", "hello", "abc123")
        writer.flush()

        record = storage.get_prompt("test")
        assert record is not None
        assert record.content == "hello"

    def test_reads_go_through(self, storage):
        storage.save_prompt("direct", "content", "hash123")
        writer = AsyncWriter(storage)

        record = writer.get_prompt("direct")
        assert record.content == "content"


class TestAsyncDrain:
    """The background thread must actually deliver queued writes to storage."""

    def test_enqueued_write_reaches_storage(self, storage):
        run_id = storage.save_eval_run(suite_name="s")
        writer = AsyncWriter(storage)
        try:
            writer.save_eval_result(
                run_id=run_id, test_name="t0", assertion_type="contains",
                passed=True, score=1.0,
            )
            got = _wait_until(lambda: len(storage.get_eval_results(run_id)) == 1)
            assert got, "queued write never reached storage"
            results = storage.get_eval_results(run_id)
            assert results[0].test_name == "t0"
        finally:
            writer.close()

    def test_record_invocation_is_queued_and_delivered(self, storage):
        before = storage.count_invocations()
        writer = AsyncWriter(storage)
        try:
            # record_invocation returns 0 immediately (fire-and-forget)...
            assert writer.record_invocation("p", input_text="in", output_text="out") == 0
            # ...but the row must land in storage via the drain thread.
            assert _wait_until(lambda: storage.count_invocations() == before + 1)
        finally:
            writer.close()

    def test_write_ordering_preserved_under_load(self, storage):
        run_id = storage.save_eval_run(suite_name="ord")
        writer = AsyncWriter(storage)
        n = 200
        try:
            for i in range(n):
                writer.save_eval_result(
                    run_id=run_id, test_name=f"t{i}", assertion_type="contains",
                    passed=True, score=float(i), details={"seq": i},
                )
            assert _wait_until(lambda: len(storage.get_eval_results(run_id)) == n, timeout=10)
            results = storage.get_eval_results(run_id)
            # get_eval_results returns rows in insertion (rowid) order.
            seqs = [r.details["seq"] for r in results]
            assert seqs == list(range(n)), "queue did not preserve write order"
        finally:
            writer.close()


class _BlockingStorage:
    """Duck-typed storage whose writes block on an event, to force backpressure."""

    def __init__(self):
        self.release = threading.Event()
        self.first_started = threading.Event()
        self.saved = []
        self._lock = threading.Lock()
        self._first = True

    def save_eval_result(self, **kwargs):
        if self._first:
            self._first = False
            self.first_started.set()
            self.release.wait(10)
        with self._lock:
            self.saved.append(kwargs)

    def close(self):
        pass


class TestBackpressure:
    """A full queue must never silently drop: it falls back to a synchronous
    write (never blocks forever, never crashes the caller, never loses data)."""

    def test_queue_full_writes_synchronously_and_warns(self, caplog):
        st = _BlockingStorage()
        writer = AsyncWriter(st, max_queue=3)
        try:
            # First op is picked up by the drain thread and blocks inside it.
            writer.save_eval_result(run_id=1, test_name="blocker",
                                    assertion_type="c", passed=True)
            assert st.first_started.wait(5), "drain thread never started first op"

            # Fill the queue to capacity (drain is blocked, nothing drains).
            for i in range(3):
                writer.save_eval_result(run_id=1, test_name=f"q{i}",
                                        assertion_type="c", passed=True)
            assert _wait_until(lambda: writer.pending == 3), \
                f"queue should saturate at maxsize, got {writer.pending}"

            # Extra writes on a full queue must fall back to a synchronous
            # write (warn, but do NOT drop) — the queue never grows past max.
            with caplog.at_level("WARNING", logger="promptry.writer"):
                for i in range(2):
                    writer.save_eval_result(run_id=1, test_name=f"extra{i}",
                                            assertion_type="c", passed=True)
            assert any("write queue full" in r.message for r in caplog.records)
            assert writer.pending == 3
            # The overflow writes landed immediately, synchronously.
            assert _wait_until(
                lambda: {"extra0", "extra1"} <= {s["test_name"] for s in st.saved}
            ), "overflow writes were not written synchronously"
        finally:
            st.release.set()
            writer.close()

        # After release: in-flight op + 3 queued + 2 synchronous overflow = 6.
        # Nothing is lost.
        assert len(st.saved) == 6
        names = {s["test_name"] for s in st.saved}
        assert {"blocker", "q0", "q1", "q2", "extra0", "extra1"} == names


class _FlakyStorage:
    """First eval-result write raises; everything else succeeds."""

    def __init__(self):
        self.saved = []
        self.tags = []
        self._raised = False

    def save_eval_result(self, **kwargs):
        if not self._raised:
            self._raised = True
            raise RuntimeError("boom in drain")
        self.saved.append(kwargs)

    def tag_prompt(self, prompt_id, tag):
        self.tags.append((prompt_id, tag))

    def close(self):
        pass


class TestDrainResilience:
    """An exception in the drain thread must not kill the thread or process."""

    def test_error_in_drain_does_not_stop_thread(self, caplog):
        st = _FlakyStorage()
        writer = AsyncWriter(st)
        try:
            with caplog.at_level("ERROR", logger="promptry.writer"):
                # This one raises inside the drain...
                writer.save_eval_result(run_id=1, test_name="bad",
                                        assertion_type="c", passed=True)
                # ...and this one must still be processed afterwards.
                writer.tag_prompt(42, "prod")
                assert _wait_until(lambda: st.tags == [(42, "prod")])
            assert writer._thread.is_alive(), "drain thread died on an exception"
            assert any("async write failed" in r.message for r in caplog.records)
        finally:
            writer.close()


class TestFlushAndShutdown:
    """flush() and close() must drain pending work; atexit hook is registered."""

    def test_flush_registered_with_atexit(self, storage, monkeypatch):
        captured = []
        monkeypatch.setattr(writer_mod.atexit, "register",
                            lambda fn, *a, **k: captured.append(fn))
        writer = AsyncWriter(storage)
        try:
            assert writer.flush in captured, "flush was not registered with atexit"
        finally:
            writer.close()

    def test_flush_drains_pending_writes(self, storage):
        run_id = storage.save_eval_run(suite_name="flush")
        writer = AsyncWriter(storage)
        try:
            for i in range(50):
                writer.save_eval_result(run_id=run_id, test_name=f"t{i}",
                                        assertion_type="c", passed=True)
            writer.flush(timeout=10)
            # flush waited for the queue to empty; give the in-flight op a beat
            # then assert everything landed.
            assert _wait_until(lambda: len(storage.get_eval_results(run_id)) == 50)
        finally:
            writer.close()

    def test_close_drains_then_stops_thread(self):
        # close() closes the underlying storage, so use a recording stub we can
        # still inspect afterwards.
        st = _FlakyStorage()
        st._raised = True  # never raise -- record everything
        writer = AsyncWriter(st)
        for i in range(30):
            writer.save_eval_result(run_id=1, test_name=f"t{i}",
                                    assertion_type="c", passed=True)
        writer.close()
        # close() flushes and joins the daemon thread before returning.
        assert not writer._thread.is_alive()
        assert len(st.saved) == 30


class TestStorageModeSwitching:
    """get_storage() wires sync / async / off based on config.storage.mode."""

    @pytest.fixture(autouse=True)
    def _isolate_storage(self, tmp_path):
        reset_storage()
        get_config().storage.db_path = str(tmp_path / "mode.db")
        yield
        reset_storage()

    def test_sync_mode_returns_plain_sqlite(self):
        get_config().storage.mode = "sync"
        reset_storage()
        st = get_storage()
        assert isinstance(st, storage_mod.SQLiteStorage)
        assert not isinstance(st, AsyncWriter)

    def test_async_mode_wraps_in_async_writer(self):
        get_config().storage.mode = "async"
        reset_storage()
        st = get_storage()
        assert isinstance(st, AsyncWriter)
        # And a queued write through it actually reaches the wrapped sqlite db.
        run_id = st.save_eval_run(suite_name="modes")
        st.save_eval_result(run_id=run_id, test_name="t", assertion_type="c", passed=True)
        assert _wait_until(lambda: len(st.get_eval_results(run_id)) == 1)

    def test_off_mode_builds_no_storage_and_passes_through(self):
        from promptry.registry import track

        get_config().storage.mode = "off"
        reset_storage()
        out = track("some prompt content", "off-prompt")
        assert out == "some prompt content"
        # 'off' must short-circuit before any storage singleton is created.
        assert storage_mod._storage_instance is None
