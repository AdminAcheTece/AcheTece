"""ED02.3G.7 - testes isolados, sem rede, DB, Flask ou credenciais."""
import unittest
from ed02_financial_guard import validate_financial_payment, FinancialValidationError

REF = 'achetece:1:0123456789abcdef0123456789abcdef'
BASE = dict(id=123456, external_reference=REF, status='approved',
            currency_id='BRL', transaction_amount= '2.00')
KW = dict(company_id=1, payment_id='123456', expected_reference=REF,
          plan='mensal', monthly_price='2.00', yearly_price='24.00')
class FinancialGuardTests(unittest.TestCase):
    def valid(self, changes=None, **kw):
        return validate_financial_payment({**BASE, **(changes or {})}, **{**KW, **kw})
    def denied(self, changes=None, **kw):
        with self.assertRaises(FinancialValidationError):
            self.valid(changes, **kw)
    def test_monthly_valid(self): self.assertEqual(self.valid()['valor'], 2)
    def test_yearly_valid(self): self.assertEqual(self.valid({'transaction_amount':'24.00'},plan='anual')['plano'],'anual')
    def test_payment_id_mismatch(self): self.denied({'id':'123457'})
    def test_company_mismatch(self): self.denied(company_id=2)
    def test_expected_reference_mismatch(self): self.denied(expected_reference='achetece:1:ffffffffffffffffffffffffffffffff')
    def test_wrong_amount(self): self.denied({'transaction_amount':'1.99'})
    def test_overpaid_amount(self): self.denied({'transaction_amount':'2.01'})
    def test_wrong_currency(self): self.denied({'currency_id':'USD'})
    def test_not_approved(self): self.denied({'status':'pending'})
    def test_refunded(self): self.denied({'transaction_amount_refunded':'1.00'})
    def test_invalid_plan(self): self.denied(plan='semestral')
    def test_nan(self): self.denied({'transaction_amount':'NaN'})
    def test_precision(self): self.denied({'transaction_amount':'2.001'})
    def test_missing_reference(self): self.denied({'external_reference':''})
    def test_bad_reference_format(self): self.denied({'external_reference':'achetece:1:foo'})
    def test_no_approved_without_provider_dict(self):
        with self.assertRaises(FinancialValidationError): validate_financial_payment(None, **KW)
if __name__=='__main__': unittest.main(verbosity=2)
