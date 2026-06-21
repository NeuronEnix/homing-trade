from homing_trade.risk import DailyRiskGuard
from homing_trade.config import Config

DAY = 86_400_000


def test_no_limits_always_allows():
    g = DailyRiskGuard()  # all off
    ok, _ = g.can_open(1_000_000, 0)
    assert ok is True


def test_master_switch_blocks():
    g = DailyRiskGuard(enabled=False)
    ok, reason = g.can_open(10, 0)
    assert ok is False and "disabled" in reason


def test_daily_trade_cap_blocks_over_limit():
    g = DailyRiskGuard(max_trade_amount_per_day=1000.0)
    assert g.can_open(600, 0)[0] is True
    g.record_open(600, 0)
    # second open of 600 would exceed the 1000/day cap
    ok, reason = g.can_open(600, 0)
    assert ok is False and "cap" in reason
    # but a smaller one still fits
    assert g.can_open(300, 0)[0] is True


def test_kill_switch_halts_after_loss():
    g = DailyRiskGuard(max_daily_loss=500.0)
    assert g.can_open(100, 0)[0] is True
    g.record_close(-200.0, 0)
    assert g.can_open(100, 0)[0] is True        # not breached yet
    g.record_close(-350.0, 0)                    # total loss 550 >= 500 -> halt
    ok, reason = g.can_open(100, 0)
    assert ok is False and "kill switch" in reason
    assert g.halted_reason is not None


def test_new_day_resets_counters_and_halt():
    g = DailyRiskGuard(max_daily_loss=500.0, max_trade_amount_per_day=1000.0)
    g.record_open(900, 0)
    g.record_close(-600.0, 0)                    # trips kill switch on day 0
    assert g.can_open(50, 0)[0] is False
    # next day -> fresh
    assert g.can_open(50, DAY)[0] is True
    assert g.traded_today == 0.0 and g.realized_today == 0.0


def test_from_config():
    cfg = Config(max_trade_amount_per_day=2000.0, max_daily_loss=300.0, trading_enabled=False)
    g = DailyRiskGuard.from_config(cfg)
    assert g.max_trade_amount_per_day == 2000.0
    assert g.max_daily_loss == 300.0
    assert g.enabled is False
