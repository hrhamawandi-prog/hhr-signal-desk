"""
Main entry point. Runs one full decision cycle:

  1. Fetch recent news for watched coins
  2. Fetch current prices
  3. Ask the AI for a decision per coin
  4. Run every decision through the risk engine
  5. Execute whatever survives (paper trading only, for now)
  6. Print a summary

Run once with `python main.py`, or use --loop to run continuously on the interval
set in config.py (DECISION_INTERVAL_MINUTES).
"""

import argparse
import sys
import time

sys.path.insert(0, "core")

import config
import news_fetcher
import price_fetcher
import ai_decision
import paper_trader


BANNER = r"""
  _  _  _  _  ___  ___  _  _  _   ___  ___  ___  _  __
 | || || || || _ \| _ \| || | | / __|| __|| _ \| |/ /
 | __ || __ ||   /|   /| __ | | \__ \| _| |  _/| ' 
 |_||_||_||_||_|_\|_|_\|_||_| |_||___/|___||_|  |_|\_\
                                         SIGNAL DESK
"""


def print_startup_banner() -> None:
    print(BANNER)
    mode_label = "PAPER TRADING (simulated money)" if config.TRADING_MODE == "paper" else config.TRADING_MODE.upper()
    print(f"  Mode: {mode_label}")
    print(f"  Watching: {', '.join(config.ALL_INSTRUMENTS)}")
    print(f"  Decision interval: every {config.DECISION_INTERVAL_MINUTES} min")
    print(f"  Starting balance: ${config.STARTING_BALANCE_USDT:,.2f}\n")


def run_cycle() -> None:
    timestamp = time.strftime("%Y-%m-%d %H:%M:%S")
    print(f"\n── Cycle · {timestamp} ({config.TRADING_MODE}) ──")

    if config.TRADING_MODE == "live":
        print(
            "  TRADING_MODE is set to 'live' but live execution isn't wired up in this "
            "starter project yet — running as paper trading instead. See README.md "
            "before ever enabling real trades."
        )

    portfolio = paper_trader.load_portfolio()

    news = news_fetcher.fetch_all_news()
    prices = price_fetcher.get_prices()

    if not prices:
        print("  No prices available this cycle, skipping.")
        return

    missing = set(config.ALL_INSTRUMENTS) - set(prices.keys())
    if missing:
        print(f"  (no price this cycle for: {', '.join(sorted(missing))} — skipping them)")

    decisions = ai_decision.get_decisions(news, prices, portfolio)
    if not decisions:
        print("  AI returned no decisions this cycle.")
    else:
        for d in decisions:
            print(f"  {d['coin']:<7} {d['action']:<5} (confidence {d['confidence']:.2f}) — {d['reasoning']}")

    executed = paper_trader.process_decisions(decisions, prices, portfolio)
    for trade in executed:
        pnl_note = f", P&L ${trade['pnl_usdt']:+.2f}" if "pnl_usdt" in trade else ""
        print(f"  EXECUTED: {trade['action']} {trade['coin']} @ ${trade['price']:,.2f}{pnl_note}")

    paper_trader.print_summary(portfolio, prices)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--loop", action="store_true", help="Run continuously on the configured interval")
    args = parser.parse_args()

    print_startup_banner()

    if args.loop:
        interval_sec = config.DECISION_INTERVAL_MINUTES * 60
        print(f"Running continuously every {config.DECISION_INTERVAL_MINUTES} minutes. Ctrl+C to stop.\n")
        while True:
            try:
                run_cycle()
            except Exception as e:
                # Never let one bad cycle kill the whole bot — log it and try again
                # next cycle instead of stopping entirely.
                print(f"[main] Cycle failed unexpectedly, will retry next cycle: {e}")
            time.sleep(interval_sec)
    else:
        run_cycle()


if __name__ == "__main__":
    main()
