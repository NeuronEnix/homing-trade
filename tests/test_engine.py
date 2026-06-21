# tests/test_engine.py
import json
from pathlib import Path
from algotrading.engine import build_skills, process_tick, run
from algotrading.db import Database
from algotrading.broker import Broker
from algotrading.config import Config
from algotrading.models import Candle


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
    cfg = Config(db_path=str(tmp_path / "e.db"))
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
