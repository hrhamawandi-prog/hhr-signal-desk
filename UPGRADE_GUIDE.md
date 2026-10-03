# HHR research and operations upgrade — October 3, 2026

## Release status

This release adds operations and research capabilities. Additional live instruments, autonomous candidate strategies and broker connections remain disabled. Production deployment status must be verified in Railway; software tests do not establish profitability. This guide supersedes the earlier research-only candidate notes where features overlap.

## Included

- `/control-center`: authenticated mobile-friendly status, dashboard-only alerts, recent decision journal, performance, fee totals, cost recording, research categories and pause/resume-new-buys controls.
- Authenticated mutation endpoints require a random per-process CSRF token from the authenticated overview. Controls persist atomically on the data volume. Missing controls preserve the pre-upgrade behavior; malformed controls block buys. Sell and protective checks are not disabled by the pause control. An order already submitted can still fill.
- Bounded atomic journal of the most recent 1,000 events: intents, holds, blocked signals, minimum-order skips, and owner control changes. An intent is not a fill. Confirmed fills remain in the durable portfolio ledger. Keep the existing one-replica deployment.
- Public hourly crypto research: 20/50-bar averages, volume ratio, ATR percentage, bid/ask spreads, market regime and aligned return correlations. Incomplete candles are excluded; gaps and stale inputs are rejected. Candidate strategies are trend, volume-confirmed breakout and range reversion.
- Optional new-entry filters: fresh bid/ask quotes, maximum 30 basis-point spread, minimum 1,000,000 USDT reported daily quote volume, a 30% total crypto exposure cap and position sizing capped by 0.5% equity divided by the configured stop distance. These are candidate settings, not personalized risk recommendations. All crypto is conservatively treated as one exposure group; measured pair correlations are research observations only. Stops can slip, so the sizing formula does not guarantee a maximum loss.
- More crypto research candidates, plus stock/ETF, gold/silver/oil and forex categories. No new live symbols. Noncrypto instruments are labelled `broker_or_csv_required`; real execution adapters, sessions and permissions remain future broker-specific work.
- Comparison lab includes cash, allocation-matched buy-and-hold, trend, breakout and range. Signals use only closed candles and execute at next-open prices with explicit fee/slippage assumptions. Earlier/later samples start separately. See `STRATEGY_LAB.md` for limitations.
- AI portfolio context excludes historical order journals. When research is enabled, fresh measured features accompany news. Candidate strategy signals are not automatically promoted to live orders. The existing AI-driven execution remains until a separately validated strategy release replaces it.
- Performance separates fees by currency and allows recording cumulative AI/hosting costs. Missing costs are unknown, not zero. Returns are hidden when external balance changes require reconciliation. Deposit/withdrawal classification is not automated; current balance changes still require review. Drawdown covers only the last 1,000 available snapshots and uses snapshot equity, not intrabar lows. The optional benchmark is a fully invested equal-weight initial crypto basket, gross of fees; bot exposure differs. Existing funded accounts without initial price records show no benchmark rather than inventing one. Legacy paper results lack complete fee accounting and are not evidence of live profitability.
- `core/demo_protection.py`: isolated OKX demo OCO adapter, dedicated demo credentials, persisted client IDs, timeout reconciliation and duplicate-submission protection. It refuses clients without the explicit OKX simulation header. An acknowledged or triggered order is never reported as a confirmed fill. This is not imported by the live trading loop.

## Settings and operation

Defaults preserve the existing live strategy and universe:

```text
MARKET_RESEARCH_ENABLED=false
ADVANCED_ENTRY_FILTERS=false
```

After testing and reviewing deployment, `MARKET_RESEARCH_ENABLED=true` collects research during the analysis worker's cycle and adds fresh measurements to AI context. This can affect AI recommendations even though candidate strategies remain research-only. The public client is separate from the live order client. Failed research startup can delay that cycle's AI analysis, while the main protective loop remains separate. `ADVANCED_ENTRY_FILTERS=true` activates the new buy filters; sell exits are not gated by these filters. Do not turn either setting on without reviewing the changed behavior.

Offline commands (no account access):

```sh
python research_cli.py --collect
python research_cli.py --export BTC --output btc-hourly.csv
python strategy_lab.py btc-hourly.csv --output comparison.json
python -m unittest discover -p 'test_*.py'
node test_control_center.cjs
```

An exported candle CSV from another provider or broker can be compared by the same lab. Use a consistent instrument, interval, volume definition and quote currency; the lab does not model broker sessions, splits, dividends, financing, futures rolls or currency conversion. The initial 200-bar collector is a short research sample, not a robust profitability evaluation. Longer histories and realistic market-specific simulations remain necessary.

For protective-order validation, use `OKX_DEMO_API_KEY`, `OKX_DEMO_API_SECRET` and `OKX_DEMO_API_PASSPHRASE` with `create_demo_client()`. There is no fallback to production keys. Before live integration, verify minimum sizes, partial fills, duplicate and timeout recovery, trigger child-order fills, cancellations/replacements, and coexistence with application-managed exits. A pending demo intent is never automatically cleared; reconcile it before any manual reset. No actual demo order was submitted during this development.

## Validation and remaining work

60 Python tests and both JavaScript dashboard checks passed locally. Coverage includes the previous order/risk tests plus authenticated controls and CSRF, pause behavior, costs, market validation, risk budgets, no new live symbols, unknown-order outcomes, and strategy replay arithmetic. The control screen was inspected with synthetic data at phone width, with no page-width overflow.

Public historical OKX access previously returned HTTP 403 from this environment; no successful historical strategy-performance results are claimed. A realistic historical run, actual OKX demo order lifecycle, and a staged deployment check are still required. Additional brokers are intentionally deferred at the owner's request. Live exchange protective orders and autonomous multi-strategy execution are not complete or enabled. No improvement in investment returns is established by software tests.
