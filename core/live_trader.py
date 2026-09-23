"""OKX spot execution with durable order intents and confirmed fills only.

An unresolved submission blocks further orders until its outcome can be reconciled.
Never infer a fill from an order acknowledgement or retry a timed-out submission.
"""
import json
import os
import uuid
from datetime import datetime, timezone
from functools import lru_cache
import ccxt
import config
import risk_engine
from storage import read_json, save_json


def now():
    return datetime.now(timezone.utc).isoformat()


@lru_cache(maxsize=1)
def get_exchange():
    if config.EXCHANGE_NAME != "okx":
        raise RuntimeError("Live execution currently supports OKX spot only")
    if not all((config.EXCHANGE_API_KEY, config.EXCHANGE_API_SECRET, config.EXCHANGE_API_PASSPHRASE)):
        raise RuntimeError("Missing OKX credentials")
    exchange = ccxt.okx({"apiKey": config.EXCHANGE_API_KEY,
        "secret": config.EXCHANGE_API_SECRET, "password": config.EXCHANGE_API_PASSPHRASE,
        "enableRateLimit": True, "timeout": 15000,
        "options": {"defaultType": "spot"}})
    exchange.load_markets()
    return exchange


def save_portfolio(portfolio):
    save_json(config.LIVE_PORTFOLIO_FILE, portfolio)


def load_portfolio():
    return read_json(config.LIVE_PORTFOLIO_FILE, {
        "cash_usdt": 0.0, "positions": {}, "trades_today": {},
        "starting_value": None, "created_at": now(), "pending_orders": [],
    })


def get_prices():
    exchange = get_exchange()
    symbols = [f"{coin}/USDT" for coin in config.WATCHED_COINS
               if exchange.markets.get(f"{coin}/USDT", {}).get("active") is not False
               and f"{coin}/USDT" in exchange.markets]
    tickers = exchange.fetch_tickers(symbols)
    prices = {}
    for symbol in symbols:
        ticker = tickers.get(symbol, {})
        stamp = ticker.get("timestamp")
        if not stamp or abs(datetime.now(timezone.utc).timestamp() * 1000 - stamp) > 120000:
            continue
        try:
            prices[symbol.split("/")[0]] = risk_engine.number(ticker.get("last"), positive=True)
        except risk_engine.RiskViolation:
            continue
    return prices


def sync_balance(portfolio, exchange, prices):
    balance = exchange.fetch_balance()
    usdt = balance.get("USDT") or {}
    # Total equity includes reserved funds; free funds are the spending limit.
    cash = risk_engine.number(usdt.get("total") or 0)
    free = risk_engine.number(usdt.get("free") or 0)
    old_positions = portfolio.get("positions", {})
    positions = {}
    for coin in config.WATCHED_COINS:
        qty = risk_engine.number((balance.get(coin) or {}).get("total") or 0)
        if qty <= 0:
            continue
        price = risk_engine.number(prices.get(coin), positive=True)
        old = old_positions.get(coin, {})
        positions[coin] = {"quantity": qty,
            "entry_price": old.get("entry_price") or price,
            "entry_time": old.get("entry_time") or now(),
            "free_quantity": risk_engine.number((balance.get(coin) or {}).get("free") or 0)}
    changed = abs(cash - portfolio.get("cash_usdt", 0)) > 0.01 or any(
        abs(positions.get(c, {}).get("quantity", 0) - old_positions.get(c, {}).get("quantity", 0)) > 1e-10
        for c in set(positions) | set(old_positions))
    # Once initialized, unexplained balance changes make return calculations unreliable.
    # Do not silently count deposits as profit or withdrawals as trading losses.
    first_funding = (portfolio.get("starting_value") == 0 and
                     not old_positions and portfolio.get("cash_usdt", 0) == 0 and
                     not portfolio.get("confirmed_trades") and not any(portfolio.get("trades_today", {}).values()))
    if portfolio.get("starting_value") is not None and changed and not first_funding:
        portfolio["performance_unavailable"] = True
        portfolio["balance_notice"] = "Account balance changed outside recorded fills; return history needs reconciliation."
        portfolio["external_change_requires_review"] = True
    portfolio.update(cash_usdt=cash, free_cash_usdt=free, positions=positions, balance_checked_at=now())
    if portfolio.get("starting_value") is None or first_funding:
        portfolio["starting_value"] = risk_engine.portfolio_value(portfolio, prices)
        if first_funding:
            portfolio["day_start_value"] = portfolio["starting_value"]
    save_portfolio(portfolio)


def _record_confirmed(portfolio, pending, order):
    qty = risk_engine.number(order.get("filled"))
    if qty == 0:
        return
    cost = risk_engine.number(order.get("cost"), positive=True)
    price = cost / qty
    coin, side = pending["coin"], pending["action"]
    fees = order.get("fees") or ([order["fee"]] if order.get("fee") else [])
    base_fee = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == coin)
    quote_fee = sum(float(f.get("cost") or 0) for f in fees if f.get("currency") == "USDT")
    if not all(__import__("math").isfinite(x) for x in (base_fee, quote_fee)):
        raise RuntimeError("Invalid exchange fee")
    position = portfolio["positions"].get(coin)
    trade = {"timestamp": order.get("datetime") or now(), "coin": coin, "action": side,
        "quantity": qty, "price": price, "usdt_amount": cost, "fees": fees,
        "reason": pending["reason"], "mode": "live", "order_id": order["id"]}
    if side == "buy":
        net_qty = qty - base_fee
        old_qty = position["quantity"] if position else 0
        basis = old_qty * position["entry_price"] if position else 0
        if net_qty <= 0:
            raise RuntimeError("Invalid net fill quantity")
        portfolio["positions"][coin] = {"quantity": old_qty + net_qty,
            "entry_price": (basis + cost + quote_fee) / (old_qty + net_qty),
            "entry_time": position["entry_time"] if position else now()}
        portfolio["cash_usdt"] -= cost + quote_fee
    else:
        if not position:
            raise RuntimeError("Sell fill has no corresponding position")
        removed = qty + base_fee
        trade["pnl_usdt"] = cost - quote_fee - removed * position["entry_price"]
        portfolio["cash_usdt"] += cost - quote_fee
        position["quantity"] -= removed
        if position["quantity"] <= 1e-10:
            del portfolio["positions"][coin]
    day = trade["timestamp"][:10]
    counts = portfolio.setdefault("trades_today", {})
    counts[day] = counts.get(day, 0) + 1
    # The ledger and updated portfolio are committed in one atomic replacement.
    portfolio.setdefault("confirmed_trades", []).append(trade)


def reconcile_orders(portfolio, exchange):
    for pending in list(portfolio.setdefault("pending_orders", [])):
        params = {} if pending.get("id") else {"clOrdId": pending["client_id"]}
        order = exchange.fetch_order(pending.get("id"), pending["symbol"], params)
        if order.get("status") not in ("closed", "canceled", "expired", "rejected"):
            return False
        # Work on a copy, so failed validation cannot partially mutate live state.
        updated = json.loads(json.dumps(portfolio))
        _record_confirmed(updated, pending, order)
        updated["pending_orders"].remove(pending)
        save_portfolio(updated)
        portfolio.clear()
        portfolio.update(updated)
    return True


def submit_order(coin, side, amount, portfolio, exchange, reason):
    if portfolio.get("pending_orders"):
        return
    symbol = f"{coin}/USDT"
    market = exchange.market(symbol)
    if not market.get("spot") or market.get("active") is False:
        raise RuntimeError("Instrument is not an active spot market")
    if side == "buy":
        amount = min(amount, config.LIVE_MAX_TRADE_USDT, portfolio.get("free_cash_usdt", 0) / 1.05)
        amount = float(exchange.cost_to_precision(symbol, amount))
        minimum = ((market.get("limits") or {}).get("cost") or {}).get("min") or 5
        if amount < max(5, minimum):
            return
    else:
        amount = min(amount, portfolio["positions"][coin].get("free_quantity", amount))
        amount = float(exchange.amount_to_precision(symbol, amount))
        minimum = ((market.get("limits") or {}).get("amount") or {}).get("min") or 0
        if amount <= 0 or amount < minimum:
            return
    pending = {"client_id": uuid.uuid4().hex, "symbol": symbol, "coin": coin,
               "action": side, "reason": reason, "submitted_at": now()}
    portfolio.setdefault("pending_orders", []).append(pending)
    save_portfolio(portfolio)  # Intent exists even if the process dies during the request.
    try:
        params = {"clOrdId": pending["client_id"], "tdMode": "cash"}
        if side == "buy":
            order = exchange.create_market_buy_order_with_cost(symbol, amount, params)
        else:
            order = exchange.create_market_sell_order(symbol, amount, params)
        pending["id"] = order.get("id")
        save_portfolio(portfolio)
    except (ccxt.InvalidOrder, ccxt.InsufficientFunds, ccxt.AuthenticationError) as exc:
        portfolio["pending_orders"].remove(pending)
        save_portfolio(portfolio)
        raise RuntimeError(f"Order rejected: {type(exc).__name__}") from None
    # All other failures retain the intent: outcome may be unknown. No resubmission.
    reconcile_orders(portfolio, exchange)


def process_decisions(decisions, prices, portfolio):
    exchange = get_exchange()
    before = len(portfolio.get("confirmed_trades", []))
    if not reconcile_orders(portfolio, exchange):
        raise RuntimeError("Waiting for confirmation of a previous order; no new orders sent")
    sync_balance(portfolio, exchange, prices)
    risk_engine.start_day(portfolio, prices)
    save_portfolio(portfolio)
    exited = set()
    for coin, position in list(portfolio["positions"].items()):
        price = prices.get(coin)
        if price is None:
            continue
        trigger = risk_engine.should_stop_loss_or_take_profit(coin, position, price)
        if trigger:
            exited.add(coin)
            submit_order(coin, "sell", position["quantity"], portfolio, exchange, trigger)
            if portfolio.get("pending_orders"):
                break
            sync_balance(portfolio, exchange, prices)
    seen = set()
    for decision in decisions:
        if portfolio.get("pending_orders"):
            break
        try:
            risk_engine.validate_trade(decision, portfolio, portfolio.get("starting_value"), prices)
            coin = decision["coin"]
            if coin in seen or coin in exited or coin not in prices or coin not in config.WATCHED_COINS:
                continue
            seen.add(coin)
            if decision["action"] == "buy":
                if portfolio.get("external_change_requires_review"):
                    continue
                total = risk_engine.portfolio_value(portfolio, prices)
                amount = risk_engine.check_position_size(coin, total * config.MAX_POSITION_SIZE_PCT, portfolio, prices)
                if amount >= 5:
                    submit_order(coin, "buy", amount, portfolio, exchange, decision["reasoning"])
            elif decision["action"] == "sell" and coin in portfolio["positions"]:
                submit_order(coin, "sell", portfolio["positions"][coin]["quantity"], portfolio, exchange, decision["reasoning"])
            if not portfolio.get("pending_orders"):
                sync_balance(portfolio, exchange, prices)
        except risk_engine.RiskViolation as exc:
            print(f"[risk] {exc}")
    save_portfolio(portfolio)
    if portfolio.get("pending_orders"):
        raise RuntimeError("Order awaiting confirmed fill; new orders paused")
    return portfolio.get("confirmed_trades", [])[before:]


def log_portfolio_snapshot(portfolio, prices):
    os.makedirs(config.DATA_DIR, exist_ok=True)
    snapshot = {"timestamp": now(), "value": risk_engine.portfolio_value(portfolio, prices),
                "cash": portfolio["cash_usdt"]}
    with open(config.LIVE_PORTFOLIO_HISTORY_LOG, "a", encoding="utf-8") as stream:
        stream.write(json.dumps(snapshot, allow_nan=False) + "\n")


def print_summary(portfolio, prices):
    print(f"[LIVE] Managed account value: ${risk_engine.portfolio_value(portfolio, prices):,.2f}")
