"""Isolated OKX demo protective-order adapter. Never used by the live loop.

Use only with a separately created sandbox client and a dedicated persisted journal.
No cancel/replacement automation: an unresolved or existing order prevents resubmission.
"""
import uuid
import math
import os
from storage import read_json, save_json


def create_demo_client():
    """No fallback to production credentials. Sandbox mode is set before API access."""
    import ccxt
    names=('OKX_DEMO_API_KEY','OKX_DEMO_API_SECRET','OKX_DEMO_API_PASSPHRASE')
    values=[os.getenv(name) for name in names]
    if not all(values): raise ValueError('Separate OKX demo credentials required')
    exchange=ccxt.okx({'apiKey':values[0],'secret':values[1],'password':values[2],
                      'enableRateLimit':True,'timeout':15000,'options':{'defaultType':'spot'}})
    exchange.set_sandbox_mode(True)
    ensure_demo(exchange)
    exchange.load_markets()
    return exchange


def ensure_demo(exchange):
    if getattr(exchange, 'id', None) != 'okx' or getattr(exchange, 'headers', {}).get('x-simulated-trading') != '1':
        raise ValueError('Explicit OKX demo client required; live orders prohibited')


def submit_oco(exchange, journal, symbol, quantity, stop_price, take_price):
    ensure_demo(exchange)
    if any(type(x) not in (int,float) or not math.isfinite(x) or x<=0 for x in (quantity,stop_price,take_price)) or stop_price>=take_price:
        raise ValueError('Invalid quantity or trigger prices')
    existing=read_json(journal,{})
    if existing:
        raise RuntimeError('Existing protective intent must be reviewed; no duplicate submitted')
    market=exchange.market(symbol)
    if not market.get('spot') or market.get('active') is False:
        raise ValueError('Active spot market required')
    amount=exchange.amount_to_precision(symbol,quantity)
    stop=exchange.price_to_precision(symbol,stop_price)
    take=exchange.price_to_precision(symbol,take_price)
    if float(amount)<=0 or float(stop)>=float(take):
        raise ValueError('Invalid rounded order')
    client_id=uuid.uuid4().hex
    request={'instId':market['id'],'tdMode':'cash','side':'sell','ordType':'oco','sz':amount,
             'algoClOrdId':client_id,'slTriggerPx':stop,'slOrdPx':'-1','slTriggerPxType':'last',
             'tpTriggerPx':take,'tpOrdPx':'-1','tpTriggerPxType':'last'}
    state={'client_id':client_id,'symbol':symbol,'quantity':float(amount),'status':'submission_unknown','mode':'demo'}
    save_json(journal,state)
    # A timeout deliberately retains the durable intent for reconciliation, never retry.
    result=exchange.private_post_trade_order_algo(request)
    rows=result.get('data') or []
    if result.get('code')!='0' or not rows or rows[0].get('sCode')!='0':
        state['status']='rejected'; save_json(journal,state)
        raise RuntimeError('Demo protective order rejected; inspect demo exchange records')
    state.update(order_id=rows[0]['algoId'],status='acknowledged')
    save_json(journal,state)
    return state


def reconcile(exchange,journal):
    ensure_demo(exchange)
    state=read_json(journal,{})
    if not state: return {'status':'no_intent'}
    query={'algoId':state['order_id']} if state.get('order_id') else {'algoClOrdId':state['client_id']}
    result=exchange.private_get_trade_order_algo(query)
    rows=result.get('data') or []
    if result.get('code')!='0' or len(rows)!=1:
        raise RuntimeError('Protective outcome unresolved; no replacement permitted')
    row=rows[0]
    if row.get('algoClOrdId') != state['client_id']:
        raise RuntimeError('Protective order identity mismatch')
    state['exchange_state']=row.get('state')
    state['status']='exchange_'+str(row.get('state','unknown'))
    state['execution_order_ids']=row.get('ordIdList',[])
    # Triggered is not filled: execution orders require separate fill reconciliation.
    save_json(journal,state)
    return state
