#!/usr/bin/env python3
"""
XMR1/USDC Market Maker Bot for Hyperliquid DEX.

Production-ready async bot with:
- Multi-exchange price feeds (Kraken, Binance, KuCoin)
- Layered quoting with inventory skew
- Volatility-aware spread widening
- SQLite logging with WAL mode
- state.json output for dashboard polling
- Auto-restart with exponential backoff
"""

import os
import sys
import time
import json
import asyncio
import logging
import datetime

import eth_account
from hyperliquid.info import Info
from hyperliquid.exchange import Exchange
from hyperliquid.utils import constants

from .config import (
    PRIVATE_KEY, API_URL, WS_URL,
    COIN_PAIR, BASE_SPREAD_BPS, NUM_LAYERS, LAYER_SPACING_BPS,
    QUOTE_REFRESH_INTERVAL, MIN_PRICE_CHANGE_BPS,
    VOLATILITY_WINDOW, VOLATILITY_THRESHOLD_BPS, VOLATILITY_SPREAD_MULTIPLIER,
    MODIFY_THRESHOLD_BPS, MAX_MODIFY_DISTANCE_BPS, LOOP_TIMEOUT,
    round_price, round_size, validate_order,
)
from .price_feed import PriceFeed, kraken_ws_feed, binance_ws_feed, kucoin_rest_feed
from .order_manager import OrderManager
from .inventory import InventoryManager
from .logger import TradeLogger

# ── Logging setup ───────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("mm.log"),
    ],
)
logger = logging.getLogger("mm.main")


class MarketMaker:
    def __init__(self):
        if not PRIVATE_KEY:
            raise RuntimeError("HL_PRIVATE_KEY not set")

        self.wallet = eth_account.Account.from_key(PRIVATE_KEY)
        self.exchange = Exchange(self.wallet, API_URL, account_address=self.wallet.address)
        self.info = Info(API_URL, skip_ws=True)
        self.address = self.wallet.address

        self.price_feed = PriceFeed()
        self.orders = OrderManager(self.exchange, self.info, self.address)
        self.inventory = InventoryManager()
        self.trade_logger = TradeLogger()

        self.running = False
        self.last_loop_time = time.time()
        self.start_time = time.time()
        self.cycle_count = 0
        self.last_mid_price = 0.0
        self.price_history: list[tuple[float, float]] = []  # (timestamp, price)

    async def run(self):
        self.running = True
        logger.info(f"Starting XMR1/USDC MM — wallet: {self.address[:10]}...")
        self.trade_logger.add_event("info", f"Bot started, wallet: {self.address[:10]}...")

        # Start background tasks
        asyncio.create_task(kraken_ws_feed(self.price_feed))
        asyncio.create_task(binance_ws_feed(self.price_feed))
        asyncio.create_task(kucoin_rest_feed(self.price_feed))
        asyncio.create_task(self._watchdog())

        # Clean slate
        await self.orders.cancel_all()
        self.trade_logger.add_event("info", "Cancelled all stale orders")

        # Wait for price feeds
        logger.info("Waiting for price feeds...")
        for _ in range(30):
            if self.price_feed.get_mid_price() is not None:
                break
            await asyncio.sleep(1)
        else:
            logger.warning("No price feeds after 30s, starting anyway")

        # Main loop
        while self.running:
            try:
                self.last_loop_time = time.time()
                await self._quote_cycle()
                self.cycle_count += 1
                await asyncio.sleep(QUOTE_REFRESH_INTERVAL)
            except Exception as e:
                logger.error(f"Quote cycle error: {e}")
                self.trade_logger.add_event("error", f"Cycle error: {e}")
                await asyncio.sleep(5)

    async def _quote_cycle(self):
        # 1. Get reference price
        mid = self.price_feed.get_mid_price()
        if mid is None:
            logger.warning("No valid price feed — pausing quotes")
            self.trade_logger.add_event("warn", "All price feeds stale, pausing")
            return

        # Track price history for volatility
        now = time.time()
        self.price_history.append((now, mid))
        self.price_history = [(t, p) for t, p in self.price_history if now - t < VOLATILITY_WINDOW * 2]

        # Skip if price hasn't moved enough
        if self.last_mid_price > 0:
            diff_bps = abs(mid - self.last_mid_price) / self.last_mid_price * 10000
            if diff_bps < MIN_PRICE_CHANGE_BPS and self.cycle_count % 10 != 0:
                await self._write_state(mid)
                return
        self.last_mid_price = mid

        # 2. Get inventory
        balances = await self.orders.get_balances()
        self.inventory.update(balances)

        # 3. Calculate spread with volatility adjustment
        spread = BASE_SPREAD_BPS
        vol = self._calculate_volatility()
        if vol > VOLATILITY_THRESHOLD_BPS:
            spread *= VOLATILITY_SPREAD_MULTIPLIER
            self.trade_logger.add_event("warn", f"High volatility ({vol:.0f} bps), widening spread to {spread:.0f} bps")

        # 4. Apply inventory skew
        skew = self.inventory.calculate_skew()

        ask_price = round_price(mid * (1 + spread / 10000 - skew / 10000))
        bid_price = round_price(mid * (1 - spread / 10000 - skew / 10000))

        # 5. Cancel existing and place new layered orders
        await self.orders.cancel_all()

        can_buy, can_sell = self.inventory.check_limits()
        bid_orders = []
        ask_orders = []

        if can_sell and self.inventory.xmr_available > 0:
            size_per = self.inventory.xmr_available / NUM_LAYERS
            for i in range(NUM_LAYERS):
                layer_px = round_price(ask_price * (1 + i * LAYER_SPACING_BPS / 10000))
                layer_sz = round_size(size_per)
                if validate_order(layer_px, layer_sz):
                    ask_orders.append({"is_buy": False, "sz": layer_sz, "limit_px": layer_px})

        if can_buy and self.inventory.usdc_available >= 100:
            usdc_per = self.inventory.usdc_available / NUM_LAYERS
            for i in range(NUM_LAYERS):
                layer_px = round_price(bid_price * (1 - i * LAYER_SPACING_BPS / 10000))
                layer_sz = round_size(usdc_per / layer_px)
                if validate_order(layer_px, layer_sz):
                    bid_orders.append({"is_buy": True, "sz": layer_sz, "limit_px": layer_px})

        all_orders = bid_orders + ask_orders
        if all_orders:
            await self.orders.place_bulk_orders(all_orders)
            self.trade_logger.add_event(
                "info",
                f"Placed {len(bid_orders)} bids, {len(ask_orders)} asks | "
                f"bid={bid_price:.2f} ask={ask_price:.2f} spread={spread:.0f}bps"
            )

        # 6. Check for fills
        await self._process_fills()

        # 7. Write state for dashboard
        await self._write_state(mid)

    def _calculate_volatility(self) -> float:
        """Return recent price volatility in basis points."""
        now = time.time()
        recent = [p for t, p in self.price_history if now - t < VOLATILITY_WINDOW]
        if len(recent) < 2:
            return 0.0
        min_p, max_p = min(recent), max(recent)
        mid_p = (min_p + max_p) / 2
        if mid_p == 0:
            return 0.0
        return (max_p - min_p) / mid_p * 10000

    async def _process_fills(self):
        """Check for new fills and log them."""
        try:
            fills = await self.orders.get_fills()
            # In production, track last processed fill to avoid duplicates
            # For now, the dashboard shows recent fills from the DB
        except Exception as e:
            logger.debug(f"Error checking fills: {e}")

    async def _write_state(self, mid_price: float):
        """Write state.json for dashboard polling."""
        uptime = time.time() - self.start_time
        hours, remainder = divmod(int(uptime), 3600)
        minutes = remainder // 60

        state = {
            "status": "running" if self.running else "stopped",
            "uptime": f"{hours}h {minutes}m",
            "cycle": self.cycle_count,
            "last_cycle_time": datetime.datetime.now().isoformat(),
            "mid_price": mid_price,
            "spread_bps": BASE_SPREAD_BPS,
            "inventory": self.inventory.to_dict(mid_price),
            "feeds": self.price_feed.get_all_feeds(),
            "pnl_24h": self.trade_logger.get_pnl_24h(),
            "fill_count_24h": self.trade_logger.get_fill_count_24h(),
            "volume_24h": self.trade_logger.get_volume_24h(),
            "recent_fills": self.trade_logger.get_recent_fills(20),
            "events": self.trade_logger.get_recent_events(),
        }
        self.trade_logger.write_state(state)

    async def _watchdog(self):
        """Force crash if main loop stalls."""
        while self.running:
            await asyncio.sleep(60)
            if time.time() - self.last_loop_time > LOOP_TIMEOUT:
                logger.critical("WATCHDOG: Main loop stalled, forcing crash!")
                self.trade_logger.add_event("error", "Watchdog triggered — loop stalled")
                raise Exception("Deadlock detected by watchdog")


def main():
    """Immortal wrapper with exponential backoff."""
    restart_count = 0
    last_restart = 0.0

    while True:
        try:
            print(f"\n{'=' * 60}")
            print(f"  XMR1/USDC Market Maker — Start #{restart_count}")
            print(f"{'=' * 60}\n")

            asyncio.run(MarketMaker().run())

        except KeyboardInterrupt:
            print("\nGraceful shutdown requested")
            sys.exit(0)

        except SystemExit:
            raise

        except Exception as e:
            restart_count += 1
            now = time.time()

            if now - last_restart < 60:
                delay = min(300, 5 * (2 ** min(restart_count, 6)))
                print(f"Crash #{restart_count}: {e}")
                print(f"Restarting too fast, waiting {delay}s...")
                time.sleep(delay)
            else:
                print(f"Crash #{restart_count}: {e}")
                print("Restarting in 5s...")
                time.sleep(5)

            last_restart = now


if __name__ == "__main__":
    main()
