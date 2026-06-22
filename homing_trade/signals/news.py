"""Crypto news headlines — free RSS into the LLM context (Phase 6 #5, optional).

Pulls recent headlines from free, keyless crypto RSS feeds (CryptoPanic's free tier retired
2026-04-01, so we deliberately do NOT depend on it — plain RSS is the default). Headlines are
parsed with the stdlib (no feedparser dep), deduped, bounded, cached, and injected as macro/event
CONTEXT — what's in the news, not a trade trigger. Every feed is best-effort: one failing feed
doesn't drop the others, and a total failure degrades to None without crashing the loop.
"""
import time
import xml.etree.ElementTree as ET
from urllib.parse import urlparse

import requests

SOURCE = "news"
KEY = "headlines"
DEFAULT_MAX_AGE_SEC = 1800          # news moves slower than price; 30-min freshness is fine
# Free, keyless RSS feeds. Add/remove without touching logic.
RSS_FEEDS = (
    "https://www.coindesk.com/arc/outboundfeeds/rss/",
    "https://cointelegraph.com/rss",
)


def _http_text_fetcher(url):
    resp = requests.get(url, timeout=10, headers={"User-Agent": "homing-trade/1.0 (+rss)"})
    resp.raise_for_status()
    return resp.text


def _domain(url):
    try:
        return (urlparse(url).netloc or "").replace("www.", "") or "rss"
    except Exception:
        return "rss"


def parse_rss(xml_text, limit=5):
    """Parse up to `limit` items from RSS 2.0 XML -> [{title, published, link}]. Returns [] on a
    parse error or no items (never raises)."""
    # Deliberate stdlib choice: ElementTree resolves NO external entities and fetches no external
    # DTDs (no XXE/SSRF). Internal-entity-expansion DoS is theoretically possible but accepted here
    # — feeds are HTTPS from major publishers and this is a degrade-safe, non-load-bearing context
    # read. Don't reflexively swap to a parser/dep without that threat-model reason.
    try:
        root = ET.fromstring(xml_text)
    except Exception:
        return []
    items = []
    for item in root.iter("item"):              # RSS <item>s carry no namespace
        title = (item.findtext("title") or "").strip()
        if not title:
            continue
        items.append({"title": title,
                      "published": (item.findtext("pubDate") or "").strip() or None,
                      "link": (item.findtext("link") or "").strip() or None})
        if len(items) >= limit:
            break
    return items


def fetch_news(feeds=RSS_FEEDS, *, fetcher=None, limit_per_feed=5, limit=12):
    """Aggregate recent headlines across `feeds` -> [{title, published, link, source}] or None if
    NO feed yielded anything. Each feed is best-effort (a failing feed is skipped); titles are
    deduped (case-insensitive) and the result is bounded to `limit`."""
    fetcher = fetcher or _http_text_fetcher
    out, seen = [], set()
    for url in feeds:
        try:
            xml_text = fetcher(url)
        except Exception:
            continue
        for h in parse_rss(xml_text, limit_per_feed):
            k = h["title"].lower()
            if k in seen:
                continue
            seen.add(k)
            h["source"] = _domain(url)
            out.append(h)
    return out[:limit] or None


def get_news(repo, *, fetcher=None, now=None, max_age_sec=DEFAULT_MAX_AGE_SEC):
    """Cache-aware recent headlines. Returns the list or None. Serves a cached value within
    `max_age_sec`; else refetches + caches; on failure returns the stale cached value (or None).
    Reads/writes signal_cache(source='news', key='headlines'). Epoch MS."""
    now = int(now if now is not None else time.time() * 1000)
    cached = repo.get_signal(SOURCE, KEY) if hasattr(repo, "get_signal") else None
    if cached and (now - cached["fetched_at"]) < max_age_sec * 1000:
        return cached["value"]
    fresh = fetch_news(fetcher=fetcher)
    if fresh is None:
        return cached["value"] if cached else None
    if hasattr(repo, "upsert_signal"):
        repo.upsert_signal(SOURCE, KEY, now, fresh, now)
    return fresh
