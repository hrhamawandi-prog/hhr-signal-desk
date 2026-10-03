"""Public candle research; candidates cannot expand the live execution universe."""
import math
import os
import statistics
import time
import config
from storage import read_json, save_json
from operations import path, stamp

RESEARCH_MARKETS = {
    'crypto': ['BTC','ETH','SOL','BNB','XRP','ADA','LINK','AVAX','LTC','DOT'],
    'stocks': ['SPY','QQQ','AAPL','MSFT'],
    'commodities': ['XAU','XAG','WTICO'],
    'forex': ['EURUSD','GBPUSD','USDJPY'],
}


def features(rows, now_ms):
    # Hourly CCXT candles. Exclude the unfinished bar, then reject gaps/stale history.
    completed = [r for r in rows if len(r) >= 6 and r[0]+3600000 <= now_ms]
    if len(completed) < 60:
        raise ValueError('Need 60 completed hourly candles')
    rows = completed[-200:]
    for i,r in enumerate(rows):
        if not all(type(x) in (int,float) and math.isfinite(x) for x in r[:6]):
            raise ValueError('Invalid candle values')
        if min(r[1:5]) <= 0 or r[5] < 0 or r[3] > min(r[1],r[4]) or r[2] < max(r[1],r[4]):
            raise ValueError('Invalid candle range')
        if i and r[0]-rows[i-1][0] != 3600000:
            raise ValueError('Missing or unordered hourly candles')
    if not 0 <= now_ms-(rows[-1][0]+3600000) <= 3900000:
        raise ValueError('Candle history is stale')
    close = rows[-1][4]
    fast = statistics.mean(r[4] for r in rows[-20:])
    slow = statistics.mean(r[4] for r in rows[-50:])
    prior = rows[-21:-1]
    avg_volume = statistics.mean(r[5] for r in prior)
    atr = statistics.mean(max(rows[i][2]-rows[i][3],abs(rows[i][2]-rows[i-1][4]),
                              abs(rows[i][3]-rows[i-1][4])) for i in range(len(rows)-14,len(rows)))
    std = statistics.pstdev(r[4] for r in rows[-20:])
    regime = 'trend' if abs(fast/slow-1) > .01 else 'range'
    breakout = close > max(r[2] for r in prior) and rows[-1][5] > avg_volume
    range_entry = regime == 'range' and std > 0 and close < fast-2*std
    return {'last_closed_ms':rows[-1][0], 'close':close, 'sma20':fast,'sma50':slow,
            'atr_pct':100*atr/close,'volume_ratio':rows[-1][5]/avg_volume if avg_volume else None,
            'regime':regime,'strategies':{'trend':'buy' if fast>slow else 'cash',
              'breakout':'buy' if breakout else 'wait',
              'range':'buy' if range_entry else 'wait'}, 'candles':rows}


def quote_quality(ticker, now_ms):
    bid,ask,ts = ticker.get('bid'),ticker.get('ask'),ticker.get('timestamp')
    if not all(type(x) in (int,float) and math.isfinite(x) and x>0 for x in (bid,ask,ts)):
        raise ValueError('Missing quote or timestamp')
    if ask < bid or not -5000 <= now_ms-ts <= 120000:
        raise ValueError('Crossed or stale quote')
    return (ask-bid)/((ask+bid)/2)*10000


def collect(exchange):
    report={'timestamp':stamp(),'mode':'research_only','markets':{},'categories':RESEARCH_MARKETS}
    for coin in RESEARCH_MARKETS['crypto']:
        symbol=coin+'/USDT'
        market=exchange.markets.get(symbol,{})
        if not market.get('spot') or market.get('active') is False:
            report['markets'][coin]={'status':'unsupported'}
            continue
        try:
            rows=exchange.fetch_ohlcv(symbol,'1h',limit=201)
            f=features(rows,int(time.time()*1000))
            ticker=exchange.fetch_ticker(symbol)
            spread=quote_quality(ticker,int(time.time()*1000))
            volume=ticker.get('quoteVolume')
            if type(volume) not in (int,float) or not math.isfinite(volume): volume=None
            liquid=type(volume) in (int,float) and math.isfinite(volume) and volume>=config.MIN_QUOTE_VOLUME_USDT
            candles=f.pop('candles')
            save_json(path('candles/'+coin+'.json'),candles)
            report['markets'][coin]={**f,'spread_bps':spread,'quote_volume':volume,
               'status':'eligible_for_research' if liquid and spread<=config.MAX_SPREAD_BPS else 'liquidity_filter',
               'live_enabled':coin in config.WATCHED_COINS}
        except Exception as exc:
            report['markets'][coin]={'status':'unavailable','error_type':type(exc).__name__}
    for category in ('stocks','commodities','forex'):
        for coin in RESEARCH_MARKETS[category]:
            report['markets'][coin]={'status':'broker_or_csv_required','category':category,'live_enabled':False}
    available = {coin:read_json(path('candles/'+coin+'.json'),[]) for coin in RESEARCH_MARKETS['crypto']
                 if report['markets'].get(coin,{}).get('status') == 'eligible_for_research'}
    report['correlations'] = {a+'/'+b:correlation(available[a],available[b])
                              for a in available for b in available if a<b}
    save_json(path('research.json'),report)
    return report


def correlation(a,b):
    aa={r[0]:r[4] for r in a}; bb={r[0]:r[4] for r in b}
    times=sorted(set(aa)&set(bb))
    pairs=[(times[i-1],times[i]) for i in range(1,len(times)) if times[i]-times[i-1]==3600000]
    if len(pairs)<30: return None
    x=[math.log(aa[t]/aa[p]) for p,t in pairs]; y=[math.log(bb[t]/bb[p]) for p,t in pairs]
    if not statistics.pstdev(x) or not statistics.pstdev(y): return None
    return statistics.correlation(x,y)


def entry_budget(coin, portfolio, prices, exchange):
    """Fresh spread gate; conservative total crypto cap if correlations are unavailable."""
    import risk_engine
    from operations import controls
    if controls().get('pause_buys'):
        raise risk_engine.RiskViolation('Owner paused new buys')
    try:
        ticker=exchange.fetch_ticker(coin+'/USDT')
        spread=quote_quality(ticker,int(time.time()*1000))
        if spread>config.MAX_SPREAD_BPS:
            raise ValueError('Spread exceeds limit')
        volume=ticker.get('quoteVolume')
        if type(volume) not in (int,float) or not math.isfinite(volume) or volume<config.MIN_QUOTE_VOLUME_USDT:
            raise ValueError('Insufficient quoted daily volume')
    except Exception as exc:
        raise risk_engine.RiskViolation('Fresh liquidity check failed: '+type(exc).__name__) from None
    equity=risk_engine.portfolio_value(portfolio,prices)
    exposure=sum(p['quantity']*prices[c] for c,p in portfolio['positions'].items())
    # Group cap deliberately treats all crypto as correlated; measured correlations are research evidence.
    group_room=max(0,equity*config.MAX_CRYPTO_EXPOSURE_PCT-exposure)
    held=portfolio['positions'].get(coin,{}).get('quantity',0)*prices[coin]
    risk_room=max(0,equity*config.RISK_PER_POSITION_PCT/config.STOP_LOSS_PCT-held)
    return min(group_room,risk_room)
