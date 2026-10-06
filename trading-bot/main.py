"""CLI grid bot.

Contoh:
  python main.py download --symbol SOL/USDT --days 90 --out data/sol.csv
  python main.py backtest --csv data/sol.csv --lower 120 --upper 200 --grids 10 --investment 100
  python main.py paper    --lower 120 --upper 200 --grids 10 --investment 100
  python main.py live     --lower 120 --upper 200 --grids 10 --investment 100 --i-understand-the-risk
"""
import argparse
import json
import logging
import os
import sys
import time

import ccxt
from dotenv import load_dotenv

from gridbot.backtest import fetch_ohlcv, load_csv, run_backtest, save_csv
from gridbot.brokers import CcxtBroker, SimBroker
from gridbot.config import GridConfig
from gridbot.engine import GridBot

log = logging.getLogger("gridbot")


def setup_logging() -> None:
    os.makedirs("logs", exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.StreamHandler(), logging.FileHandler("logs/bot.log")],
    )


def make_exchange(private: bool) -> "ccxt.Exchange":
    name = os.getenv("EXCHANGE", "bitget")
    params = {"enableRateLimit": True}
    if private:
        params.update(
            apiKey=os.getenv("API_KEY", ""),
            secret=os.getenv("API_SECRET", ""),
            password=os.getenv("API_PASSPHRASE", ""),
        )
        if not params["apiKey"] or not params["secret"]:
            sys.exit("API_KEY / API_SECRET belum diisi di file .env")
    return getattr(ccxt, name)(params)


def make_config(args) -> GridConfig:
    return GridConfig(
        symbol=args.symbol,
        lower=args.lower,
        upper=args.upper,
        grids=args.grids,
        investment=args.investment,
        fee_rate=args.fee,
        stop_loss=args.stop_loss,
    )


def print_plan(cfg: GridConfig) -> None:
    print(f"Pair            : {cfg.symbol}")
    print(f"Range           : {cfg.lower} - {cfg.upper} ({cfg.grids} grid, jarak {cfg.step:.6f})")
    print(f"Modal           : {cfg.investment} ({cfg.quote_per_grid:.4f} per grid)")
    print(f"Profit min/grid : {cfg.min_net_profit_per_grid * 100:.3f}% setelah fee")
    print(f"Stop-loss       : {cfg.stop_loss if cfg.stop_loss else 'TIDAK ADA'}")


def print_status(bot: GridBot, price: float) -> None:
    log.info(
        "harga %.6f | profit terealisasi %.4f | equity %.4f | siklus %d | koin dipegang %.6f",
        price, bot.realized_profit, bot.equity(price), bot.round_trips, bot.held_base(),
    )


def cmd_download(args) -> None:
    ex = make_exchange(private=False)
    until = ex.milliseconds()
    candles = fetch_ohlcv(ex, args.symbol, args.timeframe, until - args.days * 86_400_000, until)
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    save_csv(args.out, candles)
    print(f"{len(candles)} candle disimpan ke {args.out}")


def cmd_backtest(args) -> None:
    logging.getLogger("gridbot").setLevel(logging.WARNING)
    cfg = make_config(args)
    print_plan(cfg)
    if args.csv:
        candles = load_csv(args.csv)
    else:
        ex = make_exchange(private=False)
        until = ex.milliseconds()
        candles = fetch_ohlcv(ex, args.symbol, args.timeframe, until - args.days * 86_400_000, until)
    r = run_backtest(cfg, candles)
    print("\n=== Hasil backtest ===")
    print(f"Candle          : {r.candles}")
    print(f"Harga           : {r.start_price} -> {r.end_price}")
    print(f"Siklus beli-jual: {r.round_trips}")
    print(f"Profit grid     : {r.realized_profit:.4f}")
    print(f"Untung/rugi koin: {r.unrealized_profit:.4f} (belum dijual)")
    print(f"Equity akhir    : {r.final_equity:.4f} ({r.return_pct:+.2f}%)")
    print(f"Buy & hold      : {r.buy_hold_return_pct:+.2f}%")
    print(f"Max drawdown    : {r.max_drawdown_pct:.2f}%")
    if r.stopped:
        print(f"Bot berhenti    : {r.stop_reason}")
    print("\nCatatan: backtest mengasumsikan limit order selalu terisi saat harga menyentuh level.")


def run_loop(bot: GridBot, get_price, interval: int, on_step=None) -> None:
    last_status = 0.0
    while not bot.stopped:
        try:
            price = get_price()
            bot.step(price)
            if on_step:
                on_step()
            if time.time() - last_status > 300:
                print_status(bot, price)
                last_status = time.time()
        except (ccxt.NetworkError, ccxt.ExchangeNotAvailable) as e:
            log.warning("Gangguan jaringan, coba lagi: %s", e)
        time.sleep(interval)
    log.warning("Bot berhenti: %s", bot.stop_reason)


def cmd_paper(args) -> None:
    cfg = make_config(args)
    print_plan(cfg)
    ex = make_exchange(private=False)
    bot = GridBot(cfg, SimBroker(cfg.fee_rate))
    print("\nPAPER TRADING: harga asli, order disimulasikan. Ctrl+C untuk berhenti.\n")
    try:
        run_loop(bot, lambda: float(ex.fetch_ticker(cfg.symbol)["last"]), args.interval)
    except KeyboardInterrupt:
        price = float(ex.fetch_ticker(cfg.symbol)["last"])
        print_status(bot, price)


def cmd_live(args) -> None:
    if not args.i_understand_the_risk:
        sys.exit("Mode live memakai uang asli. Tambahkan --i-understand-the-risk kalau sudah yakin.")
    cfg = make_config(args)
    print_plan(cfg)
    broker = CcxtBroker(make_exchange(private=True), cfg.symbol, cfg.fee_rate)

    if cfg.quote_per_grid < broker.min_cost():
        sys.exit(f"Modal per grid {cfg.quote_per_grid} di bawah minimum order {broker.min_cost()} {broker.quote}")

    if os.path.exists(args.state_file):
        with open(args.state_file) as f:
            bot = GridBot.from_dict(json.load(f), cfg, broker)
        broker.open_ids = set(bot.open_order_ids())
        log.info("Melanjutkan dari %s (%d order aktif)", args.state_file, len(broker.open_ids))
        if bot.stopped:
            sys.exit(f"State menunjukkan bot sudah berhenti: {bot.stop_reason}")
    else:
        free = broker.free_balance(broker.quote)
        if free < cfg.investment:
            sys.exit(f"Saldo {broker.quote} bebas {free} kurang dari modal {cfg.investment}")
        bot = GridBot(cfg, broker)

    def save_state():
        tmp = args.state_file + ".tmp"
        with open(tmp, "w") as f:
            json.dump(bot.to_dict(), f, indent=2)
        os.replace(tmp, args.state_file)

    print("\nLIVE: order sungguhan. Ctrl+C menyimpan state; order tetap terbuka di exchange.\n")
    try:
        run_loop(bot, broker.last_price, args.interval, on_step=save_state)
    except KeyboardInterrupt:
        print("\nDihentikan. Jalankan perintah yang sama untuk melanjutkan.")
    finally:
        save_state()


def main() -> None:
    load_dotenv()
    setup_logging()

    p = argparse.ArgumentParser(description="Grid trading bot (spot)")
    sub = p.add_subparsers(dest="cmd", required=True)

    def add_grid_args(sp):
        sp.add_argument("--symbol", default="SOL/USDT")
        sp.add_argument("--lower", type=float, required=True, help="batas bawah harga")
        sp.add_argument("--upper", type=float, required=True, help="batas atas harga")
        sp.add_argument("--grids", type=int, default=10)
        sp.add_argument("--investment", type=float, required=True, help="modal dalam quote (USDT)")
        sp.add_argument("--fee", type=float, default=0.001, help="fee per transaksi (0.001 = 0.1%%)")
        sp.add_argument("--stop-loss", type=float, default=None, help="jual semua jika harga <= nilai ini")

    d = sub.add_parser("download", help="unduh data candle ke CSV")
    d.add_argument("--symbol", default="SOL/USDT")
    d.add_argument("--timeframe", default="15m")
    d.add_argument("--days", type=int, default=90)
    d.add_argument("--out", default="data/candles.csv")
    d.set_defaults(func=cmd_download)

    b = sub.add_parser("backtest", help="uji strategi dengan data historis")
    add_grid_args(b)
    b.add_argument("--csv", help="file CSV dari perintah download")
    b.add_argument("--timeframe", default="15m")
    b.add_argument("--days", type=int, default=90)
    b.set_defaults(func=cmd_backtest)

    pp = sub.add_parser("paper", help="simulasi dengan harga live, tanpa uang asli")
    add_grid_args(pp)
    pp.add_argument("--interval", type=int, default=15, help="detik antar pengecekan")
    pp.set_defaults(func=cmd_paper)

    lv = sub.add_parser("live", help="trading sungguhan")
    add_grid_args(lv)
    lv.add_argument("--interval", type=int, default=15)
    lv.add_argument("--state-file", default="state.json")
    lv.add_argument("--i-understand-the-risk", action="store_true")
    lv.set_defaults(func=cmd_live)

    args = p.parse_args()
    try:
        args.func(args)
    except ValueError as e:
        sys.exit(f"Error: {e}")


if __name__ == "__main__":
    main()
