from homing_trade.position_manager import PositionManager
from homing_trade.broker import Broker
from homing_trade.config import Config
from homing_trade.ledger import MemoryLedger
from homing_trade.models import Candle


class _Skill:
    name = "ma_trend"


def _candle(close=100.0, t=1000):
    return Candle(open=close, high=close + 1, low=close - 1, close=close, volume=1, time=t)


def test_open_records_position_and_deducts_fee():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    pm = PositionManager(led, Broker(cfg.fee, cfg.slippage), cfg)
    opened, reason = pm.open(_Skill(), "LONG", _candle(), now_ms=1000)
    assert opened is True and reason is None
    pos = led.get_open_position("ma_trend")
    assert pos is not None and pos.side == "LONG"
    assert led.get_balance("ma_trend") < 5000.0          # entry fee deducted
    assert any(t["action"] == "OPEN" for t in led.trades)


def test_close_books_pnl_and_clears_position():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    pm = PositionManager(led, Broker(cfg.fee, cfg.slippage), cfg)
    pm.open(_Skill(), "LONG", _candle(close=100.0), now_ms=1000)
    pos = led.get_open_position("ma_trend")
    bal = pm.close(_Skill(), pos, exit_price=110.0, candle=_candle(close=110.0), now_ms=2000)
    assert led.get_open_position("ma_trend") is None
    assert bal == led.get_balance("ma_trend")
    assert any(t["action"] == "CLOSE" for t in led.trades)


class _BlockGuard:
    def can_open(self, notional, ts): return (False, "blocked")
    def record_open(self, *a): pass
    def record_close(self, *a): pass


def test_guard_blocks_open():
    cfg = Config()
    led = MemoryLedger("ma_trend", 5000.0)
    pm = PositionManager(led, Broker(cfg.fee, cfg.slippage), cfg, guard=_BlockGuard())
    opened, reason = pm.open(_Skill(), "LONG", _candle(), now_ms=1000)
    assert opened is False and reason == "blocked"
    assert led.get_open_position("ma_trend") is None       # nothing opened when blocked
    assert led.risk_events and led.risk_events[-1]["kind"] == "veto"   # veto recorded


class _StopBroker:
    """Broker stub that reports a stop-out, to test manage_risk in isolation."""
    def hit_liquidation(self, pos, candle): return False
    def hit_stop(self, pos, candle): return True
    def fill_price(self, price, side, is_entry): return price
    def realized_pnl(self, pos, exit_fill): return 0.0
    def entry_fee(self, size, price): return 0.0


def test_manage_risk_closes_on_stop():
    led = MemoryLedger("ma_trend", 5000.0)
    # seed an open position directly
    from homing_trade.models import Position
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=1.0,
                   leverage=10.0, margin=10.0, stop_price=98.0, opened_at=1000)
    led.open_position(pos)
    pm = PositionManager(led, _StopBroker())
    survivor = pm.manage_risk(_Skill(), led.get_open_position("ma_trend"), _candle(t=2000), now_ms=2000)
    assert survivor is None
    assert led.get_open_position("ma_trend") is None


def test_manage_risk_keeps_position_when_safe():
    class _SafeBroker(_StopBroker):
        def hit_stop(self, pos, candle): return False
    led = MemoryLedger("ma_trend", 5000.0)
    from homing_trade.models import Position
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=1.0,
                   leverage=10.0, margin=10.0, stop_price=98.0, opened_at=1000)
    led.open_position(pos)
    pm = PositionManager(led, _SafeBroker())
    survivor = pm.manage_risk(_Skill(), led.get_open_position("ma_trend"), _candle(t=2000), now_ms=2000)
    assert survivor is not None and led.get_open_position("ma_trend") is not None
