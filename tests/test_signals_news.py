"""Phase 6 #5: crypto news (free RSS) ingestion.

RSS parse (stdlib), multi-feed aggregation + dedup + best-effort (one failing feed skipped),
the cache-aware get_news (fresh/stale/fallback), and context injection. Offline (injected text
fetcher), deterministic (injected `now`).
"""
from homing_trade.repository import Repository
from homing_trade.signals import news
from homing_trade.skills.llm_trader import LlmTrader
from homing_trade.models import Candle


def candles(n=40, price=64000.0):
    return [Candle(open=price, high=price + 5, low=price - 5, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


def _rss(*titles):
    items = "".join(
        f"<item><title>{t}</title><pubDate>Mon, 22 Jun 2026 10:00:00 GMT</pubDate>"
        f"<link>https://x/{i}</link></item>"
        for i, t in enumerate(titles))
    return f"<?xml version='1.0'?><rss version='2.0'><channel>{items}</channel></rss>"


def test_parse_rss_extracts_items():
    out = news.parse_rss(_rss("BTC ETF approved", "ETH upgrade ships"), limit=5)
    assert [h["title"] for h in out] == ["BTC ETF approved", "ETH upgrade ships"]
    assert out[0]["link"] == "https://x/0" and out[0]["published"]


def test_parse_rss_respects_limit_and_skips_blank_titles():
    xml = "<rss><channel><item><title></title></item>" \
          "<item><title>real</title></item></channel></rss>"
    assert [h["title"] for h in news.parse_rss(xml, limit=5)] == ["real"]   # blank dropped
    assert len(news.parse_rss(_rss("a", "b", "c"), limit=2)) == 2


def test_parse_rss_degrades_on_bad_xml():
    assert news.parse_rss("not xml <<<", limit=5) == []


def test_fetch_news_aggregates_dedups_across_feeds():
    feeds = ("https://www.coindesk.com/x", "https://cointelegraph.com/y")
    def fetch(url):
        return _rss("Shared headline", "CoinDesk only") if "coindesk" in url \
            else _rss("Shared headline", "CT only")     # "Shared headline" dup'd across feeds
    out = news.fetch_news(feeds, fetcher=fetch, limit_per_feed=5, limit=12)
    titles = [h["title"] for h in out]
    assert titles.count("Shared headline") == 1                # case-insensitive dedup
    assert "CoinDesk only" in titles and "CT only" in titles
    assert {h["source"] for h in out} == {"coindesk.com", "cointelegraph.com"}


def test_fetch_news_one_feed_failing_is_best_effort():
    feeds = ("https://a/x", "https://b/y")
    def fetch(url):
        if "a/" in url:
            raise RuntimeError("feed a down")
        return _rss("from b")
    out = news.fetch_news(feeds, fetcher=fetch)
    assert [h["title"] for h in out] == ["from b"]             # a skipped, b survives


def test_fetch_news_none_when_all_fail():
    def boom(url): raise RuntimeError("down")
    assert news.fetch_news(("https://a/x",), fetcher=boom) is None
    assert news.fetch_news(("https://a/x",), fetcher=lambda u: _rss()) is None   # empty channel


def test_fetch_news_bounded_to_limit():
    out = news.fetch_news(("https://a/x",), fetcher=lambda u: _rss(*[f"h{i}" for i in range(20)]),
                          limit_per_feed=20, limit=12)
    assert len(out) == 12


def test_get_news_fetches_caches_then_serves_fresh(tmp_path):
    repo = Repository.open(str(tmp_path / "n.db"))
    now = 1_000_000_000_000
    out = news.get_news(repo, fetcher=lambda u: _rss("headline one"), now=now)
    assert out[0]["title"] == "headline one"
    assert repo.get_signal("news", "headlines")["value"][0]["title"] == "headline one"
    def boom(u): raise AssertionError("must not refetch while fresh")
    again = news.get_news(repo, fetcher=boom, now=now + 60_000)
    assert again[0]["title"] == "headline one"
    repo.close()


def test_get_news_refetches_when_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "n.db"))
    now = 1_000_000_000_000
    news.get_news(repo, fetcher=lambda u: _rss("old"), now=now)
    out = news.get_news(repo, fetcher=lambda u: _rss("fresh"), now=now + 1_800_001, max_age_sec=1800)
    assert out[0]["title"] == "fresh"
    repo.close()


def test_get_news_falls_back_to_stale_on_failure(tmp_path):
    repo = Repository.open(str(tmp_path / "n.db"))
    now = 1_000_000_000_000
    news.get_news(repo, fetcher=lambda u: _rss("cached headline"), now=now)
    def boom(u): raise RuntimeError("down")
    out = news.get_news(repo, fetcher=boom, now=now + 9_000_000, max_age_sec=1800)
    assert out[0]["title"] == "cached headline"               # stale cache, no crash
    repo.close()


def test_get_news_none_when_no_cache_and_fetch_fails(tmp_path):
    repo = Repository.open(str(tmp_path / "n.db"))
    def boom(u): raise RuntimeError("down")
    assert news.get_news(repo, fetcher=boom, now=1) is None
    repo.close()


def test_news_injected_into_ai_context():
    headlines = [{"title": "BTC ETF approved", "source": "coindesk.com"}]
    t = LlmTrader(provider=lambda *a, **k: candles())
    t.add_context_provider("news", lambda: headlines)
    assert t._build_context(candles(), None)["news"] == headlines
