"""Broker = jembatan antara GridBot dan tempat order dieksekusi.

SimBroker  : simulasi lokal (backtest & paper trading), tidak menyentuh uang asli.
CcxtBroker : order sungguhan ke exchange lewat ccxt.
"""
import logging
import math
from typing import Dict, List

import ccxt

from .engine import OrderEvent

log = logging.getLogger(__name__)


class SimBroker:
    """Limit order terisi penuh begitu harga menyentuh level-nya."""

    def __init__(self, fee_rate: float, amount_step: float = 1e-6):
        self.fee_rate = fee_rate
        self.amount_step = amount_step
        self.orders: Dict[str, dict] = {}
        self._next_id = 1

    def round_amount(self, amount: float) -> float:
        steps = math.floor(amount / self.amount_step + 1e-9)
        return round(steps * self.amount_step, 12)

    def place_limit(self, side: str, price: float, amount: float) -> str:
        order_id = f"sim-{self._next_id}"
        self._next_id += 1
        self.orders[order_id] = {"side": side, "price": price, "amount": amount}
        return order_id

    def cancel(self, order_id: str) -> None:
        self.orders.pop(order_id, None)

    def poll(self, price: float) -> List[OrderEvent]:
        events = []
        for order_id, o in list(self.orders.items()):
            hit = price <= o["price"] if o["side"] == "buy" else price >= o["price"]
            if not hit:
                continue
            cost = o["price"] * o["amount"]
            events.append(
                OrderEvent(
                    order_id=order_id,
                    status="filled",
                    side=o["side"],
                    price=o["price"],
                    filled=o["amount"],
                    cost=cost,
                    fee_base=o["amount"] * self.fee_rate if o["side"] == "buy" else 0.0,
                    fee_quote=cost * self.fee_rate if o["side"] == "sell" else 0.0,
                )
            )
            del self.orders[order_id]
        return events

    def market_sell(self, amount: float, ref_price: float) -> float:
        return amount * ref_price * (1 - self.fee_rate)


class CcxtBroker:
    """Order sungguhan. Semua order dibuat bot dicatat di `open_ids`."""

    def __init__(self, exchange: "ccxt.Exchange", symbol: str, fee_rate: float):
        self.ex = exchange
        self.symbol = symbol
        self.fee_rate = fee_rate
        self.ex.load_markets()
        self.market = self.ex.market(symbol)
        self.base = self.market["base"]
        self.quote = self.market["quote"]
        self.open_ids = set()

    # -- info pasar --

    def last_price(self) -> float:
        return float(self.ex.fetch_ticker(self.symbol)["last"])

    def round_amount(self, amount: float) -> float:
        min_amount = (self.market.get("limits", {}).get("amount") or {}).get("min") or 0
        if amount < min_amount:
            return 0.0
        return float(self.ex.amount_to_precision(self.symbol, amount))

    def min_cost(self) -> float:
        return (self.market.get("limits", {}).get("cost") or {}).get("min") or 0.0

    def free_balance(self, currency: str) -> float:
        return float(self.ex.fetch_balance().get("free", {}).get(currency) or 0.0)

    # -- order --

    def place_limit(self, side: str, price: float, amount: float) -> str:
        price = float(self.ex.price_to_precision(self.symbol, price))
        order = self.ex.create_order(self.symbol, "limit", side, amount, price)
        self.open_ids.add(order["id"])
        return order["id"]

    def cancel(self, order_id: str) -> None:
        try:
            self.ex.cancel_order(order_id, self.symbol)
        except ccxt.OrderNotFound:
            log.warning("Order %s tidak ditemukan saat dibatalkan (mungkin sudah terisi)", order_id)
        self.open_ids.discard(order_id)

    def poll(self, price: float) -> List[OrderEvent]:
        if not self.open_ids:
            return []
        still_open = {o["id"] for o in self.ex.fetch_open_orders(self.symbol)}
        events = []
        for order_id in list(self.open_ids - still_open):
            order = self.ex.fetch_order(order_id, self.symbol)
            if order["status"] == "open":
                continue
            self.open_ids.discard(order_id)
            events.append(self._to_event(order))
        return events

    def market_sell(self, amount: float, ref_price: float) -> float:
        amount = self.round_amount(min(amount, self.free_balance(self.base)))
        if amount <= 0:
            return 0.0
        order = self.ex.create_order(self.symbol, "market", "sell", amount)
        order = self.ex.fetch_order(order["id"], self.symbol)
        ev = self._to_event(order)
        return ev.cost - ev.fee_quote

    def _to_event(self, order: dict) -> OrderEvent:
        filled = float(order.get("filled") or 0.0)
        price = float(order.get("average") or order.get("price") or 0.0)
        cost = float(order.get("cost") or filled * price)
        side = order["side"]
        fee_base, fee_quote, known = 0.0, 0.0, False
        fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
        for fee in fees:
            if not fee or fee.get("cost") is None:
                continue
            known = True
            if fee.get("currency") == self.base:
                fee_base += float(fee["cost"])
            elif fee.get("currency") == self.quote:
                fee_quote += float(fee["cost"])
        if not known:
            # Fee tidak dilaporkan: perkirakan secara konservatif.
            if side == "buy":
                fee_base = filled * self.fee_rate
            else:
                fee_quote = cost * self.fee_rate
        return OrderEvent(
            order_id=order["id"],
            status="filled" if order["status"] == "closed" else "canceled",
            side=side,
            price=price,
            filled=filled,
            cost=cost,
            fee_base=fee_base,
            fee_quote=fee_quote,
        )
