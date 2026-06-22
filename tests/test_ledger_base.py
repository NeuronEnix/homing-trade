import pytest
from homing_trade.ledger_base import Ledger
from homing_trade.ledger import MemoryLedger
from homing_trade.repository import Repository


def test_both_backends_are_ledgers(tmp_path):
    assert isinstance(MemoryLedger("ma_trend", 5000.0), Ledger)
    assert isinstance(Repository.open(str(tmp_path / "l.db")), Ledger)


def test_ledger_abc_cannot_be_instantiated():
    with pytest.raises(TypeError):
        Ledger()


def test_incomplete_backend_cannot_instantiate():
    class Partial(Ledger):
        pass  # implements none of the abstract methods
    with pytest.raises(TypeError):
        Partial()
