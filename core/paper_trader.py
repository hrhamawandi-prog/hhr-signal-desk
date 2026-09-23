"""
Paper trading simulator.

Executes AI decisions against a simulated portfolio using real live prices, but with
fake money. Nothing in this file ever touches a real exchange account. This is what
you run first — for weeks, ideally — before ever considering config.TRADING_MODE = "live".
"""

import json
import os
from datetime import datetime, timezone

import config
import risk_engine


def load_portfolio() -> dict:
    if os.path.exists(config.PORTFOLIO_FILE):
        with open(config.PORTFOLIO_FILE) as f:
            return json.load(f)
    return {
        "cash_usdt": config.STARTING_BALANCE_USDT,
        "positions": {},  # coin -> {quantity, entry_price, entry_time}
        "trades_today": {},  # date -> count
        "created_at": datetime.now(timezone.utc).isoformat(),
    }


def save_portfolio(portfolio: dict) -> None:
    os.makedirs(config.DATA_DIR, exist_ok=True)
    with open(config.PORTFOLIO_FILE, "w") as f:
        json.dump(portfolio, f, indent=2)


def _log_trade(trade: dict) -> None:
    os.makedirs(config.LOGS_DIR, exist_ok=True)
    with open(config.TRADES_LOG, "a") as f:
        f.write(json.dumps(trade) + "\n")


def log_portfolio_snapshot(portfolio: dict, prices: dict) -> None:
    """Appends a timestamped {value, cash} snapshot so a dashboard can chart
    portfolio value over time. Called once per cycle, independent of whether
    any trade happened."""
    os.makedirs(config.DATA_DIR, exist_ok=True)
    snapshot = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "value": risk_engine.portfolio_value(portfolio, prices),
        "cash": portfolio.get("cash_usdt", 0.0),
    }
    with open(config.PORTFOLIO_HISTORY_LOG, "a") as f:
        f.write(json.dumps(snapshot) + "\n")


def _record_trade_count(portfolio: dict) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    portfolio.setdefault("trades_today", {})
    portfolio["trades_today"][today] = portfolio["trades_today"].get(today, 0) + 1


def execute_buy(coin: str, usdt_amount: float, price: float, portfolio: dict, reason: str) -> dict:
    quantity = usdt_amount / price
    portfolio["cash_usdt"] -= usdt_amount

    existing = portfolio["positions"].get(coin)
    if existing:
        # average into the existing position
        total_qty = existing["quantity"] + quantity
        avg_price = (
            existing["quantity"] * existing["entry_price"] + quantity * price
        ) / total_qty
        portfolio["positions"][coin] = {
            "quantity": total_qty,
            "entry_price": avg_price,
            "entry_time": existing["entry_time"],
        }
    else:
        portfolio["positions"][coin] = {
            "quantity": quantity,
            "entry_price": price,
            "entry_time": datetime.now(timezone.utc).isoformat(),
        }

    _record_trade_count(portfolio)
    trade = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coin": coin,
        "action": "buy",
        "quantity": quantity,
        "price": price,
        "usdt_amount": usdt_amount,
        "reason": reason,
        "mode": config.TRADING_MODE,
    }
    _log_trade(trade)
    return trade


def execute_sell(coin: str, portfolio: dict, price: float, reason: str) -> dict | None:
    position = portfolio["positions"].get(coin)
    if not position or position["quantity"] <= 0:
        return None

    quantity = position["quantity"]
    proceeds = quantity * price
    pnl = proceeds - (quantity * position["entry_price"])

    portfolio["cash_usdt"] += proceeds
    del portfolio["positions"][coin]

    _record_trade_count(portfolio)
    trade = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "coin": coin,
        "action": "sell",
        "quantity": quantity,
        "price": price,
        "usdt_amount": proceeds,
        "pnl_usdt": pnl,
        "reason": reason,
        "mode": config.TRADING_MODE,
    }
    _log_trade(trade)
    return trade


def process_decisions(decisions: list[dict], prices: dict, portfolio: dict) -> list[dict]:
    """
    Runs every AI decision through the risk engine, then executes whatever survives.
    Also independently checks every open position for stop-loss / take-profit triggers,
    regardless of what the AI said this cycle.
    """
    executed = []

    # 1. Hard exits first — these fire even without an AI signal
    for coin, position in list(portfolio["positions"].items()):
        price = prices.get(coin)
        if price is None:
            continue
        trigger = risk_engine.should_stop_loss_or_take_profit(coin, position, price)
        if trigger:
            trade = execute_sell(coin, portfolio, price, reason=trigger)
            if trade:
                executed.append(trade)

    # 2. AI-driven decisions, each checked against risk rules
    for decision in decisions:
        coin = decision["coin"]
        price = prices.get(coin)
        if price is None:
            continue

        try:
            validated = risk_engine.validate_trade(decision, portfolio)
        except risk_engine.RiskViolation as e:
            print(f"[paper_trader] Blocked: {e}")
            continue

        if validated["action"] == "buy":
            total_value = risk_engine.portfolio_value(portfolio, prices)
            proposed_usdt = total_value * config.MAX_POSITION_SIZE_PCT
            usdt_amount = risk_engine.check_position_size(coin, proposed_usdt, portfolio)
            usdt_amount = min(usdt_amount, portfolio["cash_usdt"])
            if usdt_amount > 10:  # ignore dust trades
                trade = execute_buy(coin, usdt_amount, price, portfolio, validated["reasoning"])
                executed.append(trade)

        elif validated["action"] == "sell" and coin in portfolio["positions"]:
            trade = execute_sell(coin, portfolio, price, reason=validated["reasoning"])
            if trade:
                executed.append(trade)

    save_portfolio(portfolio)
    return executed


def print_summary(portfolio: dict, prices: dict) -> None:
    total = risk_engine.portfolio_value(portfolio, prices)
    starting = config.STARTING_BALANCE_USDT
    pnl_pct = (total - starting) / starting * 100

    print(f"\n{'='*50}")
    print(f"Portfolio value: ${total:,.2f}  ({pnl_pct:+.2f}% since start)")
    print(f"Cash: ${portfolio['cash_usdt']:,.2f}")
    if portfolio["positions"]:
        print("Open positions:")
        for coin, pos in portfolio["positions"].items():
            current_price = prices.get(coin, pos["entry_price"])
            pos_pnl_pct = (current_price - pos["entry_price"]) / pos["entry_price"] * 100
            print(
                f"  {coin}: {pos['quantity']:.6f} @ entry ${pos['entry_price']:,.2f} "
                f"(now ${current_price:,.2f}, {pos_pnl_pct:+.2f}%)"
            )
    print(f"{'='*50}\n")
