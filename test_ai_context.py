import sys
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).parent/'core'))
from ai_decision import build_prompt, portfolio_context, provider_failure_reason, safe_failure_reason


class ContextTests(unittest.TestCase):
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
