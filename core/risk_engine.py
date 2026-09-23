"""Deterministic limits, applied independently of model instructions."""
import math
from datetime import datetime, timezone
import config


class RiskViolation(Exception):
    pass


def number(value, *, positive=False):
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise RiskViolation("Expected a finite number")
    if not math.isfinite(value) or value < 0 or (positive and value == 0):
        raise RiskViolation("Invalid numeric value")
    return float(value)


def validate_decision(decision):
    if not isinstance(decision, dict):
        raise RiskViolation("Decision must be an object")
    if decision.get("coin") not in config.INSTRUMENTS:
        raise RiskViolation("Unknown instrument")
    if decision.get("action") not in ("buy", "sell", "hold"):
        raise RiskViolation("Unknown action")
    if number(decision.get("confidence")) > 1:
        raise RiskViolation("Confidence must be between zero and one")
    if not isinstance(decision.get("reasoning"), str) or len(decision["reasoning"]) > 4000:
        raise RiskViolation("Invalid reasoning")
    return decision


def portfolio_value(portfolio, prices=None):
    value = number(portfolio.get("cash_usdt", 0))
    for coin, position in portfolio.get("positions", {}).items():
        price = (prices or {}).get(coin, position.get("entry_price"))
        value += number(position["quantity"]) * number(price, positive=True)
    return value


def start_day(portfolio, prices):
    today = datetime.now(timezone.utc).date().isoformat()
    if portfolio.get("risk_day") != today:
        portfolio["risk_day"] = today
        portfolio["day_start_value"] = portfolio_value(portfolio, prices)


def check_position_size(coin, proposed_usdt, portfolio, prices=None):
    total = portfolio_value(portfolio, prices)
    position = portfolio.get("positions", {}).get(coin)
    held = 0
    if position:
        price = (prices or {}).get(coin, position.get("entry_price"))
        held = number(position["quantity"]) * number(price, positive=True)
    room = max(0, total * config.MAX_POSITION_SIZE_PCT - held)
    return min(number(proposed_usdt), room, number(portfolio["cash_usdt"]))


def check_daily_loss_limit(portfolio, starting_balance=None, prices=None):
    starting = portfolio.get("day_start_value")
    if starting is None:
        starting = starting_balance if starting_balance is not None else config.STARTING_BALANCE_USDT
    current = portfolio_value(portfolio, prices)
    if starting > 0 and (starting - current) / starting >= config.MAX_DAILY_LOSS_PCT:
        raise RiskViolation("Daily loss limit reached; new buys blocked")


def check_trade_count(portfolio):
    today = datetime.now(timezone.utc).date().isoformat()
    if portfolio.get("trades_today", {}).get(today, 0) >= config.MAX_TRADES_PER_DAY:
        raise RiskViolation("Daily trade limit reached; new buys blocked")


def check_confidence(decision):
    if decision["confidence"] < config.MIN_CONFIDENCE_TO_TRADE:
        raise RiskViolation("Confidence below the configured minimum")


def should_stop_loss_or_take_profit(coin, position, current_price):
    entry = number(position["entry_price"], positive=True)
    change = (number(current_price, positive=True) - entry) / entry
    if change <= -config.STOP_LOSS_PCT:
        return "stop_loss"
    if change >= config.TAKE_PROFIT_PCT:
        return "take_profit"
    return None


def validate_trade(decision, portfolio, starting_balance=None, prices=None):
    validate_decision(decision)
    if decision["action"] == "hold":
        return decision
    check_confidence(decision)
    # Loss/count limits must never trap an existing position by blocking an exit.
    if decision["action"] == "buy":
        check_daily_loss_limit(portfolio, starting_balance, prices)
        check_trade_count(portfolio)
    return decision
