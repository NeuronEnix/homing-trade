from homing_trade.broker import Broker
from homing_trade.models import Position

B = Broker(fee=0.0005, slippage=0.0005)


def test_fill_price_buy_is_higher():
    assert B.fill_price(100.0, "LONG", is_entry=True) == 100.0 * 1.0005


def test_fill_price_sell_is_lower():
    assert B.fill_price(100.0, "LONG", is_entry=False) == 100.0 * 0.9995


def test_fill_price_short_entry_is_sell():
    assert B.fill_price(100.0, "SHORT", is_entry=True) == 100.0 * 0.9995


def test_position_size_formula():
    # balance 5000, risk 2% -> risk 100; stop 2% of price; price 100
    # size = 100 / (100 * 0.02) = 50 ; margin = 50*100/3 = 1666.67 <= 5000
    size, margin = B.position_size(5000.0, 100.0, 0.02, 0.02, 3.0)
    assert round(size, 6) == 50.0
    assert round(margin, 2) == 1666.67


def test_position_size_capped_by_margin():
    # tiny stop -> huge size -> margin would exceed balance, so it gets capped
    size, margin = B.position_size(5000.0, 100.0, 0.5, 0.001, 3.0)
    assert round(margin, 2) == 5000.0
    # capped: margin == balance, size = balance*leverage/price = 5000*3/100 = 150
    assert round(size, 6) == 150.0


def test_stop_price_long_and_short():
    assert B.stop_price(100.0, "LONG", 0.02) == 98.0
    assert B.stop_price(100.0, "SHORT", 0.02) == 102.0


def test_realized_pnl_long_profit():
    pos = Position(strategy="x", side="LONG", entry_price=100.0, size=2.0,
                   leverage=3.0, margin=66.7, stop_price=98.0, opened_at=0)
    assert B.realized_pnl(pos, 110.0) == 20.0


def test_realized_pnl_short_profit():
    pos = Position(strategy="x", side="SHORT", entry_price=100.0, size=2.0,
                   leverage=3.0, margin=66.7, stop_price=102.0, opened_at=0)
    assert B.realized_pnl(pos, 90.0) == 20.0


def test_fill_price_short_exit_is_buy():
    # SHORT exit is a buy -> pays more
    assert B.fill_price(100.0, "SHORT", is_entry=False) == 100.0 * 1.0005


def test_entry_fee():
    # fee = size * fill * fee_rate = 2 * 100 * 0.0005 = 0.1
    assert B.entry_fee(2.0, 100.0) == 2.0 * 100.0 * 0.0005


def test_unrealized_pnl_long():
    pos = Position(strategy="x", side="LONG", entry_price=100.0, size=2.0,
                   leverage=3.0, margin=66.7, stop_price=98.0, opened_at=0)
    assert B.unrealized_pnl(pos, 105.0) == 10.0


def test_unrealized_pnl_short():
    pos = Position(strategy="x", side="SHORT", entry_price=100.0, size=2.0,
                   leverage=3.0, margin=66.7, stop_price=102.0, opened_at=0)
    assert B.unrealized_pnl(pos, 95.0) == 10.0
