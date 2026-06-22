"""Phase 6 #3: CoinDCX microstructure ingestion (the traded instrument, source of truth).

Order-book parse (best bid/ask, mid, spread_bps, depth imbalance), best-effort mark/funding,
the combine (None only when BOTH fail), the cache-aware get_coindcx, and context injection.
Offline (injected fetcher), deterministic (injected `now`).
"""
from homing_trade.repository import Repository
from homing_trade.signals import coindcx
from homing_trade.skills.llm_trader import LlmTrader
from homing_trade.models import Candle


def candles(n=40, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


# bids heavier than asks; best bid 100, best ask 101 -> mid 100.5, spread ~99.5 bps.
_OB = {"bids": {"100.0": "6", "99.5": "4", "99.0": "2"},
       "asks": {"101.0": "2", "101.5": "1", "102.0": "1"}}


def _fetch(ob=_OB, rt=None, ob_ok=True, rt_ok=True):
    def fetch(url, params):
        if url == coindcx.ORDERBOOK_URL:
            if not ob_ok:
                raise RuntimeError("ob down")
            return ob
        if url == coindcx.FUTURES_RT_URL:
            if not rt_ok:
                raise RuntimeError("rt down")
            return rt if rt is not None else {"prices": {}}
        raise AssertionError(f"unexpected url {url}")
    return fetch


def test_parse_orderbook_levels_spread_imbalance():
    ob = coindcx.parse_orderbook(_OB)
    assert ob["best_bid"] == 100.0 and ob["best_ask"] == 101.0 and ob["mid"] == 100.5
    assert round(ob["spread_bps"], 2) == round(1.0 / 100.5 * 10000, 2)
    # bid depth 6+4+2=12, ask depth 2+1+1=4 -> imbalance 12/16 = 0.75
    assert ob["imbalance"] == 0.75


def test_parse_orderbook_none_on_empty_or_bad():
    assert coindcx.parse_orderbook({"bids": {}, "asks": {"1": "1"}}) is None   # one side empty
    assert coindcx.parse_orderbook({}) is None
    assert coindcx.parse_orderbook({"bids": {"x": "y"}, "asks": {"1": "1"}}) is None  # unparseable


def test_fetch_futures_rt_parses_mark_and_funding():
    rt = {"prices": {"B-BTC_USDT": {"mark_price": "64010.2", "funding_rate": "0.00008"}}}
    out = coindcx.fetch_futures_rt("B-BTC_USDT", fetcher=_fetch(rt=rt))
    assert out == {"mark_price": 64010.2, "funding_rate": 0.00008}


def test_fetch_futures_rt_none_when_instrument_absent():
    assert coindcx.fetch_futures_rt("B-BTC_USDT", fetcher=_fetch(rt={"prices": {}})) is None


def test_fetch_coindcx_combines_orderbook_and_rt():
    rt = {"prices": {"B-BTC_USDT": {"mp": "64010.2", "fr": "0.00008"}}}   # short keys
    out = coindcx.fetch_coindcx("B-BTC_USDT", fetcher=_fetch(rt=rt))
    assert out["pair"] == "B-BTC_USDT" and out["imbalance"] == 0.75
    assert out["mark_price"] == 64010.2 and out["funding_rate"] == 0.00008


def test_fetch_coindcx_orderbook_only_when_rt_fails():
    out = coindcx.fetch_coindcx("B-BTC_USDT", fetcher=_fetch(rt_ok=False))
    assert out["imbalance"] == 0.75 and "mark_price" not in out          # rt absent, ob still lands


def test_fetch_coindcx_none_when_both_fail():
    assert coindcx.fetch_coindcx("B-BTC_USDT", fetcher=_fetch(ob_ok=False, rt_ok=False)) is None


def test_get_coindcx_fetches_caches_then_serves_fresh(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    now = 1_000_000_000_000
    out = coindcx.get_coindcx(repo, "B-BTC_USDT", fetcher=_fetch(), now=now)
    assert out["imbalance"] == 0.75
    assert repo.get_signal("coindcx", "B-BTC_USDT")["value"]["pair"] == "B-BTC_USDT"
    def boom(url, params): raise AssertionError("must not refetch while fresh")
    again = coindcx.get_coindcx(repo, "B-BTC_USDT", fetcher=boom, now=now + 60_000)
    assert again["imbalance"] == 0.75
    repo.close()


def test_get_coindcx_refetches_when_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    now = 1_000_000_000_000
    coindcx.get_coindcx(repo, "B-BTC_USDT", fetcher=_fetch(), now=now)
    heavier_asks = {"bids": {"100": "1"}, "asks": {"101": "9"}}            # imbalance 0.1
    out = coindcx.get_coindcx(repo, "B-BTC_USDT", fetcher=_fetch(ob=heavier_asks),
                              now=now + 300_001, max_age_sec=300)
    assert out["imbalance"] == 0.1
    repo.close()


def test_get_coindcx_falls_back_to_stale_on_failure(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    now = 1_000_000_000_000
    coindcx.get_coindcx(repo, "B-BTC_USDT", fetcher=_fetch(), now=now)
    def boom(url, params): raise RuntimeError("down")
    out = coindcx.get_coindcx(repo, "B-BTC_USDT", fetcher=boom, now=now + 9_000_000, max_age_sec=300)
    assert out["imbalance"] == 0.75                                       # stale cache, no crash
    repo.close()


def test_get_coindcx_none_when_no_cache_and_fetch_fails(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    def boom(url, params): raise RuntimeError("down")
    assert coindcx.get_coindcx(repo, "B-BTC_USDT", fetcher=boom, now=1) is None
    repo.close()


def test_coindcx_injected_into_ai_context():
    reading = {"pair": "B-BTC_USDT", "mid": 100.5, "spread_bps": 9.95, "imbalance": 0.75}
    t = LlmTrader(provider=lambda *a, **k: candles())
    t.add_context_provider("coindcx", lambda: reading)
    assert t._build_context(candles(), None)["coindcx"] == reading
