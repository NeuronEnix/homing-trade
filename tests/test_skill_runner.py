from homing_trade.engine import SkillRunner
from homing_trade.broker import Broker
from homing_trade.config import Config
from homing_trade.repository import Repository
from homing_trade.models import Candle


def candles(n=40, price=100.0):
    return [Candle(open=price, high=price + 1, low=price - 1, close=price, volume=1,
                   time=1000 + i * 60000) for i in range(n)]


def _cfg(tmp_path, **kw):
    return Config(db_path=str(tmp_path / "sr.db"), enabled_skills=["ma_trend"],
                  ai_claude_code_enabled=False, ai_anthropic_enabled=False, **kw)


def test_builds_roster_and_ensures_wallets(tmp_path):
    cfg = _cfg(tmp_path)
    repo = Repository.open(cfg.db_path)
    runner = SkillRunner(cfg, repo, Broker(cfg.fee, cfg.slippage))
    assert [s.name for s in runner.skills] == ["ma_trend"]
    assert repo.get_balance("ma_trend") == cfg.starting_balance   # wallet ensured at build


def test_run_tick_acts_only_on_a_new_candle(tmp_path):
    cfg = _cfg(tmp_path)
    repo = Repository.open(cfg.db_path)
    runner = SkillRunner(cfg, repo, Broker(cfg.fee, cfg.slippage))
    cs = candles()
    runner.run_tick(cs, is_paused=lambda: False, commands=None)
    assert repo.get_state("last_candle_time") == str(cs[-1].time)  # cursor advanced
    n_after_first = len(repo.recent_decisions(50))
    assert n_after_first >= 1                                      # a decision was logged
    runner.run_tick(cs, is_paused=lambda: False, commands=None)    # same candle again
    assert len(repo.recent_decisions(50)) == n_after_first         # mechanical skill did not re-run


def test_emits_trade_alerts_for_new_trades_only(tmp_path):
    cfg = _cfg(tmp_path)
    repo = Repository.open(cfg.db_path)
    kinds = []

    class _N:
        def notify(self, kind, title, msg):
            kinds.append(kind)

    runner = SkillRunner(cfg, repo, Broker(cfg.fee, cfg.slippage), notifier=_N())
    # last_alert_id captured at build (0); a new trade should alert exactly once.
    repo.record_trade("ma_trend", 1, "LONG", "OPEN", 100.0, 1.0, 0.1, -0.1, 1000)
    runner._emit_trade_alerts()
    assert kinds == ["trade"]
    runner._emit_trade_alerts()          # no new trades -> no duplicate alert
    assert kinds == ["trade"]
