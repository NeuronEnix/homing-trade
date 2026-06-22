from homing_trade.db import Database


def make(tmp_path):
    db = Database(str(tmp_path / "to.db"))
    db.ensure_strategy("ma_trend", 5000.0)
    return db


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
