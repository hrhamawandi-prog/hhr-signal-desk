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
    portfolio = _read_json(config.PORTFOLIO_FILE, {})
    decisions = _read_jsonl_tail(config.DECISIONS_LOG, 50)
    trades = _read_jsonl_tail(config.TRADES_LOG, 50)
    history = _read_jsonl_tail(config.PORTFOLIO_HISTORY_LOG, 1000)

    return jsonify(
        {
            "trading_mode": config.TRADING_MODE,
            "starting_balance": config.STARTING_BALANCE_USDT,
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

