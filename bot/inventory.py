"""Balance tracking, inventory skewing, and position limits."""

import logging
from .config import (
    TARGET_INVENTORY_XMR, SKEW_FACTOR, MAX_SKEW_BPS,
    MAX_POSITION_XMR, MAX_POSITION_USDC, MIN_USDC_TO_QUOTE, MIN_XMR_TO_QUOTE,
)

logger = logging.getLogger("mm.inventory")


class InventoryManager:
    def __init__(self):
        self.xmr_total = 0.0
        self.xmr_hold = 0.0
        self.usdc_total = 0.0
        self.usdc_hold = 0.0

    @property
    def xmr_available(self) -> float:
        return self.xmr_total - self.xmr_hold

    @property
    def usdc_available(self) -> float:
        return self.usdc_total - self.usdc_hold

    def update(self, balances: dict):
        self.xmr_total = balances.get("XMR1", 0.0)
        self.xmr_hold = balances.get("XMR1_hold", 0.0)
        self.usdc_total = balances.get("USDC", 0.0)
        self.usdc_hold = balances.get("USDC_hold", 0.0)

    def calculate_skew(self) -> float:
        """
        Returns skew in basis points.
        Positive = we want to SELL (lower asks, raise bids).
        Negative = we want to BUY (raise asks, lower bids).
        """
        imbalance = self.xmr_total - TARGET_INVENTORY_XMR
        skew = imbalance * SKEW_FACTOR
        return max(-MAX_SKEW_BPS, min(MAX_SKEW_BPS, skew))

    def can_buy(self) -> bool:
        return (
            self.xmr_total < MAX_POSITION_XMR
            and self.usdc_available >= MIN_USDC_TO_QUOTE
        )

    def can_sell(self) -> bool:
        return self.xmr_available >= MIN_XMR_TO_QUOTE

    def check_limits(self) -> tuple[bool, bool]:
        return self.can_buy(), self.can_sell()

    def to_dict(self, mid_price: float) -> dict:
        xmr_value = self.xmr_total * mid_price
        total_value = xmr_value + self.usdc_total
        return {
            "xmr_total": round(self.xmr_total, 4),
            "xmr_hold": round(self.xmr_hold, 4),
            "xmr_available": round(self.xmr_available, 4),
            "usdc_total": round(self.usdc_total, 2),
            "usdc_hold": round(self.usdc_hold, 2),
            "usdc_available": round(self.usdc_available, 2),
            "xmr_value_usdc": round(xmr_value, 2),
            "total_value_usdc": round(total_value, 2),
            "skew_bps": round(self.calculate_skew(), 2),
        }
