import unittest
from dataclasses import replace
from unittest.mock import patch
from strategy_lab import Candle, replay, compare, validate, signal


def bars(count=180):
    return [Candle(i*3600000,100.,101.,99.,100.,10.) for i in range(count)]


class StrategyLabTests(unittest.TestCase):
    def test_cash_unchanged(self):
        result=replay(bars(),'cash')
        self.assertEqual(result['final_equity'],1000)
        self.assertEqual(result['completed_trades'],0)
    def test_range_stays_out_of_flat_market(self):
        self.assertFalse(signal(bars(),'range',False))
        self.assertEqual(replay(bars(),'range')['completed_trades'],0)

    def test_flat_market_loses_exact_costs(self):
        result=replay(bars(),'buy_hold',allocation=1,fee_bps=10,slippage_bps=5)
        expected=1000/1.001/1.0005*.9995*.999
        self.assertAlmostEqual(result['final_equity'],expected)
        self.assertLess(result['net_return_pct'],0)

    def test_no_execution_bar_in_signal(self):
        candles=bars()
        calls=[]
        def spy(history,strategy,holding):
            calls.append(history[-1].timestamp)
            return False
        with patch('strategy_lab.signal',side_effect=spy): replay(candles,'trend')
        self.assertEqual(calls,[c.timestamp for c in candles[49:-1]])

    def test_fill_uses_next_open_not_signal_close(self):
        candles=bars(52)
        candles[50]=replace(candles[50],open=200,high=200)
        result=replay(candles,'buy_hold',allocation=1,fee_bps=0,slippage_bps=0)
        self.assertEqual(result['final_equity'],500)

    def test_bad_candles_rejected(self):
        for replacement in (replace(bars()[60],close=float('nan')),
                            replace(bars()[60],timestamp=0),
                            replace(bars()[60],high=90),
                            replace(bars()[60],volume=-1)):
            candles=bars(); candles[60]=replacement
            with self.assertRaises(ValueError): validate(candles)

    def test_later_period_starts_fresh(self):
        results=compare(bars())
        self.assertEqual(results['later'][0]['start_timestamp'],bars()[125].timestamp)
        self.assertEqual(results['later'][0]['final_equity'],1000)
        self.assertEqual(results['later'][1]['completed_trades'],1)

    def test_cost_stress_reduces_equity(self):
        self.assertLess(replay(bars(),'buy_hold',fee_bps=30)['final_equity'],
                        replay(bars(),'buy_hold',fee_bps=10)['final_equity'])

    def test_invalid_settings_rejected(self):
        for settings in ({'allocation':2},{'fee_bps':-1},{'capital':float('nan')},{'start':1}):
            with self.assertRaises(ValueError): replay(bars(),'trend',**settings)

    def test_trend_follows_direction(self):
        rising=[Candle(i*3600000,100+i,101+i,99+i,100+i,10) for i in range(60)]
        falling=[Candle(i*3600000,200-i,201-i,199-i,200-i,10) for i in range(60)]
        self.assertTrue(signal(rising,'trend',False))
        self.assertFalse(signal(falling,'trend',True))

    def test_breakout_requires_volume_and_exits_below_prior_lows(self):
        candles=bars(60)
        candles[-1]=replace(candles[-1],high=103,close=102,volume=20)
        self.assertTrue(signal(candles,'breakout',False))
        candles[-1]=replace(candles[-1],volume=5)
        self.assertFalse(signal(candles,'breakout',False))
        candles[-1]=replace(candles[-1],low=97,close=98)
        self.assertFalse(signal(candles,'breakout',True))


if __name__=='__main__': unittest.main()
