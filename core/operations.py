"""Owner controls, bounded audit journal and conservative performance reporting."""
import json
import os
import threading
from datetime import datetime, timezone
import config
from storage import read_json, save_json, read_tail

_lock = threading.RLock()


def path(name):
    return os.path.join(config.DATA_DIR, name)


def stamp():
    return datetime.now(timezone.utc).isoformat()


def controls():
    try:
        value = read_json(path('controls.json'), {'pause_buys': False})
        if type(value.get('pause_buys')) is not bool:
            raise ValueError('Invalid control state')
        return value
    except (ValueError, TypeError, AttributeError, OSError):
        return {'pause_buys': True, 'error': 'Control state unreadable; new buys blocked'}


def set_pause(paused):
    if type(paused) is not bool:
        raise ValueError('pause_buys must be boolean')
    with _lock:
        value = {'pause_buys': paused, 'updated_at': stamp()}
        save_json(path('controls.json'), value)
        record('control', 'owner', 'New buys paused' if paused else 'New buys resumed')
        return value


def record(kind, coin, reason, **details):
    """Never put provider exception strings or credentials in this journal."""
    event = {'timestamp': stamp(), 'kind': str(kind)[:40], 'coin': str(coin)[:20],
             'reason': str(reason)[:1000], **details}
    with _lock:
        # Single-process deployment: bounded atomic list also prevents half-written events.
        events = read_json(path('audit.json'), [])
        events.append(event)
        save_json(path('audit.json'), events[-1000:])
    return event


def performance(portfolio, history):
    trades = portfolio.get('confirmed_trades', [])
    baseline = portfolio.get('starting_value')
    latest = history[-1]['value'] if history else None
    reliable = (not portfolio.get('performance_unavailable') and baseline is not None
                and latest is not None and baseline > 0)
    costs = read_json(path('operating_costs.json'), {})
    cost = costs.get('total_usdt')
    import math
    valid_cost = (type(cost) in (int, float) and math.isfinite(cost) and cost >= 0
                  and costs.get('baseline_created_at') == portfolio.get('created_at')
                  and bool(portfolio.get('created_at')))
    pnl = latest-baseline if reliable else None
    peak, drawdown = 0., 0.
    for row in history:
        value = row.get('value', 0)
        peak = max(peak, value)
        if peak > 0:
            drawdown = max(drawdown, (peak-value)/peak)
    fees = {}
    for trade in trades:
        for fee in trade.get('fees', []):
            currency = fee.get('currency') or 'unknown'
            fees[currency] = fees.get(currency, 0) + float(fee.get('cost') or 0)
    benchmark = None
    first_prices = portfolio.get('benchmark_prices', {})
    last_prices = history[-1].get('prices', {}) if history else {}
    if reliable and first_prices and all(last_prices.get(c, 0) > 0 for c in first_prices):
        benchmark = 100*(sum(last_prices[c]/p for c,p in first_prices.items())/len(first_prices)-1)
    return {'trading_pnl_usdt': pnl,
            'net_after_recorded_costs_usdt': pnl-cost if pnl is not None and valid_cost else None,
            'operating_costs_usdt': cost if valid_cost else None,
            'costs_note': 'Record cumulative AI/hosting costs for this baseline; missing costs are not zero.',
            'return_pct': 100*pnl/baseline if reliable else None,
            'fees_by_currency': fees, 'completed_fills': len(trades),
            'realized_pnl_usdt': sum(t.get('pnl_usdt', 0) for t in trades),
            'window_drawdown_pct': 100*drawdown if reliable else None,
            'benchmark_equal_weight_gross_pct': benchmark,
            'benchmark_note': 'Fully invested equal-weight initial crypto basket, excluding fees; exposure differs from bot.',
            'reconciliation_required': bool(portfolio.get('external_change_requires_review'))}


def overview(portfolio, history, health):
    research = read_json(path('research.json'), {'markets': {}, 'mode': 'research_only'})
    alerts = []
    if health.get('error'):
        alerts.append(health['error'])
    if not health.get('healthy'):
        alerts.append('Protective checks or analysis need attention')
    if portfolio.get('external_change_requires_review'):
        alerts.append('Account balance changes require reconciliation before new buys')
    if portfolio.get('pending_orders'):
        alerts.append('An order outcome is pending; additional submissions are blocked')
    if controls().get('pause_buys'):
        alerts.append('New buys are paused; protective checks remain enabled')
    return {'controls': controls(), 'audit': list(reversed(read_json(path('audit.json'), [])[-100:])),
            'research': research, 'performance': performance(portfolio, history),
            'alerts': alerts, 'protection': 'Application-managed exits; exchange protection is demo-only until validated'}
