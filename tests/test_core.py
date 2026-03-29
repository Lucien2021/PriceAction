"""End-to-end test for replay engine, training modes, and stats service."""
import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from datetime import datetime, timedelta

from app.domain.candle import (
    Candle,
    ClosedTrade,
    Symbol,
    Timeframe,
    MarketType,
    TradeDirection,
    PredictionDirection,
)
from app.domain.market_rules import AShareRules, FutureRules
from app.replay.session import ReplaySession, TrainingMode, SessionState
from app.training.trade_mode import TradeMode, TradeStats
from app.training.predict_mode import PredictMode
from app.storage.models import get_connection
from app.storage.stats_service import StatsService
from app.storage.challenge_service import ChallengeService, average_challenge_hold_days
from app.training.challenge_trade_mode import ChallengeTradeMode
from app.data.cache.repository import CacheRepository
from app.data.providers.akshare_provider import AKShareProvider
from app.replay.engine import ReplayEngine


def make_candles(n=100, base_price=10.0):
    candles = []
    price = base_price
    for i in range(n):
        o = price
        h = o + 0.3
        l = o - 0.2
        c = o + 0.1 * (1 if i % 3 != 0 else -1)
        candles.append(Candle(
            timestamp=datetime(2025, 1, 1) + timedelta(days=i),
            open=round(o, 2), high=round(h, 2),
            low=round(l, 2), close=round(c, 2),
            volume=10000.0,
        ))
        price = c
    return candles


def test_market_rules():
    rules = AShareRules()
    assert not rules.allows_short()
    assert not rules.allows_t0()
    assert rules.contract_multiplier() == 1.0
    lo, hi = rules.price_limit(10.0)
    assert lo == 9.0 and hi == 11.0
    print("[PASS] AShareRules")

    fr = FutureRules()
    assert fr.allows_short()
    assert fr.allows_t0()
    assert fr.contract_multiplier() == 10.0
    print("[PASS] FutureRules")


def test_risk_position_capped_by_capital():
    session = ReplaySession()
    tm = TradeMode(session, initial_capital=100_000.0, slippage_pct=0)
    assert tm.max_affordable_quantity(10.0) == 10_000
    # 止损极近时按风险公式会算出超大股数，必须不超过满仓
    q_tight = tm.calculate_risk_position(1.0, 10.0, 9.99)
    assert q_tight == 10_000
    q_normal = tm.calculate_risk_position(1.0, 10.0, 9.0)
    assert q_normal == 1_000
    print("[PASS] risk position capped by capital")


def test_t1_long_blocks_same_day_close():
    candles = make_candles(100)
    symbol = Symbol("000001", "Test", MarketType.A_SHARE)
    session = ReplaySession()
    session.setup(symbol, Timeframe.DAILY, TrainingMode.TRADE, candles[:60], candles[60:])
    session.start()
    tm = TradeMode(session, slippage_pct=0.0, rules=AShareRules())
    session.advance(5)
    assert tm.open_long(1000, stop_loss=candles[64].close - 2.0)
    assert tm.close("manual") is None
    assert not tm.can_close_position_now()
    session.advance(1)
    assert tm.can_close_position_now()
    closed = tm.close("manual")
    assert closed is not None
    print("[PASS] T+1 blocks same-day close (A-share)")


def test_t0_future_allows_same_day_close():
    candles = make_candles(100)
    symbol = Symbol("cu8888", "Test", MarketType.FUTURE)
    session = ReplaySession()
    session.setup(symbol, Timeframe.DAILY, TrainingMode.TRADE, candles[:60], candles[60:])
    session.start()
    tm = TradeMode(session, slippage_pct=0.0, rules=FutureRules())
    session.advance(5)
    assert tm.open_long(1000, stop_loss=candles[64].close - 2.0)
    assert tm.can_close_position_now()
    closed = tm.close("manual")
    assert closed is not None
    print("[PASS] T+0 same-day close (futures)")


def test_trade_mode():
    candles = make_candles(200)
    symbol = Symbol("000001", "Test", MarketType.A_SHARE)

    session = ReplaySession()
    session.setup(symbol, Timeframe.DAILY, TrainingMode.TRADE, candles[:60], candles[60:])
    session.start()

    tm = TradeMode(session, initial_capital=100000)
    assert tm.capital == 100000

    session.advance(5)
    pos = tm.open_long(1000, stop_loss=candles[64].close - 1.0, take_profit=candles[64].close + 2.0)
    assert pos is not None
    assert pos.is_long

    session.advance(10)
    tm.close("manual")
    assert len(session.closed_trades) == 1
    trade = session.closed_trades[0]
    assert trade.pnl != 0
    assert trade.r_multiple is not None
    print(f"  Trade PnL: {trade.pnl:+.2f}, R: {trade.r_multiple:.2f}")

    session.advance(5)
    tm.open_long(500)
    session.advance(5)
    tm.close("manual")

    stats = tm.compute_stats()
    assert stats.total_trades == 2
    assert 0 <= stats.win_rate <= 1
    print(f"  Stats: trades={stats.total_trades}, win_rate={stats.win_rate:.1%}, "
          f"avg_r={stats.avg_r}, pf={stats.profit_factor:.2f}")
    print("[PASS] TradeMode")


def test_predict_mode():
    candles = make_candles(200)
    symbol = Symbol("000001", "Test", MarketType.A_SHARE)

    session = ReplaySession()
    session.setup(symbol, Timeframe.DAILY, TrainingMode.PREDICT, candles[:60], candles[60:])
    session.start()

    pm = PredictMode(session)
    pm.predict(PredictionDirection.UP, lookahead=3)
    pm.reveal_and_evaluate(5)

    pm.predict(PredictionDirection.DOWN, lookahead=3)
    pm.reveal_and_evaluate(5)

    result = pm.get_result()
    assert result.total == 2
    print(f"  Predictions: total={result.total}, correct={result.correct}, "
          f"accuracy={result.accuracy:.1%}")
    print("[PASS] PredictMode")


def test_stats_service():
    conn = get_connection(":memory:")
    svc = StatsService(conn)

    candles = make_candles(200)
    symbol = Symbol("000001", "Test", MarketType.A_SHARE)

    session = ReplaySession()
    session.setup(symbol, Timeframe.DAILY, TrainingMode.TRADE, candles[:60], candles[60:])
    session.start()
    tm = TradeMode(session)

    for i in range(5):
        session.advance(3)
        sl = session.current_candle.close - 0.5
        tp = session.current_candle.close + 1.0
        tm.open_long(1000, stop_loss=sl, take_profit=tp)
        session.advance(5)
        tm.close("manual")

    session.finish()
    svc.save_session(session)

    agg = svc.get_overall_stats()
    assert agg.total_sessions == 1
    assert agg.total_trades == 5
    assert 0 <= agg.win_rate <= 1
    print(f"  Aggregate: sessions={agg.total_sessions}, trades={agg.total_trades}, "
          f"win_rate={agg.win_rate:.1%}, avg_r={agg.avg_r}, pnl={agg.total_pnl:+.2f}")

    curve = svc.get_equity_curve()
    assert len(curve) == 5
    print(f"  Equity curve points: {len(curve)}, final: {curve[-1]['cumulative_pnl']:+.2f}")

    rolling = svc.get_rolling_win_rate(3)
    assert len(rolling) == 5
    print(f"  Rolling win rate (last): {rolling[-1]['win_rate']:.1%}")

    r_dist = svc.get_r_distribution()
    print(f"  R distribution: {r_dist}")

    svc.save_note(session.session_id, "Test review note", ["Pin Bar", "Trend Follow"])
    notes = svc.get_session_notes(session.session_id)
    assert len(notes) == 1
    assert notes[0]["content"] == "Test review note"
    print("[PASS] StatsService")

    svc.close()


def test_stop_loss_trigger():
    candles = []
    base = 10.0
    for i in range(100):
        if i == 70:
            o, h, l, c = base, base + 0.1, base - 2.0, base - 1.5
        else:
            o, h, l, c = base, base + 0.3, base - 0.1, base + 0.1
        candles.append(Candle(
            timestamp=datetime(2025, 1, 1) + timedelta(days=i),
            open=round(o, 2), high=round(h, 2),
            low=round(l, 2), close=round(c, 2), volume=10000,
        ))
        base = c

    symbol = Symbol("600000", "Test", MarketType.A_SHARE)
    session = ReplaySession()
    session.setup(symbol, Timeframe.DAILY, TrainingMode.TRADE, candles[:60], candles[60:])
    session.start()
    tm = TradeMode(session, slippage_pct=0.0)

    session.advance(5)
    cur = session.current_candle.close
    stop_px = cur - 1.0
    tm.open_long(1000, stop_loss=stop_px)

    closed = tm.advance_and_check(10)
    assert closed is not None
    assert closed.exit_reason == "stop_loss"
    # Intraday pierce: open above stop, low hits stop -> fill at stop (min(stop, open))
    c70 = candles[70]
    assert closed.exit_price == round(min(stop_px, c70.open), 2)
    print(f"  Stop loss triggered: exit={closed.exit_price:.2f}, pnl={closed.pnl:+.2f}")
    print("[PASS] StopLoss trigger")


def test_stop_loss_gap_fill_at_open():
    """Open gaps below stop: fill at open, not at stop price."""
    candles = []
    for i in range(100):
        if i == 65:
            o, h, l, c = 12.0, 12.2, 11.5, 11.8
        else:
            o, h, l, c = 15.0, 15.3, 14.9, 15.0
        candles.append(Candle(
            timestamp=datetime(2025, 1, 1) + timedelta(days=i),
            open=round(o, 2), high=round(h, 2),
            low=round(l, 2), close=round(c, 2), volume=10000,
        ))

    symbol = Symbol("600000", "Test", MarketType.A_SHARE)
    session = ReplaySession()
    session.setup(symbol, Timeframe.DAILY, TrainingMode.TRADE, candles[:60], candles[60:])
    session.start()
    tm = TradeMode(session, slippage_pct=0.0)

    session.advance(5)
    stop_px = 13.0
    tm.open_long(1000, stop_loss=stop_px)

    closed = tm.advance_and_check(10)
    assert closed is not None
    assert closed.exit_reason == "stop_loss"
    assert closed.exit_price == 12.0
    assert closed.exit_price < stop_px
    print(f"  Gap stop loss: exit={closed.exit_price:.2f} (open), stop was {stop_px:.2f}")
    print("[PASS] StopLoss gap fill at open")


def test_challenge_slice_contiguous_tail():
    cache = CacheRepository(":memory:")
    eng = ReplayEngine(cache, AKShareProvider())
    candles = make_candles(120)
    sym = Symbol("000001", "T", MarketType.A_SHARE)
    cache.save_candles(sym, Timeframe.DAILY, candles)
    for _ in range(5):
        vis, fut = eng.challenge_slice(sym, Timeframe.DAILY, 60)
        assert len(vis) == 60
        assert len(fut) >= 2
        flat = vis + fut
        ts_flat = [c.timestamp for c in flat]
        ok = False
        for s in range(len(candles) - len(flat) + 1):
            if [c.timestamp for c in candles[s : s + len(flat)]] == ts_flat:
                ok = True
                break
        assert ok
    pool = eng.symbols_with_timeframe(Timeframe.DAILY, min_bars=61)
    assert "000001" in pool


def test_challenge_trade_mode_outcome():
    candles = make_candles(80)
    sym = Symbol("000001", "T", MarketType.A_SHARE)
    session = ReplaySession()
    session.setup(sym, Timeframe.DAILY, TrainingMode.CHALLENGE, candles[:60], candles[20:70])
    session.start()
    ctm = ChallengeTradeMode(session, target_amount=90_000, initial_capital=50_000, slippage_pct=0.0, use_commission=False)
    ctm._capital = 95_000
    ctm.check_outcome_after_advance()
    assert ctm.challenge_outcome == "win"

    session2 = ReplaySession()
    session2.setup(sym, Timeframe.DAILY, TrainingMode.CHALLENGE, candles[:60], candles[20:70])
    session2.start()
    ctm2 = ChallengeTradeMode(session2, target_amount=200_000, initial_capital=50_000, slippage_pct=0.0, use_commission=False)
    ctm2._capital = 0.0
    ctm2.check_outcome_after_advance()
    assert ctm2.challenge_outcome == "lose"


def test_average_challenge_hold_days():
    from datetime import datetime as dt
    t1 = ClosedTrade(
        direction=TradeDirection.LONG,
        entry_price=10.0,
        exit_price=11.0,
        quantity=100,
        entry_time=dt(2026, 1, 1),
        exit_time=dt(2026, 1, 5),
        entry_bar_index=100,
        exit_bar_index=103,
    )
    t2 = ClosedTrade(
        direction=TradeDirection.LONG,
        entry_price=10.0,
        exit_price=9.0,
        quantity=100,
        entry_time=dt(2026, 1, 6),
        exit_time=dt(2026, 1, 6),
        entry_bar_index=200,
        exit_bar_index=200,
    )
    assert average_challenge_hold_days([t1]) == 4.0
    assert abs(average_challenge_hold_days([t1, t2]) - 2.5) < 1e-6


def test_challenge_service_save():
    conn = get_connection(":memory:")
    svc = ChallengeService(conn)
    from datetime import datetime as dt
    rid = svc.save_run(
        target_amount=500_000,
        initial_capital=200_000,
        final_equity=180_000,
        outcome="lose",
        bars_elapsed=42,
        calendar_seconds=3600,
        avg_hold_bars=3.5,
        trade_count=2,
        win_rate=0.5,
        profit_factor=0.8,
        total_commission=12.0,
        max_drawdown_pct=5.0,
        symbols_used=["000001", "600000"],
        started_at=dt(2026, 1, 1, 10, 0, 0),
        finished_at=dt(2026, 1, 1, 11, 0, 0),
        last_symbol="600000",
    )
    assert rid >= 1
    grouped = svc.list_runs_grouped_by_target()
    assert 500_000.0 in grouped
    assert len(grouped[500_000.0]) == 1
    svc.close()


if __name__ == "__main__":
    test_market_rules()
    test_risk_position_capped_by_capital()
    test_t1_long_blocks_same_day_close()
    test_t0_future_allows_same_day_close()
    test_trade_mode()
    test_predict_mode()
    test_stats_service()
    test_stop_loss_trigger()
    test_stop_loss_gap_fill_at_open()
    test_challenge_slice_contiguous_tail()
    test_challenge_trade_mode_outcome()
    test_average_challenge_hold_days()
    test_challenge_service_save()
    print("\n=== ALL TESTS PASSED ===")
