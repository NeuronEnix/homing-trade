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
