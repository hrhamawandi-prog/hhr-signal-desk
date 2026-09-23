"""Frequent protective checks; slow news/model calls run in a separate worker."""
import argparse
import copy
import os
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "core"))
import config
import news_fetcher
import price_fetcher
import ai_decision
import paper_trader
import live_trader
import status_server
from storage import read_json, save_json


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def analyze(prices, portfolio):
    return ai_decision.get_decisions(news_fetcher.fetch_all_news(), prices, portfolio)


def run_cycle(decisions=None):
    trader = live_trader if config.TRADING_MODE == "live" else paper_trader
    portfolio = trader.load_portfolio()
    prices = trader.get_prices() if config.TRADING_MODE == "live" else price_fetcher.get_prices()
    if not prices:
        raise RuntimeError("No fresh prices; trading skipped")
    trader.process_decisions(decisions or [], prices, portfolio)
    trader.log_portfolio_snapshot(portfolio, prices)
    health = read_json(config.HEALTH_FILE, {})
    health.update(last_risk_check=timestamp(), error=None)
    if decisions is not None:
        health.update(last_decision_cycle=timestamp(), analysis_error=None)
    save_json(config.HEALTH_FILE, health)
    return prices, portfolio


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true")
    args = parser.parse_args()
    if not args.loop:
        prices, portfolio = run_cycle()
        run_cycle(analyze(prices, portfolio))
        return
    print(f"Mode: {config.TRADING_MODE}; protective checks every {config.RISK_CHECK_SECONDS}s", flush=True)
    status_server.start_in_background()
    next_decision = 0
    future = None
    worker = ThreadPoolExecutor(max_workers=1, thread_name_prefix="market-analysis")
    while True:
        decisions = None
        if future is not None and future.done():
            try:
                decisions = future.result()
            except Exception as exc:
                health = read_json(config.HEALTH_FILE, {})
                health["analysis_error"] = f"Analysis unavailable ({type(exc).__name__})"
                save_json(config.HEALTH_FILE, health)
            future = None
        try:
            prices, portfolio = run_cycle(decisions)
            if future is None and time.monotonic() >= next_decision:
                future = worker.submit(analyze, copy.deepcopy(prices), copy.deepcopy(portfolio))
                next_decision = time.monotonic() + config.DECISION_INTERVAL_MINUTES * 60
        except Exception as exc:
            # Provider exception strings can contain credentials; record only the type.
            print(f"Protective check failed: {type(exc).__name__}", flush=True)
            health = read_json(config.HEALTH_FILE, {})
            health["error"] = f"Protective check failed ({type(exc).__name__})"
            save_json(config.HEALTH_FILE, health)
        time.sleep(config.RISK_CHECK_SECONDS)


if __name__ == "__main__":
    main()
