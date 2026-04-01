"""Strategy parameters, token specs, and environment configuration."""

import os
from dotenv import load_dotenv

load_dotenv()

# ── Authentication ──────────────────────────────────────────
PRIVATE_KEY = os.environ.get("HL_PRIVATE_KEY", "")

# ── API Endpoints ───────────────────────────────────────────
# Use QuickNode for production (higher rate limits, lower latency)
QUICKNODE_URL = os.environ.get("QUICKNODE_URL", "")
QUICKNODE_WS = os.environ.get("QUICKNODE_WS", "")
USE_QUICKNODE = bool(QUICKNODE_URL)

# Fallback to public endpoints
from hyperliquid.utils import constants
API_URL = QUICKNODE_URL if USE_QUICKNODE else constants.MAINNET_API_URL
WS_URL = QUICKNODE_WS if USE_QUICKNODE else "wss://api.hyperliquid.xyz/ws"

# ── XMR1/USDC Token Specs ──────────────────────────────────
COIN_PAIR = "XMR1/USDC"           # For order placement
COIN_QUERY_ID = "@260"            # For order queries / fills
COIN_BALANCE = "XMR1"             # For balance queries
COIN_FORMATS = ["XMR1", "XMR1/USDC", "@260"]

PRICE_DECIMALS = 2
SIZE_DECIMALS = 2
MIN_ORDER_USDC = 10

MAKER_FEE_BPS = 1      # 0.01%
TAKER_FEE_BPS = 3.5    # 0.035%

# ── Strategy Parameters ────────────────────────────────────
BASE_SPREAD_BPS = 50              # Base spread in basis points
NUM_LAYERS = 5                    # Orders per side
LAYER_SPACING_BPS = 5             # Spacing between layers
QUOTE_REFRESH_INTERVAL = 3.0      # Seconds between quote cycles
MIN_PRICE_CHANGE_BPS = 10         # Don't refresh if price moved < this

# Inventory management
TARGET_INVENTORY_XMR = 0          # Neutral target
SKEW_FACTOR = 0.5                 # bps per XMR of imbalance
MAX_SKEW_BPS = 50                 # Cap skew
MAX_POSITION_XMR = 100            # Max XMR1 to hold
MAX_POSITION_USDC = 200000        # Max USDC to deploy
MIN_USDC_TO_QUOTE = 100           # Minimum USDC to place bids
MIN_XMR_TO_QUOTE = 0.1            # Minimum XMR to place asks

# Volatility detection
VOLATILITY_WINDOW = 60            # Seconds to measure volatility
VOLATILITY_THRESHOLD_BPS = 100    # Widen spread if vol > this
VOLATILITY_SPREAD_MULTIPLIER = 2.0

# Order modification
MODIFY_THRESHOLD_BPS = 5          # Modify if price diff > this
MAX_MODIFY_DISTANCE_BPS = 200     # Cancel+replace if diff > this

# Resilience
LOOP_TIMEOUT = 300                # Watchdog timeout (seconds)
PRICE_STALENESS_THRESHOLD = 30    # Stop quoting if feeds older than this (seconds)

# Logging
DB_PATH = "mm_trades.db"
STATE_PATH = "state.json"
LOG_PATH = "mm.log"

# ── Helpers ─────────────────────────────────────────────────
def round_price(p: float) -> float:
    return round(p, PRICE_DECIMALS)

def round_size(s: float) -> float:
    return round(s, SIZE_DECIMALS)

def validate_order(price: float, size: float) -> bool:
    return round_price(price) * round_size(size) >= MIN_ORDER_USDC

def is_xmr1(coin: str) -> bool:
    return coin in COIN_FORMATS
