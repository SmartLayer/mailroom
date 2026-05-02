"""Integration test for the mu-backed local cache.

Skipped unless ``mu`` is on PATH and ``MAILROOM_TEST_MU_MAILDIR`` points
at an indexed maildir.  When opted in, the test invokes
``MuBackend.search`` for real and asserts the returned shape matches the
mailroom contract.
"""

import os
import shutil

import pytest

from mailroom.config import ImapBlock, LocalCacheConfig
from mailroom.local_cache import MuBackend

pytestmark = pytest.mark.integration


def _skip_unless_configured() -> None:
    if shutil.which("mu") is None:
        pytest.skip("mu binary not on PATH")
    if not os.environ.get("MAILROOM_TEST_MU_MAILDIR"):
        pytest.skip("MAILROOM_TEST_MU_MAILDIR not set")


def test_mu_search_real_index() -> None:
    """End-to-end smoke test against a real mu index."""
    _skip_unless_configured()

    cfg = LocalCacheConfig(
        indexer="mu",
        max_staleness_seconds=86400,
        mu_index=os.environ.get("MAILROOM_TEST_MU_INDEX"),
    )
    backend = MuBackend(cfg)

    block = ImapBlock(
        host="imap.example.com",
        port=993,
        username="test@example.com",
        password="password",
        use_ssl=True,
        maildir=os.environ["MAILROOM_TEST_MU_MAILDIR"],
    )

    results = backend.search(block, "from:alice", limit=3)

    assert isinstance(results, list)
    for rec in results:
        assert "message_id" in rec
        assert "path" in rec
