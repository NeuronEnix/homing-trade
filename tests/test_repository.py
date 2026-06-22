from homing_trade.repository import Repository
from homing_trade.ledger_base import Ledger
from homing_trade.models import Position


def make(tmp_path):
    return Repository.open(str(tmp_path / "repo.db"))


def test_is_ledger(tmp_path):
    assert isinstance(make(tmp_path), Ledger)


def test_delegates_strategy_and_balance(tmp_path):
    repo = make(tmp_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    assert repo.get_balance("ma_trend") == 5000.0
    repo.set_balance("ma_trend", 4800.0)
    assert repo.get_balance("ma_trend") == 4800.0


def test_delegates_position_lifecycle(tmp_path):
    repo = make(tmp_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    pos = Position(strategy="ma_trend", side="LONG", entry_price=100.0, size=0.5,
                   leverage=3.0, margin=50.0, stop_price=98.0, opened_at=1000)
    pid = repo.open_position(pos)
    assert repo.get_open_position("ma_trend").id == pid
    repo.close_position(pid)
    assert repo.get_open_position("ma_trend") is None


def test_closed_pnls_and_equity_series(tmp_path):
    repo = make(tmp_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    repo.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    repo.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 2000)
    repo.record_trade("ma_trend", 2, "LONG", "CLOSE", 90.0, 1.0, 0.1, -10.1, 3000)
    repo.record_equity("ma_trend", 5000.0, 1000)
    repo.record_equity("ma_trend", 5010.0, 2000)
    assert repo.closed_pnls("ma_trend") == [9.9, -10.1]   # CLOSE only, oldest-first
    assert repo.equity_series("ma_trend") == [5000.0, 5010.0]


def test_live_loop_delegations(tmp_path):
    from homing_trade.models import Candle
    repo = make(tmp_path)
    repo.ensure_strategy("ma_trend", 5000.0)
    assert repo.max_trade_id() == 0
    repo.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    assert repo.max_trade_id() == 1
    repo.set_state("last_candle_time", "123")
    assert repo.get_state("last_candle_time") == "123"
    n = repo.save_candles("B-BTC_USDT", "15m",
                          [Candle(open=1, high=2, low=0.5, close=1.5, volume=10, time=60000)], "live")
    assert n == 1
    assert [t["id"] for t in repo.trades_after(0)] == [1]


def test_candle_read_delegations(tmp_path):
    from homing_trade.models import Candle
    repo = make(tmp_path)
    repo.save_candles("B-BTC_USDT", "15m", [
        Candle(open=1, high=2, low=0.5, close=1.5, volume=10, time=60000),
        Candle(open=1.5, high=2.5, low=1.0, close=2.0, volume=12, time=120000),
    ], "history")
    assert repo.get_candle_bounds("B-BTC_USDT", "15m") == (60000, 120000)
    got = repo.get_candles_range("B-BTC_USDT", "15m", 0, 200000)
    assert [c.time for c in got] == [60000, 120000]
