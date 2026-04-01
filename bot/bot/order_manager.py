"""Place, cancel, modify orders on Hyperliquid."""

import asyncio
import concurrent.futures
import logging

from hyperliquid.info import Info
from hyperliquid.exchange import Exchange

from .config import (
    COIN_PAIR, COIN_FORMATS, PRICE_DECIMALS, SIZE_DECIMALS,
    round_price, round_size, validate_order, is_xmr1,
)

logger = logging.getLogger("mm.orders")

executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)


class OrderManager:
    def __init__(self, exchange: Exchange, info: Info, address: str):
        self.exchange = exchange
        self.info = info
        self.address = address

    async def _run(self, fn, *args, **kwargs):
        loop = asyncio.get_event_loop()
        return await loop.run_in_executor(executor, lambda: fn(*args, **kwargs))

    # ── Reading state ───────────────────────────────────────
    async def get_open_orders(self) -> list[dict]:
        orders = await self._run(self.info.open_orders, self.address)
        return [o for o in orders if is_xmr1(o.get("coin", ""))]

    async def get_balances(self) -> dict:
        state = await self._run(self.info.spot_user_state, self.address)
        result = {"XMR1": 0.0, "XMR1_hold": 0.0, "USDC": 0.0, "USDC_hold": 0.0}
        for b in state.get("balances", []):
            coin = b["coin"]
            if coin == "XMR1":
                result["XMR1"] = float(b["total"])
                result["XMR1_hold"] = float(b["hold"])
            elif coin == "USDC":
                result["USDC"] = float(b["total"])
                result["USDC_hold"] = float(b["hold"])
        return result

    async def get_fills(self) -> list[dict]:
        fills = await self._run(self.info.spot_user_fills, self.address)
        return [f for f in fills if is_xmr1(f.get("coin", ""))]

    async def get_l2_book(self) -> dict:
        return await self._run(self.info.l2_snapshot, COIN_PAIR)

    # ── Placing orders ──────────────────────────────────────
    async def place_order(self, is_buy: bool, size: float, price: float) -> dict | None:
        size = round_size(size)
        price = round_price(price)
        if not validate_order(price, size):
            logger.debug(f"Order too small: {size} @ {price}")
            return None
        try:
            result = await self._run(
                self.exchange.order,
                COIN_PAIR, is_buy, size, price,
                {"limit": {"tif": "Alo"}}, False
            )
            return result
        except Exception as e:
            await self._handle_error(e, "place_order")
            return None

    async def place_bulk_orders(self, orders: list[dict]) -> dict | None:
        formatted = []
        for o in orders:
            sz = round_size(o["sz"])
            px = round_price(o["limit_px"])
            if not validate_order(px, sz):
                continue
            formatted.append({
                "coin": COIN_PAIR,
                "is_buy": o["is_buy"],
                "sz": sz,
                "limit_px": px,
                "order_type": {"limit": {"tif": "Alo"}},
                "reduce_only": False,
            })
        if not formatted:
            return None
        try:
            return await self._run(self.exchange.bulk_orders, formatted)
        except Exception as e:
            await self._handle_error(e, "bulk_orders")
            return None

    # ── Cancel ──────────────────────────────────────────────
    async def cancel_order(self, oid: int) -> bool:
        try:
            await self._run(self.exchange.cancel, COIN_PAIR, int(oid))
            return True
        except Exception as e:
            await self._handle_error(e, f"cancel {oid}")
            return False

    async def cancel_all(self):
        orders = await self.get_open_orders()
        cancelled = 0
        for o in orders:
            if await self.cancel_order(int(o["oid"])):
                cancelled += 1
        logger.info(f"Cancelled {cancelled} stale orders")
        if cancelled > 0:
            await asyncio.sleep(1)

    # ── Modify ──────────────────────────────────────────────
    async def modify_orders(self, modifications: list[dict]) -> dict | None:
        formatted = []
        for m in modifications:
            formatted.append({
                "oid": int(m["oid"]),
                "order": {
                    "coin": COIN_PAIR,
                    "is_buy": m["is_buy"],
                    "sz": round_size(m["sz"]),
                    "limit_px": round_price(m["limit_px"]),
                    "order_type": {"limit": {"tif": "Alo"}},
                    "reduce_only": False,
                },
            })
        if not formatted:
            return None
        try:
            return await self._run(self.exchange.bulk_modify_orders_new, formatted)
        except Exception as e:
            await self._handle_error(e, "modify_orders")
            return None

    # ── Error handling ──────────────────────────────────────
    async def _handle_error(self, e: Exception, context: str):
        error = str(e).lower()
        if "insufficient balance" in error:
            logger.warning(f"{context}: insufficient balance")
        elif "rate limit" in error or "429" in error:
            logger.warning(f"{context}: rate limited, backing off")
            await asyncio.sleep(2)
        elif "order would cross" in error:
            logger.debug(f"{context}: ALO order would cross spread (normal)")
        elif "order not found" in error:
            logger.debug(f"{context}: order already filled/cancelled")
        elif "invalid price" in error or "invalid size" in error:
            logger.error(f"{context}: invalid price/size precision")
        else:
            logger.error(f"{context}: {e}")
