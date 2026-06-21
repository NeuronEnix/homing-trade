# tests/test_engine.py
from homing_trade.engine import build_skills, process_tick, run
from homing_trade.db import Database
from homing_trade.broker import Broker
from homing_trade.config import Config
from homing_trade.models import Candle


def rising_then_drop():
    # enough candles to warm up skills (>61 for grid lookback default 60)
    closes = [100 + i for i in range(70)] + [50.0]
    return [Candle(open=c, high=c + 1, low=c - 1, close=c, volume=1, time=1000 + i * 60000)
            for i, c in enumerate(closes)]


def test_build_skills_returns_three():
    skills = build_skills(["ma_trend", "rsi_revert", "grid"])
    names = sorted(s.name for s in skills)
    assert names == ["grid", "ma_trend", "rsi_revert"]


def test_process_tick_persists_decisions(tmp_path):
    cfg = Config(db_path=str(tmp_path / "e.db"),
                 enabled_skills=["ma_trend", "rsi_revert", "grid"])
    db = Database(cfg.db_path)
    broker = Broker(cfg.fee, cfg.slippage)
    skills = build_skills(cfg.enabled_skills)
    for s in skills:
        db.ensure_strategy(s.name, cfg.starting_balance)
    candles = rising_then_drop()
    process_tick(db, broker, skills, candles, cfg)
    # a decision row must exist for every skill
    rows = db.conn.execute("SELECT DISTINCT strategy FROM decision_log").fetchall()
    assert len(rows) == 3
    db.close()


def test_run_processes_one_tick_then_stops(tmp_path):
    cfg = Config(db_path=str(tmp_path / "r.db"))
    candles_raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
                    "volume": c.volume, "time": c.time} for c in rising_then_drop()]

    def fake_fetcher(url, params):
        return candles_raw

    run(cfg, fetcher=fake_fetcher, max_ticks=1, sleeper=lambda s: None)
    db = Database(cfg.db_path)
    assert db.get_state("last_candle_time") == str(candles_raw[-1]["time"])
    db.close()


def test_run_skips_already_processed_candle(tmp_path):
    cfg = Config(db_path=str(tmp_path / "idem.db"),
                 enabled_skills=["ma_trend", "rsi_revert", "grid"])
    candles_raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
                    "volume": c.volume, "time": c.time} for c in rising_then_drop()]

    def fake_fetcher(url, params):
        return candles_raw

    # Three ticks, but the fetcher always returns the SAME newest candle:
    # only the first tick should be processed (restart-safety cursor).
    run(cfg, fetcher=fake_fetcher, max_ticks=3, sleeper=lambda s: None)
    db = Database(cfg.db_path)
    count = db.conn.execute("SELECT COUNT(*) AS c FROM decision_log").fetchone()["c"]
    # 3 skills x exactly one processed tick = 3 rows (NOT 9)
    assert count == 3
    db.close()


def test_run_persists_live_candles(tmp_path):
    cfg = Config(db_path=str(tmp_path / "live.db"))
    candles_raw = [{"open": c.open, "high": c.high, "low": c.low, "close": c.close,
                    "volume": c.volume, "time": c.time} for c in rising_then_drop()]

    def fake_fetcher(url, params):
        return candles_raw

    run(cfg, fetcher=fake_fetcher, max_ticks=1, sleeper=lambda s: None)
    db = Database(cfg.db_path)
    n = db.count_candles(cfg.pair_candles, cfg.interval, source="live")
    assert n == len(candles_raw)
    db.close()
