"""
Live trading — places REAL orders on your real OKX account, using real money.

This mirrors paper_trader.py's interface exactly (load_portfolio, process_decisions,
print_summary, log_portfolio_snapshot) so main.py can swap between the two based on
config.TRADING_MODE without any other code changing.

Safety notes:
  - The API key this uses must be Trade-only (no Withdraw/Earn/Loan/Transfer) — this
    code never calls any withdrawal or transfer endpoint, and never could, because the
    key itself doesn't have that permission on OKX's side.
  - Only crypto instruments are traded live (via OKX spot). Forex/commodities are
    skipped in live mode — OANDA live execution isn't wired up.
  - Every trade still passes through risk_engine's checks (confidence, position size,
    daily loss limit, max trades/day, stop-loss/take-profit) exactly like paper trading.
  - LIVE_MAX_TRADE_USDT (config.py) hard-caps every single trade in dollar terms, on
    top of the normal % based position sizing, so a big deposit doesn't turn into one
    big first trade.
"""

import json
import os
from datetime import datetime, timezone

import ccxt

import config
import risk_engine


def get_exchange():
    if not (config.EXCHANGE_API_KEY and config.EXCHANGE_API_SECRET and config.EXCHANGE_API_PASSPHRASE):
        raise RuntimeError(
            "Live trading needs EXCHANGE_API_KEY, EXCHANGE_API_SECRET, and "
            "EXCHANGE_API_PASSPHRASE all set (Railway → Variables)."
        )
    exchange_class = getattr(ccxt, config.EXCHANGE_NAME)
    exchange = exchange_class(
        {
            "apiKey": config.EXCHANGE_API_KEY,
            "secret": config.EXCHANGE_API_SECRET,
            "password": config.EXCHANGE_API_PASSPHRASE,
            "enableRateLimit": True,
        }
    )
    # Lets ccxt's create_market_buy_order() take an amount in USDT (quote currency)
    # instead of requiring the coin quantity — much simpler and matches how we size
    # trades everywhere else in this bot (as a USDT amount).
    exchange.options["createMarketBuyOrderRequiresPrice"] = False
    return exchange


def _fetch_account_snapshot(exchange) -> dict:
    balance = exchange.fetch_balance()
    free_usdt = (balance.get("USDT") or {}).get("free") or 0.0
    coin_balances = {}
    for coin in config.WATCHED_COINS:
        amt = (balance.get(coin) or {}).get("free") or 0.0
        if amt and amt > 0:
            coin_balances[coin] = amt
    return {"cash_usdt": free_usdt, "coin_balances": coin_balances}


def load_portfolio() -> dict:
    if os.path.exists(config.LIVE_PORTFOLIO_FILE):
        with open(config.LIVE_PORTFOLIO_FILE) as f:
            return json.load(f)

    # First time running live — pull the real starting point from the exchange
    # instead of assuming an empty account.
    exchange = get_exchange()
    snapshot = _fetch_account_snapshot(exchange)

    portfolio = {
        "cash_usdt": snapshot["cash_usdt"],
        "positions": {},
        "trades_today": {},
        "starting_value": None,  # filled in once we have live prices, see _ensure_starting_value
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    for coin, qty in snapshot["coin_balances"].items():
        # Any coins already sitting in the account before the bot started have an
        # unknown cost basis. entry_price is filled in from the first live price we
        # see (below), so stop-loss/take-profit measure from "no P&L yet" rather
        # than a guess.
        portfolio["positions"][coin] = {
            "quantity": qty,
            "entry_price": None,
            "entry_time": datetime.now(timezone.utc).isoformat(),
        }

    save_portfolio(portfolio)
    return portfolio


def save_portfolio(portfolio: dict) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.LIVE_PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


def _log_trade(trade: dict) -> None:
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(config.LIVE_TRADES_LOG, "a") as f:
        f.write(json.dumps(trade) + "\n")


def log_portfolio_snapshot(portfolio: dict, prices: dict) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "value": risk_engine.portfolio_value(portfolio, prices),
        "cash": portfolio.get("cash_usdt", 0.0),
    }
    with open(config.LIVE_PORTFOLIO_HISTORY_LOG, "a") as f:
        f.write(json.dumps(snapshot) + "\n")


def _record_trade_count(portfolio: dict) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    portfolio.setdefault("trades_today", {})
    portfolio["trades_today"][today] = portfolio["trades_today"].get(today, 0) + 1


def _ensure_starting_value(portfolio: dict, prices: dict) -> bool:
    """Fills in any unknown entry prices and the portfolio's starting_value the
    first time real prices are available. Returns True if it changed anything
    (so the caller knows to save)."""
    changed = False
    for coin, pos in portfolio["positions"].items():
        if pos.get("entry_price") is None and prices.get(coin):
            pos["entry_price"] = prices[coin]
            changed = True
    if portfolio.get("starting_value") is None:
        # Only finalize once every held position has a known price, so the
        # starting point is accurate rather than partially-priced.
        if all(pos.get("entry_price") is not None for pos in portfolio["positions"].values()):
            portfolio["starting_value"] = risk_engine.portfolio_value(portfolio, prices)
            changed = True
    return changed


def execute_buy(coin: str, usdt_amount: float, price: float, portfolio: dict, reason: str, exchange) -> dict | None:
    symbol = f"{coin}/USDT"
    usdt_amount = min(usdt_amount, config.LIVE_MAX_TRADE_USDT)
    if usdt_amount < 5:  # avoid dust trades / below-minimum-notional rejections
        return None

    try:
        order = exchange.create_market_buy_order(symbol, usdt_amount, params={"tgtCcy": "quote_ccy"})
    except Exception as e:
        print(f"[live_trader] BUY {coin} failed: {e}")
        return None

    filled_qty = order.get("filled") or 0.0
    avg_price = order.get("average") or price
    spent = order.get("cost") or usdt_amount
    if not filled_qty:
        print(f"[live_trader] BUY {coin} order placed (id {order.get('id')}) but no fill info yet — will pick it up next cycle.")
        return None

    portfolio["cash_usdt"] -= spent
    existing = portfolio["positions"].get(coin)
    if existing and existing.get("entry_price") is not None:
        total_qty = existing["quantity"] + filled_qty
        avg = (existing["quantity"] * existing["entry_price"] + filled_qty * avg_price) / total_qty
        portfolio["positions"][coin] = {"quantity": total_qty, "entry_price": avg, "entry_time": existing["entry_time"]}
    else:
        portfolio["positions"][coin] = {
            "quantity": filled_qty,
            "entry_price": avg_price,
            "entry_time": datetime.now(timezone.utc).isoformat(),
        }

    _record_trade_count(portfolio)
    trade = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coin": coin,
        "action": "buy",
        "quantity": filled_qty,
        "price": avg_price,
        "usdt_amount": spent,
        "reason": reason,
        "mode": "live",
        "order_id": order.get("id"),
    }
    _log_trade(trade)
    return trade


def execute_sell(coin: str, portfolio: dict, price: float, reason: str, exchange) -> dict | None:
    position = portfolio["positions"].get(coin)
    if not position or position.get("quantity", 0) <= 0:
        return None

    symbol = f"{coin}/USDT"
    try:
        amount = float(exchange.amount_to_precision(symbol, position["quantity"]))
        if amount <= 0:
            return None
        order = exchange.create_market_sell_order(symbol, amount)
    except Exception as e:
        print(f"[live_trader] SELL {coin} failed: {e}")
        return None

    filled_qty = order.get("filled") or amount
    avg_price = order.get("average") or price
    proceeds = order.get("cost") or (filled_qty * avg_price)
    entry_price = position.get("entry_price") or avg_price
    pnl = proceeds - (filled_qty * entry_price)

    portfolio["cash_usdt"] += proceeds
    remaining = position["quantity"] - filled_qty
    if remaining > 1e-8:
        position["quantity"] = remaining
    else:
        del portfolio["positions"][coin]

    _record_trade_count(portfolio)
    trade = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coin": coin,
        "action": "sell",
        "quantity": filled_qty,
        "price": avg_price,
        "usdt_amount": proceeds,
        "pnl_usdt": pnl,
        "reason": reason,
        "mode": "live",
        "order_id": order.get("id"),
    }
    _log_trade(trade)
    return trade


def process_decisions(decisions: list[dict], prices: dict, portfolio: dict) -> list[dict]:
    exchange = get_exchange()
    if _ensure_starting_value(portfolio, prices):
        save_portfolio(portfolio)

    starting_balance = portfolio.get("starting_value")
    executed = []

    # 1. Hard exits first, same as paper trading — fire even without a fresh AI signal.
    for coin, position in list(portfolio["positions"].items()):
        price = prices.get(coin)
        if price is None or position.get("entry_price") is None:
            continue
        trigger = risk_engine.should_stop_loss_or_take_profit(coin, position, price)
        if trigger:
            trade = execute_sell(coin, portfolio, price, reason=trigger, exchange=exchange)
            if trade:
                executed.append(trade)

    # 2. AI-driven decisions, each checked against the same risk rules as paper trading.
    for decision in decisions:
        coin = decision["coin"]
        if config.INSTRUMENTS.get(coin, {}).get("type") != "crypto":
            # Live execution is only wired up for OKX crypto pairs so far.
            continue
        price = prices.get(coin)
        if price is None:
            continue

        try:
            validated = risk_engine.validate_trade(decision, portfolio, starting_balance=starting_balance)
        except risk_engine.RiskViolation as e:
            print(f"[live_trader] Blocked: {e}")
            continue

        if validated["action"] == "buy":
            total_value = risk_engine.portfolio_value(portfolio, prices)
            proposed_usdt = total_value * config.MAX_POSITION_SIZE_PCT
            usdt_amount = risk_engine.check_position_size(coin, proposed_usdt, portfolio)
            usdt_amount = min(usdt_amount, portfolio["cash_usdt"], config.LIVE_MAX_TRADE_USDT)
            if usdt_amount > 5:
                trade = execute_buy(coin, usdt_amount, price, portfolio, validated["reasoning"], exchange)
                if trade:
                    executed.append(trade)

        elif validated["action"] == "sell" and coin in portfolio["positions"]:
            trade = execute_sell(coin, portfolio, price, reason=validated["reasoning"], exchange=exchange)
            if trade:
                executed.append(trade)

    save_portfolio(portfolio)
    return executed


def print_summary(portfolio: dict, prices: dict) -> None:
    total = risk_engine.portfolio_value(portfolio, prices)
    starting = portfolio.get("starting_value") or total
    pnl_pct = (total - starting) / starting * 100 if starting else 0.0

    print(f"\n{'='*50}")
    print(f"[LIVE] Account value: ${total:,.2f}  ({pnl_pct:+.2f}% since the bot started managing it)")
    print(f"Cash (USDT): ${portfolio['cash_usdt']:,.2f}")
    if portfolio["positions"]:
        print("Open positions:")
        for coin, pos in portfolio["positions"].items():
            entry = pos.get("entry_price") or prices.get(coin, 0)
            current_price = prices.get(coin, entry)
            pos_pnl_pct = (current_price - entry) / entry * 100 if entry else 0.0
            print(
                f"  {coin}: {pos['quantity']:.6f} @ entry ${entry:,.2f} "
                f"(now ${current_price:,.2f}, {pos_pnl_pct:+.2f}%)"
            )
    print(f"{'='*50}\n")
