"""Regression tests: HML legacy Checkout Pro test buyer mode, fail closed."""
import ast
import sys
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from billing_release import BillingError,check_verified_payment

class TestHmlTestBuyerMode(unittest.TestCase):
    def setUp(self):
        self.payment={'id':187822707179,'external_reference':'achetece-v1-ref',
            'collector_id':98765,'currency_id':'BRL','transaction_amount':479.40,
            'status':'approved','live_mode':True,'payer':{'id':2564853871}}
        self.args=dict(expected_payment_id='187822707179',
            expected_reference='achetece-v1-ref',expected_cents=47940,
            expected_collector='98765',expected_live_mode=False,
            expected_hml_test_buyer_id='2564853871')
    def test_approved_matching_test_buyer_in_hml(self):
        self.assertEqual(check_verified_payment(self.payment,**self.args),'approved')
    def test_no_buyer_exception_fails_closed(self):
        with self.assertRaisesRegex(BillingError,'Ambiente'):
            check_verified_payment(self.payment,**{**self.args,'expected_hml_test_buyer_id':None})
    def test_wrong_buyer_fails(self):
        for value in ('2564853872','','TESTUSER',None):
            with self.subTest(value=value),self.assertRaises(BillingError):
                check_verified_payment(self.payment,**{**self.args,'expected_hml_test_buyer_id':value})
    def test_missing_buyer_information_fails(self):
        for payer in (None,{}, {'id':None}, {'id':2564853872},'2564853871'):
            with self.subTest(payer=payer),self.assertRaises(BillingError):
                check_verified_payment({**self.payment,'payer':payer},**self.args)
    def test_live_mode_missing_or_malformed_fails(self):
        for mode in (None,'true',1,0,'false'):
            with self.subTest(mode=mode),self.assertRaises(BillingError):
                check_verified_payment({**self.payment,'live_mode':mode},**self.args)
    def test_wrong_payment_identity_never_accepted(self):
        invalid={'id':123,'external_reference':'other','collector_id':2222,
            'currency_id':'USD','transaction_amount':47.0}
        for field,value in invalid.items():
            with self.subTest(field=field),self.assertRaises(BillingError):
                check_verified_payment({**self.payment,field:value},**self.args)
    def test_nonapproved_cannot_activate(self):
        for st in ('pending','rejected','in_process','cancelled'):
            self.assertEqual(check_verified_payment({**self.payment,'status':st},**self.args),st)
    def test_production_remains_strict(self):
        with self.assertRaises(BillingError):
            check_verified_payment(self.payment,**{**self.args,'expected_live_mode':False,'expected_hml_test_buyer_id':None})
        self.assertEqual(check_verified_payment(self.payment,**{**self.args,'expected_live_mode':True,'expected_hml_test_buyer_id':None}),'approved')
    def test_main_hml_guard_and_env_present(self):
        s=(ROOT/'main.py').read_text(encoding='utf-8')
        t=ast.parse(s)
        p=next(x for x in t.body if isinstance(x,ast.FunctionDef) and x.name=='_processar_pagamento')
        body=ast.get_source_segment(s,p)
        self.assertIn("ACHETECE_HML_EXPECTED_DB_NAME",body)
        self.assertIn("ACHETECE_MP_TEST_TOKEN_CONFIRMED",body)
        self.assertIn("ACHETECE_HML_TEST_BUYER_USER_ID",body)
        self.assertIn("expected_hml_test_buyer_id=hml_test_buyer",body)

if __name__=='__main__': unittest.main()
