"""Private read-only dashboard. Trading runs separately from HTTP requests."""
import hmac
import os
import threading
import secrets
from datetime import datetime, timezone
from flask import Flask, jsonify, send_from_directory, request, Response
from waitress import serve
import config
import operations
from storage import read_json, read_tail, save_json

app = Flask(__name__)
_HERE = os.path.dirname(os.path.abspath(__file__))
_CONTROL_TOKEN = secrets.token_urlsafe(32)


@app.route('/control-center')
def control_center():
    return send_from_directory(_HERE, 'control_center.html')


@app.route('/api/overview')
def overview():
    try:
        live = config.TRADING_MODE == 'live'
        portfolio = read_json(config.LIVE_PORTFOLIO_FILE if live else config.PORTFOLIO_FILE, {})
        history = read_tail(config.LIVE_PORTFOLIO_HISTORY_LOG if live else config.PORTFOLIO_HISTORY_LOG, 1000)
        health = read_json(config.HEALTH_FILE, {})
        last = health.get('last_risk_check')
        age = (datetime.now(timezone.utc)-datetime.fromisoformat(last)).total_seconds() if last else float('inf')
        health['healthy'] = 0 <= age < 90 and not health.get('error') and not health.get('analysis_error')
        health['error'] = health.get('error') or health.get('analysis_error')
        result = operations.overview(portfolio, history, health)
        from market_research import RESEARCH_MARKETS
        result.update(csrf_token=_CONTROL_TOKEN, health=health, mode=config.TRADING_MODE,
                      categories=RESEARCH_MARKETS, research_enabled=config.MARKET_RESEARCH_ENABLED,
                      advanced_filters_enabled=config.ADVANCED_ENTRY_FILTERS)
        return jsonify(result)
    except (ValueError, TypeError, OSError, KeyError):
        return jsonify(error='Overview temporarily unavailable'), 503


@app.route('/api/controls', methods=['POST'])
def update_controls():
    # Authenticated GET supplies a random per-process token; cross-origin forms cannot read it.
    if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), _CONTROL_TOKEN):
        return jsonify(error='Refresh the page before changing controls'), 403
    if not request.is_json:
        return jsonify(error='JSON required'), 415
    value = request.get_json(silent=True)
    if not isinstance(value, dict) or set(value) != {'pause_buys'} or type(value['pause_buys']) is not bool:
        return jsonify(error='Expected pause_buys boolean'), 400
    return jsonify(operations.set_pause(value['pause_buys']))


@app.route('/api/costs', methods=['POST'])
def update_costs():
    import math
    if not hmac.compare_digest(request.headers.get('X-CSRF-Token', ''), _CONTROL_TOKEN):
        return jsonify(error='Refresh the page before changing costs'), 403
    value = request.get_json(silent=True) if request.is_json else None
    cost = value.get('total_usdt') if isinstance(value, dict) else None
    if type(cost) not in (int,float) or not math.isfinite(cost) or cost < 0:
        return jsonify(error='Expected a finite nonnegative cost'), 400
    portfolio = read_json(config.LIVE_PORTFOLIO_FILE if config.TRADING_MODE == 'live' else config.PORTFOLIO_FILE, {})
    if not portfolio.get('created_at'):
        return jsonify(error='Account baseline not initialized'), 409
    save_json(operations.path('operating_costs.json'), {'total_usdt':cost,
              'baseline_created_at':portfolio['created_at'],'recorded_at':operations.stamp()})
    return jsonify(ok=True)


@app.before_request
def require_owner():
    if request.path == "/healthz":
        return None
    password = os.getenv("DASHBOARD_PASSWORD", "")
    if len(password) < 16:
        return Response("Dashboard locked. Set DASHBOARD_PASSWORD (at least 16 characters) in Railway.", status=503)
    auth = request.authorization
    if not auth or not hmac.compare_digest((auth.username or "").encode(), b"owner") or not hmac.compare_digest((auth.password or "").encode(), password.encode()):
        return Response("Owner sign-in required", 401, {"WWW-Authenticate": 'Basic realm="HHR Signal Desk", charset="UTF-8"'})


@app.after_request
def security_headers(response):
    response.headers["Cache-Control"] = "no-store"
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Referrer-Policy"] = "no-referrer"
    response.headers["Content-Security-Policy"] = "default-src 'self'; script-src 'self' 'unsafe-inline'; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; font-src 'self' https://fonts.gstatic.com; connect-src 'self'; frame-ancestors 'none'; base-uri 'self'; form-action 'self'"
    return response


@app.route("/healthz")
def healthz():
    return jsonify({"ok": True})  # HTTP liveness only; contains no account data.


@app.route("/status")
def status():
    live = config.TRADING_MODE == "live"
    try:
        portfolio = read_json(config.LIVE_PORTFOLIO_FILE if live else config.PORTFOLIO_FILE, {})
        trades = read_tail(config.LIVE_TRADES_LOG if live else config.TRADES_LOG, 50)
        if live:
            trades = (trades + portfolio.get("confirmed_trades", []))[-50:]
        history = read_tail(config.LIVE_PORTFOLIO_HISTORY_LOG if live else config.PORTFOLIO_HISTORY_LOG, 1000)
        health = read_json(config.HEALTH_FILE, {})
        stamp = health.get("last_risk_check")
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(stamp)).total_seconds() if stamp else float("inf")
        health["healthy"] = age < config.RISK_CHECK_SECONDS * 3 and not health.get("error") and not health.get("analysis_error")
        health["error"] = health.get("error") or health.get("analysis_error")
        # Do not expose order journal internals through the browser.
        public_portfolio = {key: portfolio.get(key) for key in (
            "cash_usdt", "free_cash_usdt", "positions", "starting_value", "balance_checked_at",
            "performance_unavailable", "balance_notice", "external_change_requires_review")}
        return jsonify({"trading_mode": config.TRADING_MODE,
            "starting_balance": portfolio.get("starting_value") if live else config.STARTING_BALANCE_USDT,
            "portfolio": public_portfolio, "recent_decisions": list(reversed(read_tail(config.DECISIONS_LOG, 50))),
            "recent_trades": list(reversed(trades)), "history": history, "health": health,
            "pending_orders": len(portfolio.get("pending_orders", []))})
    except (ValueError, TypeError, OSError):
        return jsonify({"error": "Status data temporarily unavailable"}), 503


@app.route("/")
def index():
    return send_from_directory(_HERE, "dashboard.html")


def run_server():
    serve(app, host="0.0.0.0", port=int(os.getenv("PORT", "8080")), threads=4)


def start_in_background():
    threading.Thread(target=run_server, daemon=True).start()
