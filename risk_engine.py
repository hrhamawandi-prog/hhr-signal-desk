"""
Risk engine — sits between the AI's decisions and any actual trade execution.

The AI's output is treated as a *suggestion*, never a command. Every suggestion passes
through these checks before it's allowed to become a trade. This file has no AI in it
on purpose: risk limits should be boring, predictable, and not second-guessed by a model.
"""

from datetime import datetime, timezone

import config


class RiskViolation(Exception):
    """Raised when a proposed trade would break a risk rule."""


def check_confidence(decision: dict) -> None:
    if decision["confidence"] < config.MIN_CONFIDENCE_TO_TRADE:
        raise RiskViolation(
            f"{decision['coin']}: confidence {decision['confidence']:.2f} below "
            f"minimum {config.MIN_CONFIDENCE_TO_TRADE}"
        )


def check_position_size(coin: str, proposed_usdt: float, portfolio: dict) -> float:
    """Caps the proposed trade size to the configured max % of total portfolio value."""
    total_value = portfolio_value(portfolio)
    max_allowed = total_value * config.MAX_POSITION_SIZE_PCT
    if proposed_usdt > max_allowed:
        return max_allowed  # silently cap rather than reject — smaller trade is fine
    return proposed_usdt


def check_daily_loss_limit(portfolio: dict, starting_balance: float | None = None) -> None:
    """starting_balance lets live trading check against the real account's own
    starting value instead of the paper-trading constant. Defaults to the paper
    constant when not given, so paper_trader's existing calls are unaffected."""
    starting = starting_balance if starting_balance is not None else config.STARTING_BALANCE_USDT
    current = portfolio_value(portfolio)
    loss_pct = (starting - current) / starting if starting > 0 else 0
    if loss_pct >= config.MAX_DAILY_LOSS_PCT:
        raise RiskViolation(
            f"Daily loss limit hit: down {loss_pct:.1%}, limit is {config.MAX_DAILY_LOSS_PCT:.1%}. "
            f"Trading halted."
        )


def check_trade_count(portfolio: dict) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    trades_today = portfolio.get("trades_today", {}).get(today, 0)
    if trades_today >= config.MAX_TRADES_PER_DAY:
        raise RiskViolation(f"Max trades per day ({config.MAX_TRADES_PER_DAY}) already reached")


def portfolio_value(portfolio: dict, prices: dict | None = None) -> float:
    """Total value = cash + current market value of all open positions."""
    value = portfolio.get("cash_usdt", 0.0)
    prices = prices or {}
    for coin, position in portfolio.get("positions", {}).items():
        price = prices.get(coin, position.get("entry_price", 0))
        value += position.get("quantity", 0) * price
    return value


def should_stop_loss_or_take_profit(coin: str, position: dict, current_price: float) -> str | None:
    """Returns 'stop_loss', 'take_profit', or None. Checked independently of AI decisions
    every cycle — these are hard exits that fire even if the AI says nothing."""
    entry = position["entry_price"]
    change_pct = (current_price - entry) / entry

    if change_pct <= -config.STOP_LOSS_PCT:
        return "stop_loss"
    if change_pct >= config.TAKE_PROFIT_PCT:
        return "take_profit"
    return None


def validate_trade(decision: dict, portfolio: dict, starting_balance: float | None = None) -> dict:
    """
    Runs all checks for a single proposed trade. Raises RiskViolation if it fails
    outright, or returns the decision with a possibly-reduced size if it's allowed
    through with adjustments.
    """
    check_daily_loss_limit(portfolio, starting_balance)
    check_trade_count(portfolio)

    if decision["action"] == "hold":
        return decision

    check_confidence(decision)

    return decision
