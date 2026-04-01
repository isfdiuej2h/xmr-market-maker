# XMR1/USDC Market Maker Bot

Production market maker for XMR1/USDC on Hyperliquid DEX.

## Quick Start

```bash
# 1. Clone & setup
cd /opt/xmr1-market-maker
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt

# 2. Configure
cp .env.example .env
# Edit .env with your private key

# 3. Run
python -m bot.main
```

## Deploy on Ubuntu 24.04

```bash
# Copy service files
sudo cp xmr1-mm-bot.service /etc/systemd/system/
sudo cp xmr1-mm-api.service /etc/systemd/system/

# Create user & setup
sudo useradd -r -s /bin/bash -m mm
sudo cp -r . /opt/xmr1-market-maker
sudo chown -R mm:mm /opt/xmr1-market-maker

# Enable & start
sudo systemctl daemon-reload
sudo systemctl enable xmr1-mm-bot xmr1-mm-api
sudo systemctl start xmr1-mm-bot xmr1-mm-api

# View logs
journalctl -u xmr1-mm-bot -f
```

## Architecture

- `bot/` — Async Python market maker
- `api/` — FastAPI serving state.json + dashboard
- Dashboard — React app (separate) or HTML served by API

## Key Features

- Multi-exchange price feeds (Kraken, Binance, KuCoin)
- Post-only (ALO) orders for maker rebates
- Layered quoting with inventory skew
- Volatility-aware spread widening
- SQLite WAL logging
- Auto-restart with exponential backoff
- Watchdog deadlock detection
