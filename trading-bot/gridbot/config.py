from dataclasses import dataclass
from typing import List, Optional


@dataclass(frozen=True)
class GridConfig:
    """Parameter grid. Harga dalam quote (mis. USDT), investment dalam quote."""

    symbol: str
    lower: float
    upper: float
    grids: int
    investment: float
    fee_rate: float = 0.001
    stop_loss: Optional[float] = None

    def __post_init__(self):
        if self.lower <= 0:
            raise ValueError("lower harus > 0")
        if self.upper <= self.lower:
            raise ValueError("upper harus lebih besar dari lower")
        if self.grids < 2:
            raise ValueError("grids minimal 2")
        if self.investment <= 0:
            raise ValueError("investment harus > 0")
        if not 0 <= self.fee_rate < 0.01:
            raise ValueError("fee_rate harus antara 0 dan 0.01 (1%)")
        if self.stop_loss is not None and not 0 < self.stop_loss < self.lower:
            raise ValueError("stop_loss harus > 0 dan di bawah lower")
        if self.min_net_profit_per_grid <= 0:
            raise ValueError(
                "Jarak grid terlalu rapat: profit per grid habis dimakan fee. "
                "Kurangi jumlah grid atau lebarkan range."
            )

    @property
    def step(self) -> float:
        return (self.upper - self.lower) / self.grids

    @property
    def levels(self) -> List[float]:
        return [self.lower + i * self.step for i in range(self.grids + 1)]

    @property
    def quote_per_grid(self) -> float:
        return self.investment / self.grids

    @property
    def min_net_profit_per_grid(self) -> float:
        """Profit bersih (rasio) per siklus beli-jual di grid paling atas (yang paling kecil)."""
        ratio = self.upper / (self.upper - self.step)
        return ratio * (1 - self.fee_rate) ** 2 - 1
