import base64
import json
import os
import tempfile
import time
import unittest
from unittest.mock import patch, Mock
from pathlib import Path
import sys
sys.path.insert(0,str(Path(__file__).parent/'core'))
import config
import operations as ops
import market_research as research
import demo_protection as protection
import status_server as server
import live_trader as live
import risk_engine
from storage import save_json,read_json


def candles():
    return [[i*3600000,100+i,102+i,99+i,101+i,100+i] for i in range(80)]


class UpgradeTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory()
        self.p=patch.object(config,'DATA_DIR',self.tmp.name); self.p.start()
        self.livepath=patch.object(config,'LIVE_PORTFOLIO_FILE',ops.path('live.json')); self.livepath.start()
    def tearDown(self):
        self.livepath.stop(); self.p.stop(); self.tmp.cleanup()
    def test_bad_control_state_fails_closed(self):
        save_json(ops.path('controls.json'),{'pause_buys':'no'})
        self.assertTrue(ops.controls()['pause_buys'])
    def test_pause_blocks_buy_not_sell(self):
        ops.set_pause(True)
        from test_bot import FakeExchange,portfolio
        ex=FakeExchange(); p=portfolio()
        with self.assertRaises(risk_engine.RiskViolation): live.submit_order('BTC','buy',20,p,ex,'test')
        self.assertEqual(ex.orders,[])
        p['positions']={'BTC':{'quantity':.5,'entry_price':100,'entry_time':'test','free_quantity':.5}}
        ex.fill['fees']=[]
        live.submit_order('BTC','sell',.5,p,ex,'stop_loss')
        self.assertEqual(len(ex.orders),1)
    def test_control_route_requires_auth_and_csrf(self):
        client=server.app.test_client()
        auth={'Authorization':'Basic '+base64.b64encode(b'owner:abcdefghijklmnop').decode()}
        with patch.dict(os.environ,{'DASHBOARD_PASSWORD':'abcdefghijklmnop'}):
            self.assertEqual(client.post('/api/controls',json={'pause_buys':True}).status_code,401)
            self.assertEqual(client.post('/api/controls',json={'pause_buys':True},headers=auth).status_code,403)
            headers={**auth,'X-CSRF-Token':server._CONTROL_TOKEN}
            self.assertEqual(client.post('/api/controls',json={'pause_buys':'false'},headers=headers).status_code,400)
            self.assertEqual(client.post('/api/controls',json={'pause_buys':True},headers=headers).status_code,200)
            self.assertTrue(ops.controls()['pause_buys'])
    def test_protected_new_pages(self):
        with patch.dict(os.environ,{'DASHBOARD_PASSWORD':'abcdefghijklmnop'}):
            for url in ('/control-center','/api/overview'):
                self.assertEqual(server.app.test_client().get(url).status_code,401)
    def test_cost_route_and_overview(self):
        auth={'Authorization':'Basic '+base64.b64encode(b'owner:abcdefghijklmnop').decode(),'X-CSRF-Token':server._CONTROL_TOKEN}
        save_json(config.LIVE_PORTFOLIO_FILE,{'created_at':'baseline','starting_value':100})
        with patch.dict(os.environ,{'DASHBOARD_PASSWORD':'abcdefghijklmnop'}),patch.object(config,'TRADING_MODE','live'):
            client=server.app.test_client()
            self.assertEqual(client.post('/api/costs',json={'total_usdt':2},headers=auth).status_code,200)
            self.assertEqual(client.post('/api/costs',json={'total_usdt':-1},headers=auth).status_code,400)
            response=client.get('/api/overview',headers=auth)
            self.assertEqual(response.status_code,200)
            self.assertEqual(response.json['performance']['operating_costs_usdt'],2)
            self.assertIn('stocks',response.json['categories'])
    def test_collection_never_expands_live_coins(self):
        before=list(config.WATCHED_COINS)
        ex=Mock();ex.markets={}
        result=research.collect(ex)
        self.assertEqual(config.WATCHED_COINS,before)
        self.assertEqual(result['markets']['AAPL']['status'],'broker_or_csv_required')
        ex.create_order.assert_not_called()
    def test_candle_excludes_unfinished(self):
        f=research.features(candles(),79*3600000+100)
        self.assertEqual(f['last_closed_ms'],78*3600000)
    def test_candle_gaps_stale_and_bad_range(self):
        rows=candles(); del rows[50]
        with self.assertRaises(ValueError): research.features(rows,80*3600000)
        with self.assertRaises(ValueError): research.features(candles(),84*3600000)
        rows=candles(); rows[-1][3]=999
        with self.assertRaises(ValueError): research.features(rows,80*3600000)
    def test_spread_and_staleness(self):
        self.assertAlmostEqual(research.quote_quality({'bid':99,'ask':101,'timestamp':100000},100000),200)
        for quote in ({'bid':101,'ask':99,'timestamp':100000},{'bid':99,'ask':101,'timestamp':1}):
            with self.assertRaises(ValueError): research.quote_quality(quote,200000)
    def test_group_and_risk_budget(self):
        ex=Mock(); ex.fetch_ticker.return_value={'bid':99.99,'ask':100.01,'timestamp':time.time()*1000,'quoteVolume':2000000}
        p={'cash_usdt':800,'positions':{'ETH':{'quantity':2,'entry_price':100}}}
        self.assertAlmostEqual(research.entry_budget('BTC',p,{'BTC':100,'ETH':100},ex),100)
        p['cash_usdt']=600;p['positions']['ETH']['quantity']=4
        self.assertEqual(research.entry_budget('BTC',p,{'BTC':100,'ETH':100},ex),0)
    def test_liquidity_blocks_missing_volume(self):
        ex=Mock();ex.fetch_ticker.return_value={'bid':100,'ask':100.01,'timestamp':time.time()*1000}
        with self.assertRaises(risk_engine.RiskViolation): research.entry_budget('BTC',{'cash_usdt':100,'positions':{}},{'BTC':100},ex)
    def test_missing_costs_not_zero(self):
        p={'starting_value':100,'created_at':'baseline'}
        r=ops.performance(p,[{'value':110}])
        self.assertEqual(r['trading_pnl_usdt'],10)
        self.assertIsNone(r['net_after_recorded_costs_usdt'])
        save_json(ops.path('operating_costs.json'),{'total_usdt':3,'baseline_created_at':'baseline'})
        self.assertEqual(ops.performance(p,[{'value':110}])['net_after_recorded_costs_usdt'],7)
        p['performance_unavailable']=True
        self.assertIsNone(ops.performance(p,[{'value':1000}])['trading_pnl_usdt'])
    def test_costs_wrong_baseline_rejected(self):
        save_json(ops.path('operating_costs.json'),{'total_usdt':3,'baseline_created_at':'old'})
        self.assertIsNone(ops.performance({'starting_value':100,'created_at':'new'},[{'value':110}])['operating_costs_usdt'])
    def test_benchmark_and_fees_separate(self):
        p={'starting_value':100,'benchmark_prices':{'BTC':100},'confirmed_trades':[{'fees':[{'currency':'BTC','cost':.001}]}]}
        r=ops.performance(p,[{'value':100,'prices':{'BTC':120}}])
        self.assertAlmostEqual(r['benchmark_equal_weight_gross_pct'],20)
        self.assertEqual(r['fees_by_currency'],{'BTC':.001})
    def demo(self):
        ex=Mock();ex.id='okx';ex.headers={'x-simulated-trading':'1'}
        ex.market.return_value={'spot':True,'active':True,'id':'BTC-USDT'}
        ex.amount_to_precision.side_effect=lambda s,v:str(v)
        ex.price_to_precision.side_effect=lambda s,v:str(v)
        return ex
    def test_protection_rejects_live_client(self):
        ex=self.demo();ex.headers={}
        with self.assertRaises(ValueError): protection.submit_oco(ex,ops.path('demo.json'),'BTC/USDT',1,90,120)
        ex.private_post_trade_order_algo.assert_not_called()
    def test_protection_timeout_never_duplicates(self):
        ex=self.demo();ex.private_post_trade_order_algo.side_effect=TimeoutError()
        with self.assertRaises(TimeoutError): protection.submit_oco(ex,ops.path('demo.json'),'BTC/USDT',1,90,120)
        with self.assertRaises(RuntimeError): protection.submit_oco(ex,ops.path('demo.json'),'BTC/USDT',1,90,120)
        self.assertEqual(ex.private_post_trade_order_algo.call_count,1)
        self.assertEqual(read_json(ops.path('demo.json'),{})['status'],'submission_unknown')
    def test_trigger_not_assumed_filled(self):
        ex=self.demo();ex.private_post_trade_order_algo.return_value={'code':'0','data':[{'sCode':'0','algoId':'one'}]}
        state=protection.submit_oco(ex,ops.path('demo.json'),'BTC/USDT',1,90,120)
        ex.private_get_trade_order_algo.return_value={'code':'0','data':[{'algoClOrdId':state['client_id'],'state':'effective','ordIdList':['child']}]}
        result=protection.reconcile(ex,ops.path('demo.json'))
        self.assertEqual(result['status'],'exchange_effective')
        self.assertNotIn('filled',result)
    def test_correlation_alignment(self):
        self.assertAlmostEqual(research.correlation(candles(),candles()),1)
    def test_audit_retains_reason(self):
        ops.record('blocked','BTC','Spread too wide')
        self.assertEqual(read_json(ops.path('audit.json'),[])[0]['reason'],'Spread too wide')


if __name__=='__main__': unittest.main()
