"""Phase 6 #8: the cross-cutting offline guarantee for the whole signals layer.

The per-feed suites (test_signals_fng/derivs/coindcx/price_ref/news) inject fetchers, so they never
exercise the REAL default `requests`-based fetcher (`_http_fetcher` / `_http_text_fetcher`). This
module closes that gap: it HARD-BLOCKS the network (socket.getaddrinfo raises, so any real egress fails
fast) and then drives every feed through its DEFAULT, un-injected path. The invariants it locks in:

  * every `fetch_*()` with no injected fetcher degrades to None under a network failure — never
    raises, never hangs (the block fails fast, no 10s timeout);
  * every cache-aware `get_*(repo)` with a cold cache degrades to None and writes NOTHING to
    signal_cache (a failed pull must not poison the cache with a bogus row);
  * the engine/AI loop therefore can never be crashed or stalled by an unreachable feed.

The `_blocked` sanity test proves the block is real, so the degrade assertions above are meaningful
(they pass because the code degrades, not because the network happened to be reachable).
"""
import socket

import pytest

from homing_trade.repository import Repository
from homing_trade.signals import fng, derivs, coindcx, price_ref, news


@pytest.fixture
def no_network(monkeypatch):
    """Make any real DNS resolution fail instantly, so requests/urllib egress fails fast."""
    def blocked(*a, **k):
        raise OSError("network disabled for offline signals test")
    monkeypatch.setattr(socket, "getaddrinfo", blocked)
    monkeypatch.setattr(socket.socket, "connect", blocked)   # belt-and-suspenders: block raw-IP egress too


def test_block_is_real(no_network):
    # Guard for the guard: with the block installed, a real HTTP attempt must raise. If this ever
    # stops raising, the degrade tests below would be passing for the wrong reason.
    import requests
    with pytest.raises(Exception):
        requests.get("https://api.alternative.me/fng/", timeout=5)


# --- every default fetch_* degrades to None under a hard network failure (no inject, no raise) ---
def test_fetch_fng_offline_is_none(no_network):
    assert fng.fetch_fng() is None


def test_fetch_derivs_offline_is_none(no_network):
    assert derivs.fetch_binance("BTCUSDT") is None
    assert derivs.fetch_derivs("BTCUSDT") is None


def test_fetch_coindcx_offline_is_none(no_network):
    assert coindcx.fetch_coindcx("B-BTC_USDT") is None


def test_fetch_price_ref_offline_is_none(no_network):
    assert price_ref.fetch_price_ref() is None


def test_fetch_news_offline_is_none(no_network):
    assert news.fetch_news() is None


# --- every cache-aware get_* with a cold cache degrades to None AND writes nothing ---
def test_get_paths_offline_degrade_and_write_nothing(no_network, tmp_path):
    repo = Repository.open(str(tmp_path / "s.db"))
    now = 1_000_000_000_000
    assert fng.get_fng(repo, now=now) is None
    assert derivs.get_derivs(repo, "BTCUSDT", now=now) is None
    assert coindcx.get_coindcx(repo, "B-BTC_USDT", now=now) is None
    assert price_ref.get_price_ref(repo, now=now) is None
    assert news.get_news(repo, now=now) is None
    # A failed pull must not poison the cache with a bogus row.
    assert repo.db.all_signals() == []
    repo.close()
