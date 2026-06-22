from homing_trade.engine import process_tick
from homing_trade.broker import Broker
from homing_trade.ledger import MemoryLedger
from homing_trade.risk import DailyRiskGuard
from homing_trade.config import Config, effective_leverage
from homing_trade.skills.base import Strategy
from homing_trade.models import Candle, Signal


class AlwaysLong(Strategy):
    name = "ma_trend"
    def on_candle(self, candles, position):
        return Signal("LONG") if position is None else Signal("HOLD")


def candles():
    return [Candle(open=100, high=101, low=99, close=100, volume=1, time=1000 + i * 60000)
            for i in range(30)]


def test_disabled_guard_blocks_open():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(),
                 cfg, DailyRiskGuard(enabled=False))
    assert led.get_open_position("ma_trend") is None  # master switch blocked it


def test_disabled_strategy_takes_no_new_position():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg,
                 is_disabled=lambda n: True)
    assert led.get_open_position("ma_trend") is None        # disabled -> no consult, no open
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg,
                 is_disabled=lambda n: False)
    assert led.get_open_position("ma_trend") is not None    # enabled -> opens normally


def test_disabled_strategy_still_risk_manages_open_position():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg)  # opens LONG
    pos = led.get_open_position("ma_trend")
    assert pos is not None
    # disable it, then feed a candle whose low dips just below the stop (but stays above the
    # liquidation price) — the STOP must still fire, so a parked position is never left
    # unprotected. low = stop - 1 keeps us in the stop band, not liquidation.
    crash = candles()[:-1] + [Candle(open=100, high=100, low=pos.stop_price - 1,
                                     close=pos.stop_price - 1, volume=1, time=99_000_000)]
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], crash, cfg,
                 is_disabled=lambda n: True)
    assert led.get_open_position("ma_trend") is None         # stop managed even while disabled
    assert led.trades[-1]["exit_reason"] == "stop"           # specifically the stop, not liquidation


def test_tiny_daily_cap_blocks_open():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(),
                 cfg, DailyRiskGuard(max_trade_amount_per_day=0.01))
    assert led.get_open_position("ma_trend") is None  # notional >> cap -> blocked


def test_large_cap_allows_open_at_clamped_leverage():
    cfg = Config()  # leverage 15, min 1, max 15
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(),
                 cfg, DailyRiskGuard(max_trade_amount_per_day=1e12))
    pos = led.get_open_position("ma_trend")
    assert pos is not None
    assert pos.leverage == effective_leverage(cfg) == 15.0


def test_no_guard_still_opens():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    process_tick(led, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg)  # guard=None
    assert led.get_open_position("ma_trend") is not None


def test_decision_log_records_veto_provenance_and_risk_event(tmp_path):
    from homing_trade.repository import Repository
    cfg = Config(db_path=str(tmp_path / "prov.db"))
    repo = Repository.open(cfg.db_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    # tiny daily cap -> the LONG is vetoed by the guard
    process_tick(repo, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg,
                 DailyRiskGuard(max_trade_amount_per_day=0.01))
    assert repo.get_open_position("ma_trend") is None
    row = repo.db.conn.execute(
        "SELECT intended_action, taken_action, rejection_rationale, decision_id "
        "FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["intended_action"] == "LONG"
    assert row["taken_action"] == "BLOCKED"
    assert row["rejection_rationale"]            # explains the veto
    assert row["decision_id"]                    # links the decision
    ev = repo.recent_risk_events(5)
    assert ev and ev[0]["kind"] == "veto" and ev[0]["strategy"] == "ma_trend"


def test_open_trade_links_decision_id_and_regime(tmp_path):
    from homing_trade.repository import Repository
    cfg = Config(db_path=str(tmp_path / "lnk.db"))
    repo = Repository.open(cfg.db_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    process_tick(repo, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg)  # opens a LONG
    trade = repo.db.conn.execute(
        "SELECT decision_id, regime_at_entry FROM trades WHERE action='OPEN' ORDER BY id DESC LIMIT 1").fetchone()
    dec = repo.db.conn.execute(
        "SELECT decision_id, regime FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    assert trade["decision_id"] == dec["decision_id"]   # OPEN trade traces to its decision
    assert trade["regime_at_entry"] == dec["regime"]    # ...with the same regime


def test_process_tick_records_regime_and_tags_decision(tmp_path):
    from homing_trade.repository import Repository
    cfg = Config(db_path=str(tmp_path / "rg.db"))
    repo = Repository.open(cfg.db_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    process_tick(repo, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg)
    reg = repo.latest_regime(cfg.pair_candles, cfg.interval)
    assert reg is not None                                   # the tick's regime was recorded
    row = repo.db.conn.execute(
        "SELECT regime FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["regime"] == reg["regime"]                    # decision tagged with that regime


def test_decision_log_records_taken_action_on_open(tmp_path):
    from homing_trade.repository import Repository
    cfg = Config(db_path=str(tmp_path / "prov2.db"))
    repo = Repository.open(cfg.db_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    process_tick(repo, Broker(cfg.fee, cfg.slippage), [AlwaysLong()], candles(), cfg)  # no guard
    row = repo.db.conn.execute(
        "SELECT intended_action, taken_action FROM decision_log ORDER BY id DESC LIMIT 1").fetchone()
    assert row["intended_action"] == "LONG" and row["taken_action"] == "LONG"   # opened
    assert repo.get_open_position("ma_trend") is not None
