"""Offline, long-only research. No credentials, network access, or order submission.

CSV columns: timestamp (Unix milliseconds), open, high, low, close, volume.
Use completed, consecutive candles from one instrument and one interval.
Signals use completed bars and execute at the following bar's open.
This is a simplified strategy comparison, not a replay of the live AI bot.
"""
import argparse
import csv
import json
import math
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class Candle:
    timestamp: int
    open: float
    high: float
    low: float
    close: float
    volume: float


def validate(candles):
    if len(candles) < 52:
        raise ValueError('At least 52 completed candles are required')
    interval = candles[1].timestamp - candles[0].timestamp
    if interval <= 0:
        raise ValueError('Timestamps must increase')
    for i, c in enumerate(candles):
        if not isinstance(c.timestamp, int) or c.timestamp < 0:
            raise ValueError('Timestamp must be nonnegative Unix milliseconds')
        if not all(math.isfinite(x) and x > 0 for x in (c.open,c.high,c.low,c.close)):
            raise ValueError('Prices must be finite and positive')
        if not math.isfinite(c.volume) or c.volume < 0:
            raise ValueError('Volume must be finite and nonnegative')
        if c.low > min(c.open,c.close) or c.high < max(c.open,c.close) or c.low > c.high:
            raise ValueError('Inconsistent candle prices')
        if i and c.timestamp - candles[i-1].timestamp != interval:
            raise ValueError('Duplicate, unordered, or missing candles')


def read_candles(path):
    with open(path, newline='', encoding='utf-8-sig') as stream:
        rows = list(csv.DictReader(stream))
    candles = [Candle(int(r['timestamp']), *(float(r[k]) for k in
                ('open','high','low','close','volume'))) for r in rows]
    validate(candles)
    return candles


def signal(history, strategy, holding):
    """All supplied candles are already closed; never inspect the execution bar."""
    if strategy == 'buy_hold':
        return True
    if strategy == 'cash':
        return False
    if len(history) < 50:
        return False
    closes = [c.close for c in history[-50:]]
    if strategy == 'trend':
        return sum(closes[-20:])/20 > sum(closes)/50
    if strategy == 'breakout':
        if holding:
            return history[-1].close >= min(c.low for c in history[-11:-1])
        prior = history[-21:-1]
        return (history[-1].close > max(c.high for c in prior)
                and history[-1].volume > sum(c.volume for c in prior)/20)
    if strategy == 'range':
        import statistics
        mean = statistics.mean(closes[-20:])
        deviation = statistics.pstdev(closes[-20:])
        ranging = abs(mean/(sum(closes)/50)-1) <= .01
        if holding:
            return ranging and closes[-1] < mean
        return ranging and deviation > 0 and closes[-1] < mean-2*deviation
    raise ValueError('Unknown strategy')


def replay(candles, strategy, *, capital=1000., allocation=.1, fee_bps=10., slippage_bps=5., start=50):
    validate(candles)
    if strategy not in ('cash','buy_hold','trend','breakout','range'):
        raise ValueError('Unknown strategy')
    if not all(math.isfinite(x) for x in (capital,allocation,fee_bps,slippage_bps)):
        raise ValueError('Settings must be finite')
    if capital <= 0 or not 0 < allocation <= 1 or not 0 <= fee_bps < 10000 or not 0 <= slippage_bps < 10000:
        raise ValueError('Invalid capital, allocation, or cost assumption')
    if not isinstance(start,int) or not 50 <= start < len(candles):
        raise ValueError('Invalid evaluation start')
    fee, slip = fee_bps/10000, slippage_bps/10000
    cash, qty, basis, fees = capital, 0., 0., 0.
    peak, drawdown, trades, curves = capital, 0., [], []
    for i in range(start,len(candles)):
        c = candles[i]
        want = signal(candles[:i], strategy, qty > 0)
        if qty and not want:
            gross = qty*c.open*(1-slip)
            charge = gross*fee
            cash += gross-charge
            fees += charge
            trades.append({'exit_timestamp':c.timestamp,'net_pnl':gross-charge-basis})
            qty, basis = 0., 0.
        elif not qty and want:
            budget = cash*allocation
            gross = budget/(1+fee)
            charge = gross*fee
            qty = gross/(c.open*(1+slip))
            basis = budget
            cash -= budget
            fees += charge
        # Estimate liquidation costs on open positions for comparable net equity.
        equity = cash + qty*c.close*(1-slip)*(1-fee)
        peak = max(peak,equity)
        drawdown = max(drawdown,(peak-equity)/peak)
        curves.append({'timestamp':c.timestamp,'net_equity':equity})
    # Liquidate only at the data boundary, not as a strategy-generated exit.
    if qty:
        gross = qty*candles[-1].close*(1-slip)
        charge = gross*fee
        fees += charge
        cash += gross-charge
        trades.append({'exit_timestamp':candles[-1].timestamp,'net_pnl':gross-charge-basis,'boundary_exit':True})
    return {'strategy':strategy,'start_timestamp':candles[start].timestamp,
            'end_timestamp':candles[-1].timestamp,'net_return_pct':100*(cash/capital-1),
            'final_equity':cash,'max_close_drawdown_pct':100*drawdown,
            'completed_trades':len(trades),'winning_trades':sum(t['net_pnl']>0 for t in trades),
            'fees_paid':fees,'trades':trades,'equity':curves}


def compare(candles, **settings):
    if len(candles) < 150:
        raise ValueError('At least 150 candles required for a separate later-period comparison')
    split = max(51,int(len(candles)*.7))
    # Fixed rules, no parameter fitting. Later period starts flat with prior bars as warm-up.
    return {period:[replay(sample,s,start=start,**settings) for s in ('cash','buy_hold','trend','breakout','range')]
            for period,sample,start in [('earlier',candles[:split],50),('later',candles,split)]}


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('csv')
    parser.add_argument('--output',required=True)
    parser.add_argument('--capital',type=float,default=1000.)
    parser.add_argument('--allocation',type=float,default=.1)
    parser.add_argument('--fee-bps',type=float,default=10.)
    parser.add_argument('--slippage-bps',type=float,default=5.)
    args=parser.parse_args()
    settings={k:getattr(args,k) for k in ('capital','allocation','fee_bps','slippage_bps')}
    report={'source':str(Path(args.csv).resolve()),'assumptions':settings,
            'limitations':['Research only; no orders or live AI replay.',
              'Costs are assumptions, not measured exchange fills.',
              'No intrabar stops, liquidity capacity, taxes, AI or hosting costs modeled.',
              'Drawdown uses candle-close equity; intrabar losses may be larger.',
              'Same allocation for buy-and-hold and strategies; unused cash earns zero.',
              'Later-period results are not proof of future profitability.'],
            'results':compare(read_candles(args.csv),**settings)}
    Path(args.output).write_text(json.dumps(report,indent=2,allow_nan=False),encoding='utf-8')
    for period,results in report['results'].items():
        for r in results:
            print(f"{period:7} {r['strategy']:9} net={r['net_return_pct']:.2f}% drawdown={r['max_close_drawdown_pct']:.2f}% trades={r['completed_trades']}")


if __name__=='__main__': main()
