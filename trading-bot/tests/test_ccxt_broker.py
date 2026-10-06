"""CcxtBroker diuji dengan exchange palsu (tanpa jaringan)."""
import pytest

from gridbot.brokers import CcxtBroker
from gridbot.config import GridConfig
from gridbot.engine import SELLING, GridBot


class FakeExchange:
    def __init__(self):
        self.orders = {}
        self.n = 0
        self.price = 155.0
        self.free = {"SOL": 0.0, "USDT": 1000.0}

    def load_markets(self):
        pass

    def market(self, symbol):
        return {"base": "SOL", "quote": "USDT", "limits": {"amount": {"min": 0.01}, "cost": {"min": 1}}}

    def amount_to_precision(self, symbol, amount):
        return str(int(amount * 1000) / 1000)

    def price_to_precision(self, symbol, price):
        return f"{price:.2f}"

    def fetch_ticker(self, symbol):
        return {"last": self.price}

    def fetch_balance(self):
        return {"free": dict(self.free)}

    def create_order(self, symbol, type_, side, amount, price=None):
        self.n += 1
        oid = str(self.n)
        self.orders[oid] = {"id": oid, "side": side, "type": type_, "amount": amount, "price": price,
                            "status": "open", "filled": 0.0, "average": None, "cost": 0.0, "fee": None}
        if type_ == "market":
            self._fill(oid, self.price)
        return {"id": oid}

    def _fill(self, oid, price):
        o = self.orders[oid]
        o.update(status="closed", filled=o["amount"], average=price, cost=o["amount"] * price)
        o["fee"] = ({"cost": o["amount"] * 0.001, "currency": "SOL"} if o["side"] == "buy"
                    else {"cost": o["cost"] * 0.001, "currency": "USDT"})

    def cancel_order(self, oid, symbol):
        self.orders[oid]["status"] = "canceled"

    def fetch_open_orders(self, symbol):
        return [o for o in self.orders.values() if o["status"] == "open"]

    def fetch_order(self, oid, symbol):
        return dict(self.orders[oid])

    def move(self, price):
        self.price = price
        for oid, o in self.orders.items():
            if o["status"] != "open":
                continue
            if (o["side"] == "buy" and price <= o["price"]) or (o["side"] == "sell" and price >= o["price"]):
                self._fill(oid, o["price"])


def test_live_flow_with_fake_exchange():
    ex = FakeExchange()
    cfg = GridConfig(symbol="SOL/USDT", lower=100, upper=200, grids=10, investment=1000, stop_loss=90)
    broker = CcxtBroker(ex, cfg.symbol, cfg.fee_rate)
    bot = GridBot(cfg, broker)

    bot.step(broker.last_price())
    assert len(broker.open_ids) == 6

    ex.move(149)
    bot.step(broker.last_price())
    assert bot.slots[5].state == SELLING
    sell = ex.orders[bot.slots[5].order_id]
    assert sell["side"] == "sell" and sell["price"] == 160.0
    assert sell["amount"] == pytest.approx(0.665)  # 0.666 dikurangi fee 0.1% lalu dibulatkan ke bawah

    ex.move(161)
    bot.step(broker.last_price())
    assert bot.round_trips == 1 and bot.realized_profit > 0

    ex.free["SOL"] = 10.0
    ex.move(85)
    bot.step(broker.last_price())
    assert bot.stopped
    assert not ex.fetch_open_orders("SOL/USDT")
    last = ex.orders[str(ex.n)]
    assert last["type"] == "market" and last["side"] == "sell"
    assert last["amount"] == pytest.approx(sum(o["amount"] for o in ex.orders.values()
                                               if o["side"] == "sell" and o["status"] == "canceled"), abs=0.002)
