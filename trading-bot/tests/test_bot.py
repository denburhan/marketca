import math

import pytest

from gridbot.backtest import candle_path, run_backtest
from gridbot.brokers import SimBroker
from gridbot.config import GridConfig
from gridbot.engine import BUYING, IDLE, SELLING, GridBot, OrderEvent


def cfg(**kw):
    base = dict(symbol="SOL/USDT", lower=100, upper=200, grids=10, investment=1000, fee_rate=0.001)
    base.update(kw)
    return GridConfig(**base)


def test_levels_and_per_grid():
    c = cfg()
    assert c.levels[0] == 100 and c.levels[-1] == 200 and len(c.levels) == 11
    assert c.quote_per_grid == 100


@pytest.mark.parametrize(
    "kw",
    [dict(lower=0), dict(upper=90), dict(grids=1), dict(investment=0), dict(stop_loss=150), dict(fee_rate=0.05)],
)
def test_invalid_config(kw):
    with pytest.raises(ValueError):
        cfg(**kw)


def test_grid_too_tight_for_fees():
    with pytest.raises(ValueError, match="terlalu rapat"):
        cfg(lower=100, upper=101, grids=10)


def test_start_places_buys_only_below_price():
    bot = GridBot(cfg(), SimBroker(0.001))
    bot.step(155)
    states = [s.state for s in bot.slots]
    assert states[:6] == [BUYING] * 6  # level 100..150
    assert states[6:] == [IDLE] * 4


def test_round_trip_profit_matches_fees():
    bot = GridBot(cfg(), SimBroker(0.001))
    bot.step(155)
    bot.step(150)  # buy slot 5 @150
    assert bot.slots[5].state == SELLING
    bot.step(160)  # sell slot 5 @160
    assert bot.round_trips == 1
    floor6 = lambda x: math.floor(x / 1e-6 + 1e-9) * 1e-6
    bought = floor6(100 / 150)
    sold = floor6(bought * (1 - 0.001))  # sisa pembulatan jadi dust
    expected = sold * 160 * (1 - 0.001) - bought * 150
    assert bot.realized_profit == pytest.approx(expected, rel=1e-6)
    assert bot.slots[5].state == BUYING  # buy dipasang ulang


def test_idle_slots_activate_when_price_rises():
    bot = GridBot(cfg(), SimBroker(0.001))
    bot.step(125)
    assert bot.slots[5].state == IDLE
    bot.step(175)
    assert bot.slots[5].state == BUYING and bot.slots[7].state == BUYING


def test_never_spends_more_than_investment():
    bot = GridBot(cfg(), SimBroker(0.001))
    bot.step(199)
    bot.step(50.5)  # semua buy terisi, belum ada stop-loss
    assert all(s.state == SELLING for s in bot.slots)
    assert 999.99 < bot.open_cost() <= 1000


def test_stop_loss_sells_everything_and_stops():
    bot = GridBot(cfg(stop_loss=90), SimBroker(0.001))
    bot.step(155)
    bot.step(120)  # beli slot 2..5
    bot.step(85)
    assert bot.stopped and "stop-loss" in bot.stop_reason
    assert bot.held_base() == 0 and bot.open_order_ids() == []
    assert bot.realized_profit < 0
    assert bot.equity(85) == pytest.approx(1000 + bot.realized_profit)


def test_partial_cancel_halts_bot():
    broker = SimBroker(0.001)
    bot = GridBot(cfg(), broker)
    bot.step(155)
    oid = bot.slots[5].order_id
    broker.orders.pop(oid)
    broker.poll = lambda price: [OrderEvent(oid, "canceled", "buy", 150, 0.1, 15)]
    bot.step(152)
    assert bot.stopped and "sebagian" in bot.stop_reason


def test_state_roundtrip():
    c = cfg()
    bot = GridBot(c, SimBroker(0.001))
    bot.step(155)
    bot.step(140)
    restored = GridBot.from_dict(bot.to_dict(), c, SimBroker(0.001))
    assert restored.to_dict() == bot.to_dict()
    with pytest.raises(ValueError):
        GridBot.from_dict(bot.to_dict(), cfg(grids=8), SimBroker(0.001))


def test_candle_path_order():
    assert candle_path([0, 10, 12, 8, 11, 0]) == [10, 8, 12, 11]
    assert candle_path([0, 10, 12, 8, 9, 0]) == [10, 12, 8, 9]


def sine_candles(n=2000, mid=150, amp=40):
    out, prev = [], mid
    for i in range(n):
        close = mid + amp * math.sin(i / 30)
        out.append([i * 900_000, prev, max(prev, close) + 0.5, min(prev, close) - 0.5, close, 1])
        prev = close
    return out


def test_backtest_sideways_market_is_profitable():
    r = run_backtest(cfg(), sine_candles())
    assert r.round_trips > 50
    assert r.realized_profit > 0
    assert r.final_equity == pytest.approx(1000 + r.realized_profit + r.unrealized_profit)


def test_backtest_crash_triggers_stop_loss():
    candles = [[i, 150 - i, 151 - i, 149 - i, 149.5 - i, 1] for i in range(100)]
    r = run_backtest(cfg(stop_loss=80), candles)
    assert r.stopped and r.return_pct < 0
