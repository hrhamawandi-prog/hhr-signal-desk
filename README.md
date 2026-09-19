# HHR Signal Desk — AI Multi-Asset Trading Bot (Paper Trading)

This is step 1 of your AI trading bot: an AI decision pipeline that reads news, decides
buy/sell/hold across **crypto, gold, silver, oil, and forex**, and trades a **simulated**
portfolio with real live prices. No real money is touched by anything in this project
yet — that's intentional.

## ⚠️ Before you open this on your phone

This is code that needs to **run on a computer** (Windows/Mac/Linux) with Python
installed — it runs continuously, checking the market on a timer. An iPhone/iPad can't
run it. You'll need a laptop or PC to actually operate this. To unzip on iOS in the
meantime: open the **Files** app, find `ai-trader.zip`, and tap it — it extracts
automatically, no extra app needed. But you'll still want to move it to a computer to
actually run it.

## How it works

```
news_fetcher.py  →  ai_decision.py  →  risk_engine.py  →  paper_trader.py
(get headlines)     (Claude decides)   (checks limits)    (simulates trade)
```

Every cycle:
1. Fetches recent news — coin-specific for crypto, general market/macro news for
   gold, silver, oil, and forex (there's no free coin-style filtered news API for those
   at retail level, so the AI reads broad financial headlines, same as a human trader scanning a feed)
2. Fetches current live prices for every instrument (crypto via CoinGecko, everything
   else via OANDA)
3. Sends all of it — plus the current time, since trading-session liquidity matters for
   forex/commodities — to Claude, which returns a decision per instrument (buy/sell/hold
   + confidence + reasoning)
4. Every decision is checked against hard-coded risk rules — position size caps, daily
   loss limit, trade count limit, minimum confidence. **The AI can suggest anything; the
   risk engine decides what's actually allowed to happen.**
5. Approved trades are simulated against your paper portfolio (starts at $1000 fake USD)
6. Every decision and every trade is logged to `logs/decisions.jsonl` and `logs/trades.jsonl`
   so you can review exactly what the AI was thinking, later, unedited.

Open positions are also checked every cycle for stop-loss / take-profit triggers,
independent of what the AI says that cycle — these are hard exits.

## About deposits and withdrawals

This bot **never handles moving your money in or out** — and that's deliberate, not a
missing feature. Broker/exchange APIs (OANDA, OKX, and every legitimate one) restrict
API keys from withdrawing funds unless you explicitly enable a separate, dangerous
permission. If this bot's API key could withdraw funds, a bug or a leaked key could drain
your account straight out. The correct and standard way this works: **you deposit and
withdraw directly through OANDA's / OKX's own official app or website**, protected by
their own 2FA and security. This bot's API key only ever gets *trade* permission — it can
buy/sell within whatever balance is already sitting in your account, and nothing more.
Once we build the dashboard, it'll be able to *display* your balance, but the money
itself always moves through the broker's own systems.

## Setup

**1. Install Python 3.10+** if you don't have it (check with `python3 --version`).

**2. Install dependencies:**
```bash
pip install -r requirements.txt
```

**3. Get your API keys:**
- **Anthropic API key** (required) — sign up at https://console.anthropic.com/, create a key.
  This costs a small amount per request (far less than a cent per cycle at these prompt sizes).
- **CryptoPanic API key** (optional but recommended) — free tier at
  https://cryptopanic.com/developers/api/. Without it, the bot still works using general
  crypto RSS feeds, just less coin-specific.
- **OANDA practice account** (needed for gold/silver/oil/forex) — free demo signup at
  https://www.oanda.com/demo-account/tpa/personal_finance. After signing up, find your
  API key under "Manage API Access" and your Account ID on your dashboard (looks like
  `101-001-12345678-001`). Without this, the bot still runs fine on crypto alone.

**4. Configure:**
```bash
cp .env.example .env
```
Open `.env` and paste in your keys. Leave `EXCHANGE_*` blank for now — you don't need
them for paper trading.

**5. Adjust settings if you want:**
Open `config.py` — you can change which instruments are watched (`INSTRUMENTS`), risk
limits, starting balance, and how often it runs.

## Running it

Run one decision cycle:
```bash
python main.py
```

Run continuously (checks news and re-evaluates every `DECISION_INTERVAL_MINUTES`, default 30):
```bash
python main.py --loop
```

Each run prints what the AI decided, what actually got executed (after risk checks),
and a portfolio summary. Your portfolio state persists in `data/portfolio.json` between
runs, so you can stop and restart without losing progress.

## What to do with it

**Run it for a few weeks before touching real money.** Watch the `logs/decisions.jsonl`
file — read the AI's actual reasoning, not just whether it made money. A bot that gets
lucky with bad reasoning is more dangerous than one that's honestly uncertain. Things
to evaluate before ever going live:

- Does the win rate hold up over dozens of trades, not just a handful?
- Does it survive a bad week without blowing through the daily loss limit constantly?
- Does the reasoning in the logs actually make sense, or is it hallucinating connections
  between unrelated news and price moves?
- Does each asset class actually perform differently? (gold/oil/forex behave nothing
  like crypto — don't assume a strategy that works for one works for all of them)
- What's the simulated performance vs. just holding the assets the whole time? (a bot
  that underperforms buy-and-hold isn't worth the complexity or fees)

## What's NOT in this starter project yet

- **Live trading** — connecting to real accounts (OKX for crypto, OANDA for
  forex/commodities) to place real orders. `TRADING_MODE=live` is stubbed but not
  implemented — main.py will refuse to place real trades even if you set it, until this
  is built. We'll add this together once paper trading has a real track record.
- **A web dashboard** — right now everything is terminal output + log files. Once the
  trading logic is proven, we'll build the website on top of this as a monitoring/control
  layer — including a balance display, but never a way to move money itself (see above).
- **Backtesting against historical data** — worth adding before going live, so you can
  test the strategy against past market conditions, not just going-forward paper trading.

## Project structure

```
ai-trader/
├── config.py              # all settings — instruments, risk limits, mode
├── main.py                # entry point — runs the decision cycle
├── requirements.txt
├── .env.example            # copy to .env and fill in your keys
├── core/
│   ├── news_fetcher.py     # pulls crypto + macro/commodity news
│   ├── price_fetcher.py    # pulls live prices (CoinGecko + OANDA)
│   ├── ai_decision.py      # sends context to Claude, gets decisions
│   ├── risk_engine.py      # hard rules — the AI never bypasses these
│   └── paper_trader.py     # simulates trades, tracks fake portfolio
├── data/
│   └── portfolio.json      # current simulated portfolio (created on first run)
└── logs/
    ├── decisions.jsonl     # every AI decision, ever — for auditing
    └── trades.jsonl        # every executed trade, ever
```
