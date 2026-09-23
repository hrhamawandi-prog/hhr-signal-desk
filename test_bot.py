"""Regression tests; all exchange interactions are simulated, never live."""
import base64
import copy
import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch, Mock
sys.path.insert(0, str(Path(__file__).parent / "core"))
import config
import risk_engine as risk
import live_trader as live
import status_server
import ai_decision
from storage import read_json, save_json, read_tail


def portfolio():
    return {"cash_usdt": 1000.0, "free_cash_usdt": 1000.0, "positions": {},
            "starting_value": 1000.0, "trades_today": {}, "pending_orders": []}


def decision(action="buy", coin="BTC", confidence=0.9):
    return {"coin": coin, "action": action, "confidence": confidence, "reasoning": "test"}


class FakeExchange:
    def __init__(self):
        self.orders = []
        self.status = "closed"
        self.balance = {"USDT": {"free": 1000.0, "total": 1000.0}}
        self.fill = {"id": "order1", "status": "closed", "filled": 0.5, "cost": 50.0,
                     "fees": [{"currency": "BTC", "cost": 0.001}], "datetime": datetime.now(timezone.utc).isoformat()}
    def market(self, symbol):
        return {"spot": True, "active": True, "limits": {"amount": {"min": 0.0001}, "cost": {"min": 5}}}
    def cost_to_precision(self, symbol, amount): return str(round(amount, 2))
    def amount_to_precision(self, symbol, amount): return str(round(amount, 6))
    def create_market_buy_order_with_cost(self, symbol, amount, params):
        self.orders.append((symbol, amount, params))
        return {"id": "order1", "filled": None}
    def create_market_sell_order(self, symbol, amount, params):
        self.orders.append((symbol, amount, params))
        return {"id": "order1", "filled": None}
    def fetch_order(self, order_id, symbol, params):
        return dict(self.fill, status=self.status)
    def fetch_balance(self): return self.balance


class RegressionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.patches = []
        for key in ("LIVE_PORTFOLIO_FILE", "PORTFOLIO_FILE", "LIVE_TRADES_LOG", "TRADES_LOG", "DECISIONS_LOG", "HEALTH_FILE", "LIVE_PORTFOLIO_HISTORY_LOG", "PORTFOLIO_HISTORY_LOG"):
            patcher = patch.object(config, key, str(Path(self.tmp.name) / key))
            patcher.start(); self.patches.append(patcher)
    def tearDown(self):
        for patcher in self.patches: patcher.stop()
        self.tmp.cleanup()
    def test_position_cap_includes_existing_holdings(self):
        p = portfolio(); p["cash_usdt"] = 920
        p["positions"] = {"BTC": {"quantity": 0.8, "entry_price": 100}}
        self.assertEqual(risk.check_position_size("BTC", 100, p, {"BTC": 100}), 20)
    def test_full_position_blocks_additional_buy(self):
        p = portfolio(); p["cash_usdt"] = 900
        p["positions"] = {"BTC": {"quantity": 1, "entry_price": 100}}
        self.assertEqual(risk.check_position_size("BTC", 100, p, {"BTC": 100}), 0)
    def test_daily_loss_uses_market_value(self):
        p = portfolio(); p.update(cash_usdt=0, day_start_value=1000)
        p["positions"] = {"BTC": {"quantity": 10, "entry_price": 100}}
        with self.assertRaises(risk.RiskViolation): risk.validate_trade(decision(), p, prices={"BTC": 90})
    def test_day_baseline_rolls_over(self):
        p = portfolio(); p.update(risk_day="2000-01-01", day_start_value=123)
        risk.start_day(p, {})
        self.assertEqual(p["day_start_value"], 1000)
        p["cash_usdt"] = 950; risk.start_day(p, {})
        self.assertEqual(p["day_start_value"], 1000)
    def test_sell_not_blocked_by_loss_or_trade_limit(self):
        p = portfolio(); p.update(cash_usdt=1, day_start_value=1000)
        p["trades_today"][datetime.now(timezone.utc).date().isoformat()] = 100
        self.assertEqual(risk.validate_trade(decision("sell"), p)["action"], "sell")
    def test_invalid_confidence_rejected(self):
        for value in (float("nan"), float("inf"), -1, 2, True, "0.9", None):
            with self.subTest(value=value), self.assertRaises(risk.RiskViolation):
                risk.validate_decision(decision(confidence=value))
    def test_unknown_action_and_symbol_rejected(self):
        for d in (decision("withdraw"), decision(coin="UNKNOWN"), {}):
            with self.assertRaises(risk.RiskViolation): risk.validate_decision(d)
    def test_acknowledgement_not_treated_as_fill(self):
        p = portfolio(); ex = FakeExchange(); ex.status = "open"
        live.submit_order("BTC", "buy", 50, p, ex, "test")
        self.assertEqual(p["cash_usdt"], 1000)
        self.assertEqual(p["positions"], {})
        self.assertEqual(len(p["pending_orders"]), 1)
    def test_pending_order_prevents_duplicate_submission(self):
        p = portfolio(); ex = FakeExchange(); ex.status = "open"
        live.submit_order("BTC", "buy", 50, p, ex, "test")
        live.submit_order("BTC", "buy", 50, p, ex, "test")
        self.assertEqual(len(ex.orders), 1)
    def test_restart_recovers_confirmed_fill_once_with_fee(self):
        p = portfolio(); ex = FakeExchange(); ex.status = "open"
        live.submit_order("BTC", "buy", 50, p, ex, "test")
        recovered = live.load_portfolio(); ex.status = "closed"
        live.reconcile_orders(recovered, ex); live.reconcile_orders(recovered, ex)
        self.assertEqual(recovered["cash_usdt"], 950)
        self.assertAlmostEqual(recovered["positions"]["BTC"]["quantity"], .499)
        self.assertEqual(len(recovered["confirmed_trades"]), 1)
        self.assertEqual(live.load_portfolio(), recovered)
    def test_submission_timeout_retains_intent(self):
        p = portfolio(); ex = FakeExchange()
        ex.create_market_buy_order_with_cost = Mock(side_effect=TimeoutError())
        with self.assertRaises(TimeoutError): live.submit_order("BTC", "buy", 50, p, ex, "test")
        recovered = live.load_portfolio()
        self.assertEqual(len(recovered["pending_orders"]), 1)
        self.assertNotIn("id", recovered["pending_orders"][0])
        live.reconcile_orders(recovered, ex)
        self.assertEqual(len(recovered["confirmed_trades"]), 1)
    def test_explicit_rejection_clears_intent(self):
        p = portfolio(); ex = FakeExchange()
        ex.create_market_buy_order_with_cost = Mock(side_effect=live.ccxt.InsufficientFunds())
        with self.assertRaises(RuntimeError): live.submit_order("BTC", "buy", 50, p, ex, "test")
        self.assertFalse(live.load_portfolio()["pending_orders"])
    def test_partial_canceled_sell_keeps_remaining_position(self):
        p = portfolio(); p["positions"] = {"BTC": {"quantity": 1, "entry_price": 80, "free_quantity": 1}}
        ex = FakeExchange(); ex.status = "canceled"; ex.fill["fees"] = [{"currency": "USDT", "cost": 1}]
        live.submit_order("BTC", "sell", 1, p, ex, "test")
        self.assertEqual(p["positions"]["BTC"]["quantity"], .5)
        self.assertEqual(p["cash_usdt"], 1049)
        self.assertEqual(p["confirmed_trades"][0]["pnl_usdt"], 9)
    def test_invalid_confirmed_fill_does_not_mutate_portfolio(self):
        p = portfolio(); ex = FakeExchange(); ex.status = "open"
        live.submit_order("BTC", "buy", 50, p, ex, "test")
        previous = copy.deepcopy(p); ex.status = "closed"; ex.fill["cost"] = None
        with self.assertRaises(risk.RiskViolation): live.reconcile_orders(p, ex)
        self.assertEqual(p, previous)
    def test_balance_refresh_and_external_change_notice(self):
        p = portfolio(); ex = FakeExchange(); ex.balance["USDT"] = {"total": 1200, "free": 1100}
        live.sync_balance(p, ex, {})
        self.assertEqual(p["cash_usdt"], 1200)
        self.assertEqual(p["free_cash_usdt"], 1100)
        self.assertTrue(p["external_change_requires_review"])
    def test_initial_zero_balance_is_preserved(self):
        p = portfolio(); p["starting_value"] = None; ex = FakeExchange()
        ex.balance = {"USDT": {"total": 0, "free": 0}}
        live.sync_balance(p, ex, {})
        self.assertEqual(p["starting_value"], 0)
        self.assertFalse(p.get("external_change_requires_review", False))
    def test_first_real_funding_is_baseline_not_profit(self):
        p = portfolio(); p.update(cash_usdt=0, starting_value=0, day_start_value=0)
        ex = FakeExchange()
        live.sync_balance(p, ex, {})
        self.assertEqual(p["starting_value"], 1000)
        self.assertEqual(p["day_start_value"], 1000)
        self.assertFalse(p.get("external_change_requires_review", False))
    def test_protective_cycle_does_not_call_ai(self):
        import main
        p = portfolio()
        with patch.object(config, "TRADING_MODE", "live"), patch.object(main.live_trader, "load_portfolio", return_value=p), patch.object(main.live_trader, "get_prices", return_value={"BTC":100}), patch.object(main.live_trader, "process_decisions") as process, patch.object(main.live_trader, "log_portfolio_snapshot"), patch.object(main.ai_decision, "get_decisions") as ai:
            main.run_cycle()
            process.assert_called_once_with([], {"BTC":100}, p)
            ai.assert_not_called()
    def test_news_filters_old_duplicate_and_undated_items(self):
        import news_fetcher
        fresh = {"title":"Fresh", "published_at":datetime.now(timezone.utc).isoformat()}
        items = [fresh, fresh, {"title":"Old", "published_at":"2000-01-01T00:00:00Z"}, {"title":"Undated"}]
        self.assertEqual(len(news_fetcher.recent_unique(items)), 1)
    def test_no_news_does_not_invoke_ai(self):
        with patch.object(ai_decision.anthropic, "Anthropic") as client:
            with self.assertRaises(RuntimeError): ai_decision.get_decisions({}, {"BTC":100}, portfolio())
            client.assert_not_called()
    def test_invalid_market_price_blocks_balance_sync(self):
        p = portfolio(); ex = FakeExchange(); ex.balance["BTC"] = {"total": 1, "free": 1}
        with self.assertRaises(risk.RiskViolation): live.sync_balance(p, ex, {})
    def test_password_required_for_dashboard_and_status(self):
        client = status_server.app.test_client()
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "a-test-password-long-enough"}):
            for path in ("/", "/status"):
                self.assertEqual(client.get(path).status_code, 401)
            self.assertEqual(client.get("/healthz").status_code, 200)
    def test_missing_password_locks_dashboard(self):
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": ""}):
            self.assertEqual(status_server.app.test_client().get("/").status_code, 503)
    def test_authenticated_status_preserves_zero_and_marks_stale(self):
        p = portfolio(); p["starting_value"] = 0; live.save_portfolio(p)
        auth = base64.b64encode(b"owner:a-test-password-long-enough").decode()
        with patch.dict(os.environ, {"DASHBOARD_PASSWORD": "a-test-password-long-enough"}), patch.object(config, "TRADING_MODE", "live"):
            response = status_server.app.test_client().get("/status", headers={"Authorization": "Basic " + auth})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["starting_balance"], 0)
        self.assertFalse(response.json["health"]["healthy"])
        self.assertNotIn("Access-Control-Allow-Origin", response.headers)
    def test_truncated_log_does_not_break_status(self):
        path = str(Path(self.tmp.name) / "tail")
        Path(path).write_text('{"a":1}\n{"a":2}\n{"a":')
        self.assertEqual(read_tail(path, 3), [{"a": 1}, {"a": 2}])
    def test_atomic_write_rejects_nan_without_destroying_previous_state(self):
        path = str(Path(self.tmp.name) / "state")
        save_json(path, {"a": 1})
        with self.assertRaises(ValueError): save_json(path, {"a": float("nan")})
        self.assertEqual(read_json(path, {}), {"a": 1})
    def test_model_rejects_duplicate_symbols(self):
        response = Mock(content=[Mock(type="text", text=json.dumps([decision(), decision()]))])
        with patch.object(ai_decision.anthropic, "Anthropic") as client:
            client.return_value.messages.create.return_value = response
            with self.assertRaises(RuntimeError): ai_decision.get_decisions({"GENERAL": [{"title": "test"}]}, {"BTC":100}, portfolio())
    def test_model_rejects_non_list(self):
        response = Mock(content=[Mock(type="text", text='{"coin":"BTC"}')])
        with patch.object(ai_decision.anthropic, "Anthropic") as client:
            client.return_value.messages.create.return_value = response
            with self.assertRaises(RuntimeError): ai_decision.get_decisions({"GENERAL": [{"title": "test"}]}, {"BTC":100}, portfolio())


if __name__ == "__main__":
    unittest.main()
