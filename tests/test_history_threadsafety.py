"""Thread-safety tests for SyncHistory.

Reproduces the production failure seen on the dhst-oikb-moodleservice daemon:
the daemon logs every sync via ``asyncio.to_thread(_history.log, ...)`` which
dispatches to rotating ThreadPoolExecutor worker threads, while SyncHistory
cached a single ``sqlite3.connect(...)`` connection (default
``check_same_thread=True``). The second call from a different worker thread
raised ``sqlite3.ProgrammingError: SQLite objects created in a thread can only
be used in that same thread`` and killed the scheduled task.
"""

from __future__ import annotations

import threading

from oikb.history import SyncHistory


def test_log_from_a_different_thread_does_not_raise(tmp_path):
    """A log() call must succeed even when the connection was created on
    another thread (the daemon's asyncio.to_thread pattern)."""
    hist = SyncHistory(db_path=tmp_path / "history.db")
    errors: list[Exception] = []

    def do_log(i: int) -> None:
        try:
            hist.log(source=f"src{i}", kb_id="kb", status="success", started_at=0.0)
        except Exception as e:  # noqa: BLE001 - capture to assert in main thread
            errors.append(e)

    # First call creates the cached connection on worker thread A.
    t1 = threading.Thread(target=do_log, args=(1,))
    t1.start()
    t1.join()

    # Second call runs on a *different* worker thread B — must not raise.
    t2 = threading.Thread(target=do_log, args=(2,))
    t2.start()
    t2.join()

    assert not errors, f"cross-thread log() raised: {errors!r}"
    assert len(hist.query(limit=10)) == 2


def test_concurrent_logs_are_serialised(tmp_path):
    """Many threads logging at once must not corrupt or drop rows
    (guards the lock added alongside check_same_thread=False)."""
    hist = SyncHistory(db_path=tmp_path / "history.db")
    errors: list[Exception] = []

    def do_log(i: int) -> None:
        try:
            hist.log(source=f"src{i}", kb_id="kb", status="success", started_at=0.0)
        except Exception as e:  # noqa: BLE001
            errors.append(e)

    threads = [threading.Thread(target=do_log, args=(i,)) for i in range(25)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert not errors, f"concurrent log() raised: {errors!r}"
    assert len(hist.query(limit=100)) == 25
