# HHR Signal Desk

Personal news-assisted trading bot with a private, read-only dashboard.

## Modes and limits

- `TRADING_MODE=paper`: simulated portfolio; no orders sent to an exchange.
- `TRADING_MODE=live`: real OKX spot orders for configured crypto pairs only.
- The configured 10% per-instrument cap includes existing holdings. The default
  per-order live cap remains $50; confidence threshold and stop/target percentages
  are unchanged. Loss and trade-count limits block new buys, not protective exits.
- Protective checks run about every 30 seconds while online. News/model analysis
  runs in a separate worker every 30 minutes. These are application-managed exits,
  not exchange-hosted stop orders; outages, missing prices, pending orders and
  exchange errors can delay them. An exchange-native protective-order integration
  remains a future improvement.

## Private dashboard

Set `DASHBOARD_PASSWORD` to a unique password of at least 16 characters in Railway
Variables. Sign in as `owner`. Enter credentials only over HTTPS.
Without that variable, both `/` and `/status` are locked (503); there is no public
fallback. `/healthz` exposes only HTTP liveness. The dashboard uses a separate
heartbeat to distinguish a responsive web server from current trading checks.
Never commit passwords or exchange/API keys. The GitHub source may remain public.

## Running and tests

Use Python 3.12 or newer. Install `requirements.txt`, then run:

```
python -m unittest test_bot.py -v
python main.py --loop
```

Tests mock exchange and AI calls. They do not place orders.
Set the existing API variables listed in `.env.example`. `AI_MODEL` optionally
selects a model supported by your Anthropic account. Mount a persistent Railway
volume at `/app/data`; use one replica and one trading process per account.
Use an OKX key with only the required read/trade permissions. This code does not
call withdrawal or transfer APIs, but it cannot prove the key's permissions.

## Order recovery and account changes

An order intent is persisted before submission, with a unique OKX client order ID.
Only confirmed terminal fills affect the local ledger. Partial fills of canceled
orders are retained; fees in the base currency or USDT are applied. Unknown or
open orders block new submissions until reconciled. A timeout is never retried
as a new order. If an order stays unknown, inspect that client ID in OKX before
manually resolving the journal; never simply delete it and submit again.

Balances refresh from OKX, including reserved funds; spending uses only free USDT.
The displayed account value includes USDT and configured crypto assets in the
OKX trading account, not every asset or the funding account. Pre-existing holdings
have a reference entry price rather than a known historical cost basis.

Unexplained balance changes (deposit, withdrawal, external trade, or inconsistent
exchange snapshot) update the displayed balance but pause new buys and hide return
percentages. Protective exits remain eligible. Reconcile the change with OKX first.
With the bot stopped and no pending orders, an operator can reset `starting_value`
and `day_start_value` to verified current equity and clear
`external_change_requires_review`, `performance_unavailable`, and `balance_notice`
in the persisted live portfolio. Back up the volume first. This resets the return
baseline; do not represent the result as lifetime trading profit.

Confirmed trades and portfolio updates share one atomic JSON commit. Existing
legacy trade logs remain visible. Future ledger growth, log rotation, backup
restores, and exchange-native stops warrant further work as the bot grows.

## Files

`main.py` starts the worker and protective loop. `core/` is the maintained code.
The three top-level legacy modules are compatibility imports, not separate copies.
`data/` contains private state and must not be committed. Production deployment
should follow passing tests and dashboard password setup.
