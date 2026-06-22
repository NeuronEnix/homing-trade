from homing_trade.advisor import Advisor, EntryPlan
from homing_trade.broker import Broker
from homing_trade.config import Config


def test_plan_entry_matches_broker_math():
    cfg = Config(risk_pct=0.02, stop_pct=0.02, leverage_min=5.0, leverage_max=10.0)
    broker = Broker(cfg.fee, cfg.slippage)
    plan = Advisor(cfg, broker).plan_entry(balance=5000.0, entry_fill=100.0, side="LONG")
    assert isinstance(plan, EntryPlan)
    assert plan.leverage == 10.0          # effective_leverage = top of the band
    assert plan.stop_price < 100.0        # LONG stop sits below entry
    size, margin = broker.position_size(5000.0, 100.0, cfg.risk_pct, cfg.stop_pct, 10.0)
    assert plan.size == size and plan.margin == margin
    assert plan.stop_price == broker.stop_price(100.0, "LONG", cfg.stop_pct)


def test_weight_scales_size_down():
    cfg = Config()
    broker = Broker(cfg.fee, cfg.slippage)
    adv = Advisor(cfg, broker)
    full = adv.plan_entry(5000.0, 100.0, "LONG", weight=1.0)
    half = adv.plan_entry(5000.0, 100.0, "LONG", weight=0.5)
    assert half.size < full.size          # lower weight -> smaller risk -> smaller size
