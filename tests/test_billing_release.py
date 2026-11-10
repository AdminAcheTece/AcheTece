import hashlib
import hmac
import unittest
from datetime import datetime, timezone
from unittest.mock import patch
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from billing_release import (
    BillingError, MercadoPagoClient, ProviderError, add_months,
    check_verified_payment, fmt_brl, money_cents, next_expiration,
    price_cents_from_environment, verify_webhook_signature, credential_environment_allowed,
)

class TestEnvironmentCredentialGate(unittest.TestCase):
    def test_mixed_prefix_test_token_requires_operator_confirmation(self):
        self.assertFalse(credential_environment_allowed('APP_USR-TEST-EXAMPLE', 'sandbox'))
        self.assertTrue(credential_environment_allowed('APP_USR-TEST-EXAMPLE', 'sandbox',
                                                       test_credential_confirmed=True))
    def test_explicit_test_prefix(self):
        self.assertTrue(credential_environment_allowed('TEST-EXAMPLE', 'sandbox'))
    def test_live_mode_needs_valid_prefix(self):
        self.assertTrue(credential_environment_allowed('APP_USR-LIVE-EXAMPLE', 'production'))
        self.assertFalse(credential_environment_allowed('TEST-EXAMPLE', 'production'))
    def test_invalid_mode_is_closed(self):
        self.assertFalse(credential_environment_allowed('APP_USR-EXAMPLE', ''))
        self.assertFalse(credential_environment_allowed('APP_USR-EXAMPLE', 'production-test'))


class TestMoney(unittest.TestCase):
    def test_precision_brl(self):
        self.assertEqual(money_cents('47.00'),4700)
        self.assertEqual(money_cents('479.40'),47940)
        self.assertEqual(fmt_brl(47940),'479,40')
    def test_negative_nan_and_inexact(self):
        for bad in ('0','-10','nan','inf','1.001','',None):
            with self.subTest(bad=bad), self.assertRaises(BillingError):
                money_cents(bad)
    def test_no_silent_price_default(self):
        with self.assertRaises(BillingError):
            price_cents_from_environment({},'mensal')
        self.assertEqual(price_cents_from_environment({'ACHETECE_PRICE_YEARLY_BRL':'479.40'},'anual'),47940)

class TestPeriods(unittest.TestCase):
    def test_end_month(self):
        self.assertEqual(next_expiration(datetime(2027,1,31), 'mensal'),datetime(2027,2,28))
    def test_leap_year(self):
        self.assertEqual(next_expiration(datetime(2028,2,29), 'anual'),datetime(2029,2,28))
    def test_invalid_plan(self):
        with self.assertRaises(BillingError): next_expiration(datetime(2027,1,1),'semanal')

class TestSignature(unittest.TestCase):
    def setUp(self):
        self.now = datetime(2026,11,10,12,0,0,tzinfo=timezone.utc)
        self.timestamp=str(int(self.now.timestamp()*1000))
        self.secret='webhook-secret-test'
        self.request_id='test-123'
        self.data_id='123456'
        self.message=f'id:{self.data_id};request-id:{self.request_id};ts:{self.timestamp};'
        self.sig='ts='+self.timestamp+',v1='+hmac.new(self.secret.encode(),self.message.encode(),hashlib.sha256).hexdigest()
    def test_valid(self):
        self.assertTrue(verify_webhook_signature(self.sig,self.request_id,self.data_id,self.secret,now=self.now))
    def test_wrong_key_id(self):
        self.assertFalse(verify_webhook_signature(self.sig,self.request_id,self.data_id,'other',now=self.now))
        self.assertFalse(verify_webhook_signature(self.sig,self.request_id,'1',self.secret,now=self.now))
    def test_missing_fields(self):
        self.assertFalse(verify_webhook_signature('',self.request_id,self.data_id,self.secret,now=self.now))
    def test_replay_outside_window(self):
        later=datetime(2026,11,10,13,0,0,tzinfo=timezone.utc)
        self.assertFalse(verify_webhook_signature(self.sig,self.request_id,self.data_id,self.secret,now=later))

class TestPaymentReconciliation(unittest.TestCase):
    def setUp(self):
        self.pay={
            'id':123456,
            'status':'approved',
            'external_reference':'achetece-v1-aaa',
            'collector_id':999,
            'currency_id':'BRL',
            'transaction_amount':47.0,
            'preapproval_id':'sub1234',
            'live_mode':False,
        }
        self.args=dict(expected_payment_id='123456',expected_reference='achetece-v1-aaa',
                       expected_cents=4700,expected_collector='999',expected_subscription='sub1234',expected_live_mode=False)
    def test_valid_approved(self):
        self.assertEqual(check_verified_payment(self.pay,**self.args),'approved')
    def test_pending_does_not_activate(self):
        self.pay['status']='pending'
        self.assertEqual(check_verified_payment(self.pay,**self.args),'pending')
    def test_reject_wrong_identity(self):
        keys={
            'id':42, 'external_reference':'achetece-v1-other','collector_id':8,
            'currency_id':'USD','transaction_amount':2.00,'preapproval_id':'wrong-sub',
            'live_mode':True,
        }
        for key,val in keys.items():
            with self.subTest(key=key):
                p=dict(self.pay);p[key]=val
                with self.assertRaises(BillingError):check_verified_payment(p,**self.args)
    def test_missing_identity(self):
        for key in ('id','external_reference','collector_id','currency_id','transaction_amount','live_mode'):
            with self.subTest(key=key):
                p=dict(self.pay);p.pop(key)
                with self.assertRaises(BillingError):check_verified_payment(p,**self.args)

class MockResponse:
    def raise_for_status(self):pass
    def json(self):return {'id':'sub1234','status':'pending','init_point':'https://www.mercadopago.com.br/subscriptions/checkout?preapproval_id=sub1234'}

class TestMercadoPagoClient(unittest.TestCase):
    def test_monthly_uses_preapproval(self):
        with patch('billing_release.requests.request',return_value=MockResponse()) as req:
            client=MercadoPagoClient('TEST-FAKE')
            data=client.create_monthly_subscription(email='cliente@example.com',reference='ref-123',cents=4700,return_url='https://test.example.com/pagamento_pendente')
            self.assertEqual(data['status'],'pending')
            args,kwargs=req.call_args
            self.assertEqual(args[:2],('POST','https://api.mercadopago.com/preapproval'))
            self.assertEqual(kwargs['json']['auto_recurring']['frequency_type'],'months')
            self.assertEqual(kwargs['json']['status'],'pending')
            self.assertEqual(kwargs['headers']['X-Idempotency-Key'],'ref-123')
    def test_no_bad_provider_path(self):
        with self.assertRaises(BillingError):
            MercadoPagoClient('TEST-FAKE')._request('GET','https://evil.example.com')

if __name__ == '__main__': unittest.main()
