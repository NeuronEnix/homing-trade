"""Phase 6 #2: derivatives ingestion (Binance perp funding + open interest).

fetch/parse + OI best-effort, cross-venue funding_skew, the cache-aware get_derivs
(fresh/stale/fallback), the symbol mapping, and injection into the AI context via the generic
context-provider registry. Offline (injected fetcher), deterministic (injected `now`).
"""
from homing_trade.repository import Repository
from homing_trade.signals import derivs
from homing_trade.skills.llm_trader import LlmTrader
from homing_trade.models import Candle


def candles(n=40, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


# A fetcher routing the two Binance endpoints by URL.
def _binance(funding="0.00012", mark="64000.5", oi="123456.7", ts=1700000000000, oi_ok=True):
    def fetch(url, params):
        if url == derivs.BINANCE_PREMIUM_URL:
            return {"symbol": params["symbol"], "lastFundingRate": funding,
                    "markPrice": mark, "time": ts}
        if url == derivs.BINANCE_OI_URL:
            if not oi_ok:
                raise RuntimeError("oi endpoint down")
            return {"symbol": params["symbol"], "openInterest": oi, "time": ts}
        raise AssertionError(f"unexpected url {url}")
    return fetch


def test_binance_symbol_mapping():
    assert derivs.binance_symbol("B-BTC_USDT") == "BTCUSDT"
    assert derivs.binance_symbol("B-ETH_USDT") == "ETHUSDT"


def test_fetch_binance_parses_funding_and_oi():
    r = derivs.fetch_binance("BTCUSDT", fetcher=_binance(funding="0.0003", oi="999.5"))
    assert r["venue"] == "binance" and r["funding_rate"] == 0.0003
    assert r["mark_price"] == 64000.5 and r["open_interest"] == 999.5


def test_fetch_binance_oi_is_best_effort():
    # If only the OI call fails, funding still returns with open_interest=None.
    r = derivs.fetch_binance("BTCUSDT", fetcher=_binance(oi_ok=False))
    assert r["funding_rate"] == 0.00012 and r["open_interest"] is None


def test_fetch_binance_none_when_funding_fails():
    def fetch(url, params): raise RuntimeError("down")
    assert derivs.fetch_binance("BTCUSDT", fetcher=fetch) is None


def test_funding_skew_single_and_multi_venue():
    assert derivs.funding_skew({"binance": 0.001}) == {"mean": 0.001, "spread": 0.0,
                                                       "venues": {"binance": 0.001}}
    sk = derivs.funding_skew({"binance": 0.001, "okx": 0.003})
    assert sk["mean"] == 0.002 and abs(sk["spread"] - 0.002) < 1e-12
    assert derivs.funding_skew({}) is None
    assert derivs.funding_skew({"x": None}) is None          # non-numeric dropped -> None


def test_fetch_derivs_aggregates_with_skew():
    out = derivs.fetch_derivs("BTCUSDT", fetcher=_binance(funding="0.0002"))
    assert out["symbol"] == "BTCUSDT" and len(out["venues"]) == 1
    assert out["funding_skew"]["mean"] == 0.0002


def test_get_derivs_fetches_and_caches(tmp_path):
    repo = Repository.open(str(tmp_path / "d.db"))
    calls = []
    def fetch(url, params):
        calls.append(url)
        return _binance(funding="0.0005")(url, params)
    out = derivs.get_derivs(repo, "BTCUSDT", fetcher=fetch, now=1_000_000_000_000)
    assert out["funding_skew"]["mean"] == 0.0005
    assert repo.get_signal("derivs", "BTCUSDT")["value"]["symbol"] == "BTCUSDT"
    repo.close()


def test_get_derivs_serves_fresh_cache_then_refetches_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "d.db"))
    now = 1_000_000_000_000
    derivs.get_derivs(repo, "BTCUSDT", fetcher=_binance(funding="0.0001"), now=now)
    def boom(url, params): raise AssertionError("must not refetch while fresh")
    fresh = derivs.get_derivs(repo, "BTCUSDT", fetcher=boom, now=now + 60_000, max_age_sec=900)
    assert fresh["funding_skew"]["mean"] == 0.0001                       # served from cache
    later = derivs.get_derivs(repo, "BTCUSDT", fetcher=_binance(funding="0.0009"),
                              now=now + 900_001, max_age_sec=900)         # stale -> refetch
    assert later["funding_skew"]["mean"] == 0.0009
    repo.close()


def test_get_derivs_falls_back_to_stale_on_failure(tmp_path):
    repo = Repository.open(str(tmp_path / "d.db"))
    now = 1_000_000_000_000
    derivs.get_derivs(repo, "BTCUSDT", fetcher=_binance(funding="0.0007"), now=now)
    def boom(url, params): raise RuntimeError("down")
    out = derivs.get_derivs(repo, "BTCUSDT", fetcher=boom, now=now + 10_000_000, max_age_sec=900)
    assert out["funding_skew"]["mean"] == 0.0007                         # stale cache, no crash
    repo.close()


def test_get_derivs_none_when_no_cache_and_fetch_fails(tmp_path):
    repo = Repository.open(str(tmp_path / "d.db"))
    def boom(url, params): raise RuntimeError("down")
    assert derivs.get_derivs(repo, "BTCUSDT", fetcher=boom, now=1) is None
    repo.close()


def test_derivs_injected_into_ai_context_via_registry():
    reading = {"symbol": "BTCUSDT", "venues": [{"venue": "binance", "funding_rate": 0.0002}],
               "funding_skew": {"mean": 0.0002, "spread": 0.0}, "ts": 1000}
    t = LlmTrader(provider=lambda *a, **k: candles())
    t.add_context_provider("derivatives", lambda: reading)
    ctx = t._build_context(candles(), None)
    assert ctx["derivatives"] == reading


def test_context_provider_failure_omits_field_not_crash():
    t = LlmTrader(provider=lambda *a, **k: candles())
    t.add_context_provider("derivatives", lambda: (_ for _ in ()).throw(RuntimeError("x")))
    assert "derivatives" not in t._build_context(candles(), None)        # degraded, no crash


def test_multiple_context_providers_coexist():
    t = LlmTrader(provider=lambda *a, **k: candles())
    t.add_context_provider("fear_greed", lambda: {"value": 20})
    t.add_context_provider("derivatives", lambda: {"funding_skew": {"mean": 0.001}})
    ctx = t._build_context(candles(), None)
    assert ctx["fear_greed"] == {"value": 20} and ctx["derivatives"]["funding_skew"]["mean"] == 0.001
