"""Phase 6 #6: the single enforced cache-aware contract (signals/cache.py) + cache observability.

cached_signal() is the one audited path every feed reads through: cache-with-fetched_at, serve-fresh
(rate-limit), degrade-to-stale-or-None on failure, never crash. signal_status() inspects the cache.
"""
from homing_trade.repository import Repository
from homing_trade.signals.cache import cached_signal, signal_status


def test_fetches_caches_and_records_fetched_at(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    out = cached_signal(repo, "s", "k", lambda: {"v": 1}, now=1000, max_age_sec=60)
    assert out == {"v": 1}
    row = repo.get_signal("s", "k")
    assert row["value"] == {"v": 1} and row["fetched_at"] == 1000 and row["ts"] == 1000  # ts_fn None -> now
    repo.close()


def test_ts_fn_sets_observation_ts(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    cached_signal(repo, "s", "k", lambda: {"ts": 777}, ts_fn=lambda v: v["ts"], now=1000, max_age_sec=60)
    assert repo.get_signal("s", "k")["ts"] == 777                # upstream observation time, not now
    repo.close()


def test_serves_fresh_cache_without_refetch(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    cached_signal(repo, "s", "k", lambda: {"v": 1}, now=1000, max_age_sec=60)
    def boom(): raise AssertionError("must not refetch while fresh")
    assert cached_signal(repo, "s", "k", boom, now=1000 + 59_000, max_age_sec=60) == {"v": 1}
    repo.close()


def test_refetches_when_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    cached_signal(repo, "s", "k", lambda: {"v": 1}, now=1000, max_age_sec=60)
    out = cached_signal(repo, "s", "k", lambda: {"v": 2}, now=1000 + 60_001, max_age_sec=60)
    assert out == {"v": 2}
    repo.close()


def test_none_fetch_falls_back_to_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    cached_signal(repo, "s", "k", lambda: {"v": 1}, now=1000, max_age_sec=60)
    out = cached_signal(repo, "s", "k", lambda: None, now=10_000_000, max_age_sec=60)
    assert out == {"v": 1}                                       # stale cache preserved
    repo.close()


def test_raising_fetch_degrades_not_crashes(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    def boom(): raise RuntimeError("provider blew up")
    assert cached_signal(repo, "s", "k", boom, now=1000, max_age_sec=60) is None   # no cache -> None
    cached_signal(repo, "s", "k", lambda: {"v": 1}, now=2000, max_age_sec=60)      # seed
    out = cached_signal(repo, "s", "k", boom, now=10_000_000, max_age_sec=60)
    assert out == {"v": 1}                                       # stale on exception, no crash
    repo.close()


def test_signal_status_reports_freshness(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    cached_signal(repo, "fng", "latest", lambda: {"v": 1}, now=1_000_000, max_age_sec=60)
    cached_signal(repo, "news", "headlines", lambda: [{"t": "x"}], now=1_500_000, max_age_sec=60)
    st = signal_status(repo, now=1_500_000 + 2_000_000, max_age_sec=3600)   # +2000s
    by = {r["source"]: r for r in st}
    assert by["news"]["fetched_at"] == 1_500_000
    assert by["fng"]["age_sec"] == round((1_500_000 + 2_000_000 - 1_000_000) / 1000.0, 1)
    assert by["fng"]["stale"] is False and by["news"]["stale"] is False    # 2000s < 3600s
    # newest fetch first
    assert [r["source"] for r in st] == ["news", "fng"]
    repo.close()


def test_signal_status_flags_stale(tmp_path):
    repo = Repository.open(str(tmp_path / "c.db"))
    cached_signal(repo, "fng", "latest", lambda: {"v": 1}, now=0, max_age_sec=60)
    st = signal_status(repo, now=4_000_000, max_age_sec=3600)               # 4000s old > 3600
    assert st[0]["stale"] is True
    repo.close()


def test_signal_status_empty_without_cache():
    class _NoCache:
        pass
    assert signal_status(_NoCache()) == []
