"""Logika grid yang sama dipakai untuk backtest, paper trading, dan live.

Model: range [lower, upper] dibagi menjadi `grids` slot. Slot i berada di antara
level i dan level i+1. Setiap slot punya tepat satu order aktif:
  - BUYING : limit buy di level i (slot kosong, menunggu harga turun)
  - SELLING: limit sell di level i+1 (slot memegang koin, menunggu harga naik)
  - IDLE   : belum aktif (level i di atas harga saat ini)

Bot mulai hanya dengan quote (USDT), tanpa market buy di awal.
"""
import logging
from dataclasses import asdict, dataclass
from typing import List, Optional

from .config import GridConfig

log = logging.getLogger(__name__)

IDLE, BUYING, SELLING = "idle", "buying", "selling"


@dataclass
class Slot:
    index: int
    state: str = IDLE
    order_id: Optional[str] = None
    base_amount: float = 0.0  # koin yang dipegang slot (state SELLING)
    cost_quote: float = 0.0  # quote yang dibayar untuk koin itu, termasuk fee


@dataclass
class OrderEvent:
    order_id: str
    status: str  # "filled" atau "canceled"
    side: str
    price: float
    filled: float  # jumlah base yang terisi (gross)
    cost: float  # filled * price (gross, dalam quote)
    fee_base: float = 0.0
    fee_quote: float = 0.0


class GridBot:
    def __init__(self, config: GridConfig, broker):
        self.cfg = config
        self.broker = broker
        self.levels = config.levels
        self.slots: List[Slot] = [Slot(i) for i in range(config.grids)]
        self.realized_profit = 0.0
        self.round_trips = 0
        self.dust_base = 0.0
        self.stopped = False
        self.stop_reason: Optional[str] = None

    # ---- loop utama -------------------------------------------------------

    def step(self, price: float) -> None:
        if self.stopped:
            return
        for event in self.broker.poll(price):
            self._on_event(event)
            if self.stopped:
                return
        if self.cfg.stop_loss is not None and price <= self.cfg.stop_loss:
            self.stop_loss_exit(price)
            return
        self._activate_idle(price)

    def _activate_idle(self, price: float) -> None:
        for slot in self.slots:
            if slot.state == IDLE and self.levels[slot.index] < price:
                self._place_buy(slot)

    def _place_buy(self, slot: Slot) -> None:
        level = self.levels[slot.index]
        amount = self.broker.round_amount(self.cfg.quote_per_grid / level)
        if amount <= 0:
            raise ValueError("Ukuran order per grid terlalu kecil setelah pembulatan")
        slot.order_id = self.broker.place_limit("buy", level, amount)
        slot.state = BUYING
        log.info("BUY  order slot %d @ %.6f amount %.8f", slot.index, level, amount)

    def _place_sell(self, slot: Slot) -> None:
        level = self.levels[slot.index + 1]
        amount = self.broker.round_amount(slot.base_amount)
        self.dust_base += slot.base_amount - amount
        slot.base_amount = amount
        slot.order_id = self.broker.place_limit("sell", level, amount)
        slot.state = SELLING
        log.info("SELL order slot %d @ %.6f amount %.8f", slot.index, level, amount)

    def _on_event(self, ev: OrderEvent) -> None:
        slot = next((s for s in self.slots if s.order_id == ev.order_id), None)
        if slot is None:
            log.warning("Event untuk order tak dikenal %s, diabaikan", ev.order_id)
            return

        if ev.status == "canceled":
            if ev.filled > 0:
                self.halt(f"Order {ev.order_id} dibatalkan setelah terisi sebagian; cek manual di exchange")
            elif slot.state == BUYING:
                log.warning("Buy order slot %d dibatalkan dari luar, akan dipasang ulang", slot.index)
                slot.state, slot.order_id = IDLE, None
            else:
                log.warning("Sell order slot %d dibatalkan dari luar, dipasang ulang", slot.index)
                self._place_sell(slot)
            return

        if slot.state == BUYING:
            slot.base_amount = ev.filled - ev.fee_base
            slot.cost_quote = ev.cost + ev.fee_quote
            log.info("FILLED buy  slot %d @ %.6f", slot.index, ev.price)
            self._place_sell(slot)
        elif slot.state == SELLING:
            proceeds = ev.cost - ev.fee_quote
            profit = proceeds - slot.cost_quote
            self.realized_profit += profit
            self.round_trips += 1
            log.info("FILLED sell slot %d @ %.6f profit %.4f", slot.index, ev.price, profit)
            slot.state, slot.order_id = IDLE, None
            slot.base_amount = slot.cost_quote = 0.0

    # ---- berhenti ---------------------------------------------------------

    def _cancel_all(self) -> None:
        for slot in self.slots:
            if slot.state in (BUYING, SELLING) and slot.order_id:
                self.broker.cancel(slot.order_id)
                slot.order_id = None
            if slot.state == BUYING:
                slot.state = IDLE

    def stop_loss_exit(self, price: float) -> None:
        """Batalkan semua order dan jual semua koin yang dipegang bot di harga pasar."""
        self._cancel_all()
        held = sum(s.base_amount for s in self.slots if s.state == SELLING) + self.dust_base
        cost = sum(s.cost_quote for s in self.slots if s.state == SELLING)
        proceeds = self.broker.market_sell(held, price) if held > 0 else 0.0
        self.realized_profit += proceeds - cost
        for slot in self.slots:
            slot.state, slot.order_id = IDLE, None
            slot.base_amount = slot.cost_quote = 0.0
        self.dust_base = 0.0
        self.stopped = True
        self.stop_reason = f"stop-loss tersentuh di {price:.6f}"
        log.warning("STOP-LOSS: semua koin dijual, hasil %.4f", proceeds)

    def halt(self, reason: str) -> None:
        """Berhenti tanpa menjual apa pun (butuh pengecekan manual)."""
        self._cancel_all()
        self.stopped = True
        self.stop_reason = reason
        log.error("BOT BERHENTI: %s", reason)

    # ---- statistik --------------------------------------------------------

    def held_base(self) -> float:
        return sum(s.base_amount for s in self.slots if s.state == SELLING) + self.dust_base

    def open_cost(self) -> float:
        return sum(s.cost_quote for s in self.slots if s.state == SELLING)

    def equity(self, price: float) -> float:
        return self.cfg.investment + self.realized_profit - self.open_cost() + self.held_base() * price

    def open_order_ids(self) -> List[str]:
        return [s.order_id for s in self.slots if s.order_id]

    # ---- simpan / lanjutkan (untuk live) ------------------------------------

    def to_dict(self) -> dict:
        return {
            "config": asdict(self.cfg),
            "slots": [asdict(s) for s in self.slots],
            "realized_profit": self.realized_profit,
            "round_trips": self.round_trips,
            "dust_base": self.dust_base,
            "stopped": self.stopped,
            "stop_reason": self.stop_reason,
        }

    @classmethod
    def from_dict(cls, data: dict, config: GridConfig, broker) -> "GridBot":
        if data["config"] != asdict(config):
            raise ValueError(
                "Config di state file berbeda dengan config sekarang. "
                "Pakai config yang sama, atau hapus state file setelah membatalkan order lama."
            )
        bot = cls(config, broker)
        bot.slots = [Slot(**s) for s in data["slots"]]
        bot.realized_profit = data["realized_profit"]
        bot.round_trips = data["round_trips"]
        bot.dust_base = data["dust_base"]
        bot.stopped = data["stopped"]
        bot.stop_reason = data["stop_reason"]
        return bot
