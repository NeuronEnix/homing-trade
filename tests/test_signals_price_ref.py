"""Phase 6 #4: CoinGecko reference-price ingestion (venue sanity-check).

Parse + degrade, the optional Demo key (sent as a query param), keyless operation, the cache-aware
get_price_ref (fresh/stale/fallback), resolve_key from env, and context injection. Offline
(injected fetcher), deterministic (injected `now`).
"""
from homing_trade.repository import Repository
from homing_trade.signals import price_ref
from homing_trade.skills.llm_trader import LlmTrader
from homing_trade.models import Candle


def candles(n=40, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


def _payload(btc=64000.0, eth=3400.0):
    return {"bitcoin": {"usd": btc, "usd_24h_change": 1.5, "usd_market_cap": 1.2e12},
            "ethereum": {"usd": eth, "usd_24h_change": -0.7, "usd_market_cap": 4.0e11}}


def test_fetch_price_ref_parses_assets():
    out = price_ref.fetch_price_ref(fetcher=lambda u, p: _payload(64000.0, 3400.0))
    assert out["bitcoin"] == {"usd": 64000.0, "change_24h": 1.5, "market_cap": 1.2e12}
    assert out["ethereum"]["usd"] == 3400.0 and out["ethereum"]["change_24h"] == -0.7


def test_fetch_price_ref_sends_demo_key_when_present():
    seen = {}
    def fetch(url, params): seen.update(params); return _payload()
    price_ref.fetch_price_ref(fetcher=fetch, api_key="CG-secret")
    assert seen["x_cg_demo_api_key"] == "CG-secret"
    assert seen["ids"] == "bitcoin,ethereum" and seen["vs_currencies"] == "usd"


def test_fetch_price_ref_works_keyless():
    seen = {}
    def fetch(url, params): seen.update(params); return _payload()
    assert price_ref.fetch_price_ref(fetcher=fetch) is not None
    assert "x_cg_demo_api_key" not in seen                    # no key param when none given


def test_fetch_price_ref_degrades_on_error_and_bad_shape():
    assert price_ref.fetch_price_ref(fetcher=lambda u, p: (_ for _ in ()).throw(RuntimeError())) is None
    assert price_ref.fetch_price_ref(fetcher=lambda u, p: {"bitcoin": {}}) is None   # no usd
    assert price_ref.fetch_price_ref(fetcher=lambda u, p: {}) is None                # empty


def test_resolve_key_from_env(monkeypatch):
    monkeypatch.delenv("COINGECKO_DEMO_KEY", raising=False)
    assert price_ref.resolve_key("COINGECKO_DEMO_KEY") is None
    monkeypatch.setenv("COINGECKO_DEMO_KEY", "CG-abc")
    assert price_ref.resolve_key("COINGECKO_DEMO_KEY") == "CG-abc"
    assert price_ref.resolve_key("") is None


def test_get_price_ref_fetches_caches_then_serves_fresh(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    now = 1_000_000_000_000
    out = price_ref.get_price_ref(repo, fetcher=lambda u, p: _payload(64000.0), now=now)
    assert out["bitcoin"]["usd"] == 64000.0
    assert repo.get_signal("price_ref", "usd")["value"]["bitcoin"]["usd"] == 64000.0
    def boom(u, p): raise AssertionError("must not refetch while fresh")
    again = price_ref.get_price_ref(repo, fetcher=boom, now=now + 60_000)
    assert again["bitcoin"]["usd"] == 64000.0
    repo.close()


def test_get_price_ref_refetches_when_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    now = 1_000_000_000_000
    price_ref.get_price_ref(repo, fetcher=lambda u, p: _payload(64000.0), now=now)
    out = price_ref.get_price_ref(repo, fetcher=lambda u, p: _payload(70000.0),
                                  now=now + 600_001, max_age_sec=600)
    assert out["bitcoin"]["usd"] == 70000.0
    repo.close()


def test_get_price_ref_falls_back_to_stale_on_failure(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    now = 1_000_000_000_000
    price_ref.get_price_ref(repo, fetcher=lambda u, p: _payload(64000.0), now=now)
    def boom(u, p): raise RuntimeError("down")
    out = price_ref.get_price_ref(repo, fetcher=boom, now=now + 9_000_000, max_age_sec=600)
    assert out["bitcoin"]["usd"] == 64000.0                   # stale cache, no crash
    repo.close()


def test_get_price_ref_none_when_no_cache_and_fetch_fails(tmp_path):
    repo = Repository.open(str(tmp_path / "p.db"))
    assert price_ref.get_price_ref(repo, fetcher=lambda u, p: (_ for _ in ()).throw(RuntimeError()),
                                   now=1) is None
    repo.close()


def test_price_ref_injected_into_ai_context():
    reading = {"bitcoin": {"usd": 64000.0, "change_24h": 1.5}}
    t = LlmTrader(provider=lambda *a, **k: candles())
    t.add_context_provider("price_ref", lambda: reading)
    assert t._build_context(candles(), None)["price_ref"] == reading
