from homing_trade.db import Database
from homing_trade.models import Candle


def make(tmp_path):
    db = Database(str(tmp_path / "to.db"))
    db.ensure_strategy("ma_trend", 5000.0)
    return db


def _candles(times_hl):
    """Build candles from (time, high, low) tuples; open/close/volume don't matter here."""
    return [Candle(open=h, high=h, low=l, close=l, volume=1.0, time=t) for (t, h, l) in times_hl]


def test_rebuild_joins_open_close(tmp_path):
    db = make(tmp_path)
    # position 1: OPEN @100 (fee .1, pnl -.1), CLOSE @110 (fee .11, pnl 9.89)
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000,
                    decision_price=100.0, slippage=0.02)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.11, 9.89, 5000,
                    decision_price=110.0, slippage=0.03)
    # position 2: only OPEN (still running) -> must be skipped
    db.record_trade("ma_trend", 2, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 2000)
    db.rebuild_trade_outcomes()
    outs = db.trade_outcomes()
    assert len(outs) == 1
    o = outs[0]
    assert o["position_id"] == 1 and o["side"] == "LONG"
    assert o["entry_price"] == 100.0 and o["exit_price"] == 110.0
    assert o["entry_ts"] == 1000 and o["exit_ts"] == 5000
    assert o["holding_period_ms"] == 4000
    assert round(o["realized_pnl"], 2) == round(-0.1 + 9.89, 2)   # sum of the trades' pnl
    assert round(o["fees"], 2) == 0.21
    assert round(o["slippage"], 2) == 0.05
    assert o["realized_at_ts"] == 5000


def test_embargo_as_of(tmp_path):
    db = make(tmp_path)
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 5000)
    db.rebuild_trade_outcomes()
    assert db.trade_outcomes(as_of=4000) == []          # realized_at_ts 5000 > 4000 -> hidden
    assert len(db.trade_outcomes(as_of=6000)) == 1      # now visible
    assert len(db.trade_outcomes()) == 1                # no embargo -> all rows


def test_exit_reason_flows_into_outcome(tmp_path):
    db = make(tmp_path)
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 5000, exit_reason="stop")
    db.rebuild_trade_outcomes()
    assert db.trade_outcomes()[0]["exit_reason"] == "stop"   # carried from the CLOSE trade


def test_decision_and_regime_flow_into_outcome(tmp_path):
    db = make(tmp_path)
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000,
                    decision_id="abc123", regime_at_entry="trend_up")
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 5000, exit_reason="signal")
    db.rebuild_trade_outcomes()
    o = db.trade_outcomes()[0]
    assert o["decision_id"] == "abc123"        # from the OPEN trade
    assert o["regime_at_entry"] == "trend_up"
    assert o["exit_reason"] == "signal"


def test_prediction_correct_is_directional(tmp_path):
    db = make(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    # LONG up -> correct
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 2000)
    # LONG down -> incorrect
    db.record_trade("ma_trend", 3, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 3000)
    db.record_trade("ma_trend", 3, "LONG", "CLOSE", 90.0, 1.0, 0.1, -10.1, 4000)
    # SHORT down -> correct
    db.record_trade("grid", 2, "SHORT", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("grid", 2, "SHORT", "CLOSE", 95.0, 1.0, 0.1, 4.9, 2000)
    db.rebuild_trade_outcomes()
    by_pos = {o["position_id"]: o for o in db.trade_outcomes()}
    assert by_pos[1]["prediction_correct"] == 1   # LONG, price up
    assert by_pos[3]["prediction_correct"] == 0   # LONG, price down
    assert by_pos[2]["prediction_correct"] == 1   # SHORT, price down


def test_mae_mfe_long_from_candle_path(tmp_path):
    db = make(tmp_path)
    # LONG @100 held 1000->5000. Path runs up to high 112 and dips to low 96 mid-flight.
    db.save_candles("B-BTC_USDT", "15m", _candles([
        (1000, 101, 100), (2000, 106, 99), (3000, 112, 104), (4000, 108, 96), (5000, 110, 105),
    ]), "test")
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 5000)
    db.rebuild_trade_outcomes(pair="B-BTC_USDT", interval="15m")
    o = db.trade_outcomes()[0]
    assert round(o["mfe"], 4) == 0.12     # best: high 112 -> (112-100)/100
    assert round(o["mae"], 4) == -0.04    # worst: low 96 -> (96-100)/100


def test_mae_mfe_short_is_mirrored(tmp_path):
    db = make(tmp_path)
    # SHORT @100: favorable = price falls (low 90), adverse = price rises (high 105).
    db.save_candles("B-BTC_USDT", "15m", _candles([
        (1000, 102, 98), (2000, 105, 95), (3000, 101, 90),
    ]), "test")
    db.record_trade("ma_trend", 1, "SHORT", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "SHORT", "CLOSE", 92.0, 1.0, 0.1, 7.9, 3000)
    db.rebuild_trade_outcomes(pair="B-BTC_USDT", interval="15m")
    o = db.trade_outcomes()[0]
    assert round(o["mfe"], 4) == 0.10     # favorable: low 90 -> (100-90)/100
    assert round(o["mae"], 4) == -0.05    # adverse: high 105 -> (100-105)/100


def test_mae_mfe_window_snaps_to_entry_bar_despite_wallclock_latency(tmp_path):
    db = make(tmp_path)
    # Bars open on a 15m grid (900000, 1800000). The position opens at WALL-CLOCK 900350 —
    # a little after the 900000 bar opened, due to fetch/processing latency — so a naive
    # `time >= entry_ts` filter would skip the very bar the position opened into and miss its
    # low. The anchor snaps the lower bound down to that bar so its 96 low is the true MAE.
    db.save_candles("B-BTC_USDT", "15m", _candles([
        (900000, 108, 96), (1800000, 110, 102),
    ]), "test")
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 900350)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 105.0, 1.0, 0.1, 4.9, 1800500)
    db.rebuild_trade_outcomes(pair="B-BTC_USDT", interval="15m")
    o = db.trade_outcomes()[0]
    assert round(o["mae"], 4) == -0.04    # low 96 from the entry bar (dropped by a naive filter)
    assert round(o["mfe"], 4) == 0.10     # high 110


def test_mae_mfe_clamped_to_invariant(tmp_path):
    db = make(tmp_path)
    # LONG whose whole candle window sits ABOVE entry (gapped up, never underwater): an
    # adverse excursion can't be positive, so MAE clamps to 0.0 (not +0.03).
    db.save_candles("B-BTC_USDT", "15m", _candles([(1000, 115, 103), (2000, 120, 108)]), "test")
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 118.0, 1.0, 0.1, 17.9, 2000)
    db.rebuild_trade_outcomes(pair="B-BTC_USDT", interval="15m")
    o = db.trade_outcomes()[0]
    assert o["mae"] == 0.0                 # never below entry -> clamped
    assert round(o["mfe"], 4) == 0.20      # high 120


def test_mae_mfe_null_without_pair_or_candles(tmp_path):
    db = make(tmp_path)
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 5000)
    db.rebuild_trade_outcomes()                          # no pair/interval -> NULL excursions
    o = db.trade_outcomes()[0]
    assert o["mae"] is None and o["mfe"] is None
    # pair/interval given but no candles cover the window -> still NULL (not a crash)
    db.rebuild_trade_outcomes(pair="B-BTC_USDT", interval="15m")
    o = db.trade_outcomes()[0]
    assert o["mae"] is None and o["mfe"] is None


def test_rebuild_idempotent_and_filter(tmp_path):
    db = make(tmp_path)
    db.ensure_strategy("grid", 5000.0)
    db.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("ma_trend", 1, "LONG", "CLOSE", 110.0, 1.0, 0.1, 9.9, 5000)
    db.record_trade("grid", 2, "SHORT", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    db.record_trade("grid", 2, "SHORT", "CLOSE", 95.0, 1.0, 0.1, 4.9, 3000)
    db.rebuild_trade_outcomes()
    db.rebuild_trade_outcomes()                          # rerun -> DELETE+reinsert, no dupes
    assert len(db.trade_outcomes()) == 2
    assert [o["position_id"] for o in db.trade_outcomes(strategy="grid")] == [2]
