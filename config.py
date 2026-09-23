"""
Central configuration for the AI trading bot.
Edit the values below to change how the bot behaves.
API keys are loaded from environment variables (see .env.example) — never hardcode keys here.
"""

import os
from dotenv import load_dotenv

load_dotenv()

# --- API Keys (set these in a .env file, see .env.example) ---
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")
CRYPTOPANIC_API_KEY = os.getenv("CRYPTOPANIC_API_KEY", "")  # free tier: https://cryptopanic.com/developers/api/

# Free CoinGecko "Demo" API key — without one, requests share an overloaded public pool
# and get blocked constantly (429 errors). With one, you get your own private allowance.
# Get one free at https://www.coingecko.com/en/developer/dashboard (no credit card needed).
COINGECKO_API_KEY = os.getenv("COINGECKO_API_KEY", "")

# OANDA powers gold/silver/oil/forex prices (and later, trading). Free practice
# account: https://www.oanda.com/demo-account/tpa/personal_finance
OANDA_API_KEY = os.getenv("OANDA_API_KEY", "")
OANDA_ACCOUNT_ID = os.getenv("OANDA_ACCOUNT_ID", "")
OANDA_ENVIRONMENT = os.getenv("OANDA_ENVIRONMENT", "practice")  # "practice" or "live" — always start with practice

# --- Crypto exchange (only needed once you move past paper trading) ---
EXCHANGE_NAME = os.getenv("EXCHANGE_NAME", "okx")  # any ccxt-supported id, e.g. "binance", "bybit", "okx"
EXCHANGE_API_KEY = os.getenv("EXCHANGE_API_KEY", "")
EXCHANGE_API_SECRET = os.getenv("EXCHANGE_API_SECRET", "")
EXCHANGE_API_PASSPHRASE = os.getenv("EXCHANGE_API_PASSPHRASE", "")  # OKX requires this in addition to key+secret

# Hard dollar cap per single live trade, independent of MAX_POSITION_SIZE_PCT below.
# This exists purely so a first real deposit can't accidentally turn into one big live
# trade — raise it later in Railway's Variables tab once you trust the bot's behavior.
LIVE_MAX_TRADE_USDT = float(os.getenv("LIVE_MAX_TRADE_USDT", "50"))

# --- Instrument universe ---
# Every tradable symbol, with its market type and where its price/news come from.
#   type "crypto"    -> priced via CoinGecko, news via CryptoPanic
#   type "forex"     -> priced via OANDA
#   type "commodity" -> priced via OANDA (gold, silver, oil)
INSTRUMENTS = {
    # -- Crypto --
    "BTC":    {"type": "crypto", "display_name": "Bitcoin"},
    "ETH":    {"type": "crypto", "display_name": "Ethereum"},
    "SOL":    {"type": "crypto", "display_name": "Solana"},
    "BNB":    {"type": "crypto", "display_name": "BNB"},
    "XRP":    {"type": "crypto", "display_name": "XRP"},
    # -- Commodities --
    "XAU":    {"type": "commodity", "display_name": "Gold", "oanda_symbol": "XAU_USD"},
    "XAG":    {"type": "commodity", "display_name": "Silver", "oanda_symbol": "XAG_USD"},
    "WTICO":  {"type": "commodity", "display_name": "Crude Oil (WTI)", "oanda_symbol": "WTICO_USD"},
    # -- Forex (major pairs) --
    "EURUSD": {"type": "forex", "display_name": "EUR/USD", "oanda_symbol": "EUR_USD"},
    "GBPUSD": {"type": "forex", "display_name": "GBP/USD", "oanda_symbol": "GBP_USD"},
    "USDJPY": {"type": "forex", "display_name": "USD/JPY", "oanda_symbol": "USD_JPY"},
}

# Convenience: flat list of every symbol, and symbols grouped by type
WATCHED_COINS = [s for s, meta in INSTRUMENTS.items() if meta["type"] == "crypto"]
WATCHED_COMMODITIES = [s for s, meta in INSTRUMENTS.items() if meta["type"] == "commodity"]
WATCHED_FOREX = [s for s, meta in INSTRUMENTS.items() if meta["type"] == "forex"]
ALL_INSTRUMENTS = list(INSTRUMENTS.keys())

# --- Mode ---
# "paper"  = simulated trades, no real money, safe to run anytime
# "live"   = real trades on your connected accounts (DO NOT enable until paper trading has proven itself)
TRADING_MODE = os.getenv("TRADING_MODE", "paper")

# --- Risk management (applies in both paper and live mode) ---
STARTING_BALANCE_USDT = 1000.0       # paper trading starting balance
MAX_POSITION_SIZE_PCT = 0.10         # never risk more than 10% of balance on a single instrument
STOP_LOSS_PCT = 0.05                 # auto-sell a position if it drops 5% from entry
TAKE_PROFIT_PCT = 0.15               # auto-sell a position if it rises 15% from entry
MAX_DAILY_LOSS_PCT = 0.08            # halt all trading for the day if total losses exceed 8% of balance
MAX_TRADES_PER_DAY = 10              # hard cap regardless of how many signals the AI produces
MIN_CONFIDENCE_TO_TRADE = 0.65       # AI must be at least this confident (0-1) to act on a signal

# --- Decision loop timing ---
DECISION_INTERVAL_MINUTES = 30       # how often the bot checks news and reconsiders positions

# --- Paths ---
# Everything lives under DATA_DIR so a single mounted volume (see Railway
# "Attach volume" -> /app/data) persists all of it across deploys and restarts.
# Without a persisted volume, this folder resets every time new code is deployed.
DATA_DIR = os.path.join(os.path.dirname(__file__), "data")
LOGS_DIR = os.path.join(DATA_DIR, "logs")
DECISIONS_LOG = os.path.join(LOGS_DIR, "decisions.jsonl")
TRADES_LOG = os.path.join(LOGS_DIR, "trades.jsonl")
PORTFOLIO_FILE = os.path.join(DATA_DIR, "portfolio.json")
PORTFOLIO_HISTORY_LOG = os.path.join(DATA_DIR, "portfolio_history.jsonl")

# Live-trading uses its own separate files so switching TRADING_MODE back and forth
# never mixes real account data with the paper-trading simulation.
LIVE_PORTFOLIO_FILE = os.path.join(DATA_DIR, "live_portfolio.json")
LIVE_TRADES_LOG = os.path.join(LOGS_DIR, "live_trades.jsonl")
LIVE_PORTFOLIO_HISTORY_LOG = os.path.join(DATA_DIR, "live_portfolio_history.jsonl")
