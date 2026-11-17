import sys
import unittest
from unittest.mock import Mock, patch
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'core'))
from ai_decision import build_prompt, portfolio_context, provider_failure_reason, safe_failure_reason, get_decisions


class ContextTests(unittest.TestCase):
    def test_gemini_uses_public_context_and_validates_decisions(self):
        response = Mock(status_code=200)
        response.json.return_value = {"candidates": [{"finishReason": "STOP", "content": {"parts": [
            {"text": '[{"coin":"BTC","action":"hold","confidence":0.2,"reasoning":"Sparse news"}]'}]}}]}
        with patch.dict('os.environ', {'AI_PROVIDER': 'gemini', 'GEMINI_API_KEY': 'fake-key'}), \
                patch('ai_decision.config.TRADING_MODE', 'paper'), \
                patch('ai_decision.requests.post', return_value=response) as request, \
                patch('ai_decision._log_decisions') as log:
            decisions = get_decisions({'GENERAL': [{'title': 'Public headline'}]}, {'BTC': 100},
                                      {'cash_usdt': 987654321, 'positions': {}})
            self.assertEqual(decisions[0]['action'], 'hold')
            body = str(request.call_args.kwargs['json'])
            self.assertIn('Public headline', body)
            self.assertNotIn('987654321', body)
            self.assertFalse(request.call_args.kwargs['allow_redirects'])
            response.json.return_value['candidates'][0]['content']['parts'][0]['text'] = (
                '[{"coin":"ETH","action":"buy","confidence":0.9,"reasoning":"Wrong symbol"}]')
            with self.assertRaisesRegex(RuntimeError, 'failed validation'):
                get_decisions({'GENERAL': [{'title': 'Public headline'}]}, {'BTC': 100}, {})
            log.assert_called_once()

    def test_gemini_cannot_run_in_live_mode(self):
        with patch.dict('os.environ', {'AI_PROVIDER': 'gemini'}), \
                patch('ai_decision.config.TRADING_MODE', 'live'), \
                patch('ai_decision.requests.post') as request:
            with self.assertRaisesRegex(RuntimeError, 'practice mode only'):
                get_decisions({}, {}, {})
            request.assert_not_called()

    def test_gemini_quota_failure_does_not_fall_back_to_anthropic(self):
        with patch.dict('os.environ', {'AI_PROVIDER': 'gemini', 'GEMINI_API_KEY': 'fake-key'}), \
                patch('ai_decision.config.TRADING_MODE', 'paper'), \
                patch('ai_decision.requests.post', return_value=Mock(status_code=429)), \
                patch('ai_decision._anthropic_response') as fallback:
            with self.assertRaisesRegex(RuntimeError, 'rate limit'):
                get_decisions({'GENERAL': [{'title': 'Public headline'}]}, {'BTC': 100}, {})
            fallback.assert_not_called()

    def test_credit_failure_is_actionable_without_echoing_provider_text(self):
        error = RuntimeError("Your credit balance is too low PRIVATE_SECRET")
        error.status_code = 400
        reason = provider_failure_reason(error)
        self.assertIn("insufficient credit", reason)
        self.assertNotIn("PRIVATE_SECRET", reason)
        self.assertEqual(safe_failure_reason(RuntimeError(reason)), reason)

    def test_unrecognized_failure_does_not_expose_sensitive_text(self):
        self.assertEqual(safe_failure_reason(RuntimeError("PRIVATE_SECRET")),
                         "Analysis unavailable (RuntimeError)")
        error = RuntimeError("PRIVATE_SECRET")
        error.status_code = 401
        self.assertIn("authentication failed", provider_failure_reason(error))
        self.assertNotIn("PRIVATE_SECRET", provider_failure_reason(error))

    def test_journal_growth_does_not_grow_prompt(self):
        p={'cash_usdt':100,'positions':{}}
        before=portfolio_context(p,{'BTC':100},'2026-10-02')
        p['confirmed_trades']=[{'reason':'historical text','order_id':'PRIVATE_ID'}]*10000
        p['trades_today']={'2020-01-01':100}
        self.assertEqual(before,portfolio_context(p,{'BTC':100},'2026-10-02'))
        self.assertNotIn('PRIVATE_ID',build_prompt({}, {'BTC':100},p))

    def test_preserves_current_exposure_and_restrictions(self):
        p={'cash_usdt':100,'free_cash_usdt':90,'external_change_requires_review':True,
           'positions':{'BTC':{'quantity':.1,'entry_price':100,'free_quantity':.08,'private':'omit'}},
           'pending_orders':[{'client_id':'PRIVATE_ID'}], 'trades_today':{'2026-10-02':3}}
        c=portfolio_context(p,{'BTC':110},'2026-10-02')
        self.assertEqual(c['positions']['BTC'],{'quantity':.1,'entry_price':100,'free_quantity':.08})
        self.assertEqual(c['trades_today'],3)
        self.assertEqual(c['pending_order_count'],1)
        self.assertTrue(c['external_change_requires_review'])
        self.assertEqual(c['free_cash_usdt'],90)


if __name__=='__main__': unittest.main()
