"""Idempotency-guard tests: overlapping syncs must not create duplicate KB
uploads. OWUI's /sync/diff marks a still-processing upload as "added", so a
manual run during a daemon cycle (or a file slower to embed than the sync
interval) would re-upload it. The guard skips filenames already present in the
KB or in-flight.
"""

from __future__ import annotations

import time

import httpx
import respx

from oikb.client import OikbClient
from oikb.sync import _filter_already_present


def test_filter_skips_present_filenames():
    added = [{"filename": "a.pdf"}, {"filename": "b.pdf"}, {"filename": "c.pdf"}]
    kept, skipped = _filter_already_present(added, {"a.pdf", "c.pdf"})
    assert [e["filename"] for e in kept] == ["b.pdf"]
    assert skipped == 2


def test_filter_noop_when_nothing_present():
    added = [{"filename": "a.pdf"}]
    kept, skipped = _filter_already_present(added, set())
    assert kept == added
    assert skipped == 0


_KB = "kb-123"


def _files_payload():
    now = time.time()
    return {
        "items": [
            # linked to this KB (any age) -> present
            {"meta": {"name": "in-kb.pdf", "collection_name": _KB}, "created_at": now - 99999},
            # uploaded, not yet linked, recent -> in-flight, present
            {"meta": {"name": "inflight.pdf", "collection_name": None}, "created_at": now - 30},
            # orphaned + stale -> NOT present (should be re-added)
            {"meta": {"name": "old-orphan.pdf", "collection_name": "file-xyz"}, "created_at": now - 99999},
            # belongs to a different KB -> NOT present here
            {"meta": {"name": "other-kb.pdf", "collection_name": "kb-OTHER"}, "created_at": now - 30},
        ]
    }


@respx.mock
def test_present_filenames_returns_in_kb_and_inflight_only():
    respx.get("http://owui/api/v1/files/").mock(
        return_value=httpx.Response(200, json=_files_payload())
    )
    client = OikbClient("http://owui", "tok")
    names = client.present_filenames(_KB)
    assert names == {"in-kb.pdf", "inflight.pdf"}


@respx.mock
def test_present_filenames_excludes_stale_orphan_after_window():
    now = time.time()
    payload = {"items": [
        {"meta": {"name": "pending.pdf", "collection_name": None}, "created_at": now - 5000},
    ]}
    respx.get("http://owui/api/v1/files/").mock(return_value=httpx.Response(200, json=payload))
    client = OikbClient("http://owui", "tok")
    # 5000s old + unlinked is past the default 900s window -> not "in-flight"
    assert client.present_filenames(_KB) == set()
    # ...but within a wider window it counts
    assert client.present_filenames(_KB, window_s=6000) == {"pending.pdf"}
