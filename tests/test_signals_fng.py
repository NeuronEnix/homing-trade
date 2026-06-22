"""Phase 6 #1: Fear & Greed ingestion — fetch/parse, the cache-aware get_fng (fresh/stale/
fallback), the SQLite signal_cache roundtrip, and injection into the AI context. All offline
(injected fetcher), deterministic (injected `now`)."""
from homing_trade.db import Database, AUDIT_TRUTH_TABLES
from homing_trade.repository import Repository
from homing_trade.signals import fng
from homing_trade.skills.llm_trader import LlmTrader
from homing_trade.models import Candle


# Alternative.me /fng/ returns string fields under data[0].
def _payload(value="40", cls="Fear", ts="1551157200"):
    return {"name": "Fear and Greed Index",
            "data": [{"value": value, "value_classification": cls, "timestamp": ts}]}


def candles(n=40, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


def test_fetch_fng_parses_payload():
    out = fng.fetch_fng(fetcher=lambda url, params: _payload("72", "Greed", "1551157200"))
    assert out == {"value": 72, "classification": "Greed", "ts": 1551157200 * 1000}


def test_fetch_fng_degrades_to_none_on_error():
    def boom(url, params): raise RuntimeError("network down")
    assert fng.fetch_fng(fetcher=boom) is None


def test_fetch_fng_degrades_on_bad_shape():
    assert fng.fetch_fng(fetcher=lambda u, p: {"data": []}) is None      # empty
    assert fng.fetch_fng(fetcher=lambda u, p: {"oops": 1}) is None        # missing key


def test_get_fng_fetches_and_caches(tmp_path):
    repo = Repository.open(str(tmp_path / "s.db"))
    calls = []
    def fetch(u, p): calls.append(1); return _payload("55", "Greed")
    out = fng.get_fng(repo, fetcher=fetch, now=1_000_000_000_000)
    assert out["value"] == 55 and len(calls) == 1
    cached = repo.get_signal("fng", "latest")
    assert cached["value"]["value"] == 55 and cached["fetched_at"] == 1_000_000_000_000
    repo.close()


def test_get_fng_serves_fresh_cache_without_refetch(tmp_path):
    repo = Repository.open(str(tmp_path / "s.db"))
    now = 1_000_000_000_000
    fng.get_fng(repo, fetcher=lambda u, p: _payload("30", "Fear"), now=now)
    def boom(u, p): raise AssertionError("must not refetch while cache is fresh")
    out = fng.get_fng(repo, fetcher=boom, now=now + 60_000)               # 1 min later, still fresh
    assert out["value"] == 30
    repo.close()


def test_get_fng_refetches_when_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "s.db"))
    now = 1_000_000_000_000
    fng.get_fng(repo, fetcher=lambda u, p: _payload("30", "Fear"), now=now)
    out = fng.get_fng(repo, fetcher=lambda u, p: _payload("80", "Extreme Greed"),
                      now=now + 3_600_001, max_age_sec=3600)              # just past max age
    assert out["value"] == 80 and out["classification"] == "Extreme Greed"
    repo.close()


def test_get_fng_falls_back_to_stale_on_fetch_failure(tmp_path):
    repo = Repository.open(str(tmp_path / "s.db"))
    now = 1_000_000_000_000
    fng.get_fng(repo, fetcher=lambda u, p: _payload("42", "Fear"), now=now)
    def boom(u, p): raise RuntimeError("down")
    out = fng.get_fng(repo, fetcher=boom, now=now + 10_000_000, max_age_sec=3600)  # stale + fetch fails
    assert out["value"] == 42                                            # stale cache, not a crash
    repo.close()


def test_get_fng_returns_none_when_no_cache_and_fetch_fails(tmp_path):
    repo = Repository.open(str(tmp_path / "s.db"))
    out = fng.get_fng(repo, fetcher=lambda u, p: (_ for _ in ()).throw(RuntimeError("x")), now=1)
    assert out is None
    repo.close()


def test_signal_cache_roundtrip_and_upsert(tmp_path):
    db = Database(str(tmp_path / "s.db"))
    db.upsert_signal("fng", "latest", 100, {"value": 10}, 500)
    assert db.get_signal("fng", "latest")["value"] == {"value": 10}
    db.upsert_signal("fng", "latest", 200, {"value": 90}, 600)           # same PK -> overwrite
    row = db.get_signal("fng", "latest")
    assert row["value"] == {"value": 90} and row["ts"] == 200 and row["fetched_at"] == 600
    assert db.get_signal("fng", "missing") is None
    db.close()


def test_signal_cache_is_audit_truth():
    assert "signal_cache" in AUDIT_TRUTH_TABLES


def test_fng_injected_into_ai_context():
    reading = {"value": 18, "classification": "Extreme Fear", "ts": 1000}
    t = LlmTrader(provider=lambda *a, **k: candles(), fng_provider=lambda: reading)
    ctx = t._build_context(candles(), None)
    assert ctx["fear_greed"] == reading


def test_fng_absent_when_no_provider():
    t = LlmTrader(provider=lambda *a, **k: candles())
    assert "fear_greed" not in t._build_context(candles(), None)


def test_fng_provider_failure_omits_field_not_crash():
    def boom(): raise RuntimeError("provider blew up")
    t = LlmTrader(provider=lambda *a, **k: candles(), fng_provider=boom)
    assert "fear_greed" not in t._build_context(candles(), None)         # degraded, no crash
