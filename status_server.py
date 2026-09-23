"""
Small read-only HTTP status server so an external dashboard can see what the
bot is doing, without ever touching the trading logic itself.

It only reads the files the bot already writes (portfolio.json,
decisions.jsonl, trades.jsonl, portfolio_history.jsonl) and serves them as
JSON on GET /status. It never accepts input and never places trades — it
can't affect the bot in any way, it just reports on it.
"""

import json
import os
import threading

from flask import Flask, jsonify

import config

app = Flask(__name__)


@app.after_request
def add_cors_headers(response):
    # This is a read-only, no-secrets endpoint — safe to let any page (like
    # the dashboard) fetch it directly from the browser.
    response.headers["Access-Control-Allow-Origin"] = "*"
    return response


def _read_json(path, default):
    if not os.path.exists(path):
        return default
    with open(path) as f:
        return json.load(f)


def _read_jsonl_tail(path, limit):
    if not os.path.exists(path):
        return []
    with open(path) as f:
        lines = f.readlines()
    tail = lines[-limit:]
    return [json.loads(line) for line in tail if line.strip()]


@app.route("/status")
def status():
    live = config.TRADING_MODE == "live"
    portfolio_file = config.LIVE_PORTFOLIO_FILE if live else config.PORTFOLIO_FILE
    trades_log = config.LIVE_TRADES_LOG if live else config.TRADES_LOG
    history_log = config.LIVE_PORTFOLIO_HISTORY_LOG if live else config.PORTFOLIO_HISTORY_LOG

    portfolio = _read_json(portfolio_file, {})
    decisions = _read_jsonl_tail(config.DECISIONS_LOG, 50)
    trades = _read_jsonl_tail(trades_log, 50)
    history = _read_jsonl_tail(history_log, 1000)

    starting_balance = portfolio.get("starting_value") if live else config.STARTING_BALANCE_USDT

    return jsonify(
        {
            "trading_mode": config.TRADING_MODE,
            "starting_balance": starting_balance if starting_balance is not None else config.STARTING_BALANCE_USDT,
            "portfolio": portfolio,
            "recent_decisions": list(reversed(decisions)),
            "recent_trades": list(reversed(trades)),
            "history": history,
        }
    )


@app.route("/")
def index():
    return jsonify({"ok": True, "service": "hhr-signal-desk", "see": "/status"})


def run_server() -> None:
    port = int(os.getenv("PORT", "8080"))
    app.run(host="0.0.0.0", port=port)


def start_in_background() -> None:
    """Runs the status server on its own thread so it doesn't block the
    trading loop in main.py."""
    thread = threading.Thread(target=run_server, daemon=True)
    thread.start()
