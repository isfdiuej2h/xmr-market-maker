"""Multi-exchange price aggregation with staleness detection."""

import time
import asyncio
import json
import logging
import aiohttp
import websockets

logger = logging.getLogger("mm.price_feed")


class PriceFeed:
    def __init__(self, staleness_threshold: float = 30.0):
        self.prices: dict[str, tuple[float, float]] = {}  # source -> (price, timestamp)
        self.staleness_threshold = staleness_threshold
        self._lock = asyncio.Lock()

    async def update(self, price: float, source: str):
        async with self._lock:
            self.prices[source] = (price, time.time())
            logger.debug(f"Price update from {source}: {price:.2f}")

    def get_mid_price(self) -> float | None:
        now = time.time()
        valid = []
        for source in ["kraken", "binance", "kucoin"]:
            if source in self.prices:
                price, ts = self.prices[source]
                if now - ts < self.staleness_threshold:
                    valid.append(price)
        if not valid:
            return None
        # Median for robustness
        valid.sort()
        return valid[len(valid) // 2]

    def get_all_feeds(self) -> list[dict]:
        now = time.time()
        result = []
        for source in ["kraken", "binance", "kucoin"]:
            if source in self.prices:
                price, ts = self.prices[source]
                age = now - ts
                result.append({
                    "source": source,
                    "price": price,
                    "age_seconds": round(age, 1),
                    "stale": age > self.staleness_threshold,
                })
            else:
                result.append({
                    "source": source,
                    "price": None,
                    "age_seconds": None,
                    "stale": True,
                })
        return result


async def kraken_ws_feed(feed: PriceFeed):
    """Kraken WebSocket price feed for XMR/USD."""
    uri = "wss://ws.kraken.com"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                await ws.send(json.dumps({
                    "event": "subscribe",
                    "pair": ["XMR/USD"],
                    "subscription": {"name": "ticker"}
                }))
                logger.info("Kraken WS connected")
                async for msg in ws:
                    data = json.loads(msg)
                    if isinstance(data, list) and len(data) >= 2:
                        ticker = data[1]
                        if isinstance(ticker, dict) and "c" in ticker:
                            price = float(ticker["c"][0])
                            await feed.update(price, "kraken")
        except Exception as e:
            logger.warning(f"Kraken WS error: {e}, reconnecting in 5s")
            await asyncio.sleep(5)


async def binance_ws_feed(feed: PriceFeed):
    """Binance WebSocket price feed for XMRUSDT."""
    uri = "wss://stream.binance.com:9443/ws/xmrusdt@ticker"
    while True:
        try:
            async with websockets.connect(uri) as ws:
                logger.info("Binance WS connected")
                async for msg in ws:
                    data = json.loads(msg)
                    if "c" in data:
                        price = float(data["c"])
                        await feed.update(price, "binance")
        except Exception as e:
            logger.warning(f"Binance WS error: {e}, reconnecting in 5s")
            await asyncio.sleep(5)


async def kucoin_rest_feed(feed: PriceFeed, interval: float = 5.0):
    """KuCoin REST fallback, polled every `interval` seconds."""
    url = "https://api.kucoin.com/api/v1/market/orderbook/level1?symbol=XMR-USDT"
    while True:
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url, timeout=aiohttp.ClientTimeout(total=10)) as resp:
                    data = await resp.json()
                    if data.get("code") == "200000":
                        price = float(data["data"]["price"])
                        await feed.update(price, "kucoin")
        except Exception as e:
            logger.warning(f"KuCoin REST error: {e}")
        await asyncio.sleep(interval)
