# Strategy research upgrade

This candidate adds an offline research tool and bounds the portfolio context sent to the AI. It does not enable additional live instruments or strategies. The running bot remains unchanged until a separate release.

## Running a comparison

Export completed candles for one instrument and a single interval to CSV with columns `timestamp,open,high,low,close,volume`. Timestamps must be Unix milliseconds in ascending order. Missing candles, duplicates, non-finite values and inconsistent OHLC prices are rejected. The importer cannot determine whether the final candle is completed: remove it if still forming.

From the repository directory:

```sh
python strategy_lab.py candles.csv --output comparison.json --capital 1000 --allocation 0.10 --fee-bps 10 --slippage-bps 5
python -m unittest discover -p 'test_*.py'
```

Capital here is hypothetical research capital, not a deposit or a claim about your account. The tool never accesses an account or submits orders. Supply actual applicable fees and realistic slippage assumptions; repeat with higher costs. The default costs are illustrative.

## What is compared

- Cash, earning zero interest.
- Buy-and-hold with the same allocation as the candidate strategies.
- Trend following: hold when the 20-bar average exceeds the 50-bar average.
- Volume-confirmed breakout: enter above the prior 20-bar highs with above-average volume; exit below the prior 10-bar lows.

Signals use completed candles and fills occur at the next candle's open. The first 70% and remaining 30% of the supplied history are evaluated separately, starting each period in cash. Earlier candles provide warm-up for the later period. No parameter optimization is performed. Each result includes assumed fees, completed trades, estimated net return, and drawdown based on candle-close equity. Open positions are liquidated at the last close for comparison, with the boundary exit labelled.

This is not a simulation of the existing news/AI strategy or its stops and daily limits. It does not model intrabar stops, liquidity capacity, taxes, hosting costs, or AI charges. Intrabar drawdowns may exceed the reported value. The buy-and-hold benchmark is allocation-matched, not a fully invested portfolio unless allocation is 1.0. A short or favorable sample does not establish profitability. Use different instruments and market conditions, then forward-test with an exchange demo account before considering any live integration.

## AI efficiency change

The AI receives current cash, available cash, positions, current-day trade count, pending-order count and relevant risk flags. Historical confirmed fills, order identifiers and old daily counts are omitted. This prevents the prompt from growing with the trade ledger while preserving current exposure information. It does not make model confidence a calibrated probability of profit.

## Validation and limits

40 automated tests passed on October 2, 2026, including the original 28 tests. New coverage verifies exact cost accounting, next-open fills, exclusion of future bars from signals, input rejection, later-period reset, strategy signals and bounded AI context. Public OKX historical-candle retrieval returned HTTP 403 in this environment. No historical profit results or actual demo fills have been established for these strategies.

## Recommended next changes

1. Add a decision audit showing why each signal executed or was blocked, plus returns after exchange fees and separate AI/hosting expenses.
2. Collect candles, volume, bid/ask spread and freshness information; use them as measurable entry filters before AI commentary.
3. Test exchange-hosted protective orders and their recovery/reconciliation in OKX demo before live integration.
4. Expand crypto coverage only through supported spot instruments and explicit liquidity/spread limits. A symbol list alone is insufficient.
5. Add stocks, gold and forex first as research-only categories. Their execution requires a separate eligible broker connection, instrument mapping, market-hours handling and instrument-specific sizing/cost tests. Existing price-display configuration is not live execution support.

Use the strategy lab to select what deserves further testing, rather than automatically selecting the largest historical return. No new strategy is approved for live trading by this document.
