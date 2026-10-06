import csv
from dataclasses import dataclass
from typing import List, Optional, Sequence

from .brokers import SimBroker
from .config import GridConfig
from .engine import GridBot

# Satu candle: (timestamp_ms, open, high, low, close, volume)
Candle = Sequence[float]


def fetch_ohlcv(exchange, symbol: str, timeframe: str, since_ms: int, until_ms: int) -> List[list]:
    candles: List[list] = []
    cursor = since_ms
    while cursor < until_ms:
        batch = exchange.fetch_ohlcv(symbol, timeframe, since=cursor, limit=1000)
        batch = [c for c in batch if c[0] >= cursor and c[0] < until_ms]
        if not batch:
            break
        candles.extend(batch)
        cursor = batch[-1][0] + 1
    return candles


def save_csv(path: str, candles: List[list]) -> None:
    with open(path, "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["timestamp", "open", "high", "low", "close", "volume"])
        w.writerows(candles)


def load_csv(path: str) -> List[list]:
    with open(path) as f:
        rows = list(csv.DictReader(f))
    return [
        [int(r["timestamp"]), float(r["open"]), float(r["high"]), float(r["low"]), float(r["close"]), float(r["volume"])]
        for r in rows
    ]


def candle_path(c: Candle) -> List[float]:
    """Urutan harga dalam satu candle. Candle hijau diasumsikan turun dulu lalu naik."""
    _, o, h, l, close = c[:5]
    return [o, l, h, close] if close >= o else [o, h, l, close]


@dataclass
class BacktestResult:
    candles: int
    start_price: float
    end_price: float
    realized_profit: float
    unrealized_profit: float
    final_equity: float
    return_pct: float
    buy_hold_return_pct: float
    round_trips: int
    max_drawdown_pct: float
    stopped: bool
    stop_reason: Optional[str]


def run_backtest(cfg: GridConfig, candles: List[Candle]) -> BacktestResult:
    if not candles:
        raise ValueError("Tidak ada data candle")
    bot = GridBot(cfg, SimBroker(cfg.fee_rate))
    peak, max_dd = cfg.investment, 0.0
    last = candles[0][4]
    for c in candles:
        for price in candle_path(c):
            bot.step(price)
            last = price
            if bot.stopped:
                break
        eq = bot.equity(last)
        peak = max(peak, eq)
        max_dd = max(max_dd, (peak - eq) / peak)
        if bot.stopped:
            break

    start = candles[0][1]
    equity = bot.equity(last)
    return BacktestResult(
        candles=len(candles),
        start_price=start,
        end_price=last,
        realized_profit=bot.realized_profit,
        unrealized_profit=bot.held_base() * last - bot.open_cost(),
        final_equity=equity,
        return_pct=(equity / cfg.investment - 1) * 100,
        buy_hold_return_pct=(last / start - 1) * 100,
        round_trips=bot.round_trips,
        max_drawdown_pct=max_dd * 100,
        stopped=bot.stopped,
        stop_reason=bot.stop_reason,
    )
