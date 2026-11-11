"""V1.5 -- mensalidade: fatura assinada, API autenticada e associacao sem atalhos.

Testa o fluxo de invoice sem precisar do servidor ou credenciais Mercado Pago.
"""
import hashlib
import hmac
import logging
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from billing_release import (BillingError, MercadoPagoClient, check_verified_payment,
                             verify_authorized_invoice, verify_webhook_signature)
from test_webhook_v14 import Harness, EXEC

INVOICE = {
 'id': 6114264375, 'preapproval_id': 'preapproval-HML-ABC123',
 'external_reference':'achetece-v1-invoice-hml-123',
 'currency_id':'BRL', 'transaction_amount':'47.00',
 'payment':{'id':187800000001, 'status':'approved'},
}

class InvoiceTests(unittest.TestCase):
    def validate(self,obj):
        return verify_authorized_invoice(obj, invoice_id='6114264375',
           subscription_id='preapproval-HML-ABC123',
           reference='achetece-v1-invoice-hml-123',
           payment_id='187800000001', cents=4700)
    def test_valid_invoice(self):self.assertTrue(self.validate(INVOICE))
    def test_invoice_id_mismatch(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'id':3})
    def test_subscription_mismatch(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'preapproval_id':'another'})
    def test_reference_mismatch(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'external_reference':'another'})
    def test_missing_reference(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'external_reference':None})
    def test_currency_mismatch(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'currency_id':'USD'})
    def test_amount_mismatch(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'transaction_amount':46.99})
    def test_payment_id_mismatch(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'payment':{'id':101,'status':'approved'}})
    def test_payment_not_approved(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'payment':{'id':187800000001,'status':'pending'}})
    def test_payment_missing(self):
        with self.assertRaises(BillingError):self.validate({**INVOICE,'payment':None})
    def test_payment_missing_reference_requires_verified_invoice(self):
        p={'id':187800000001,'external_reference':None,'collector_id':2569815840,
           'currency_id':'BRL','transaction_amount':47,'status':'approved','live_mode':False}
        kw={'expected_payment_id':'187800000001','expected_reference':'achetece-v1-invoice-hml-123',
            'expected_cents':4700,'expected_collector':'2569815840','expected_live_mode':False}
        with self.assertRaises(BillingError):check_verified_payment(p,**kw)
        self.assertEqual(check_verified_payment(p,verified_invoice_reference=kw['expected_reference'],**kw),'approved')
        with self.assertRaises(BillingError):
            check_verified_payment({**p,'external_reference':'some_other_contract'},verified_invoice_reference=kw['expected_reference'],**kw)
    def test_mp_client_can_get_authorized_invoice_by_safe_numeric_id_only(self):
        client=MercadoPagoClient('TEST-NOT-REAL')
        with patch('billing_release.requests.request') as req:
            req.return_value.raise_for_status.return_value=None
            req.return_value.json.return_value={'id':6114264375}
            result=client.get_authorized_payment('6114264375')
            self.assertEqual(result['id'],6114264375)
            self.assertEqual(req.call_args.args[:2],('GET','https://api.mercadopago.com/authorized_payments/6114264375'))
        with self.assertRaises(BillingError):client.get_authorized_payment('../../etc/passwd')

class WebhookMonthlyTests(unittest.TestCase):
    def signed(self, *, invoice=None, invalid_signature=False, paid=True, failure=None):
        id='6114264375'
        now=str(int(datetime.now(timezone.utc).timestamp()*1000))
        request_id='safe-request-v15'
        key='test-only-fake-secret'
        manifest=f'id:{id};request-id:{request_id};ts:{now};'
        mac=hmac.new(key.encode(),manifest.encode(),hashlib.sha256).hexdigest()
        sig=f'ts={now},v1={mac}'
        if invalid_signature: sig=f'ts={now},v1={"0"*64}'
        h=Harness(query={'data.id':id,'type':'subscription_authorized_payment'},
           body={'type':'subscription_authorized_payment','data':{'id':id}},
           signature=sig,request_id=request_id,env={'MP_WEBHOOK_SECRET':key})
        self.calls=[]
        self.client=Mock()
        self.client.get_authorized_payment.return_value= invoice if invoice is not None else (
             INVOICE if paid else {**INVOICE,'payment':{'id':187800000001,'status':'pending'}})
        if failure:self.client.get_authorized_payment.side_effect=failure
        def process(pid,*,invoice_id=None):
            self.calls.append((pid,invoice_id))
            return {'aplicado':True}
        h.process=process
        def run():
            import re
            from billing_release import is_legacy_ipn_payment_hint, hml_legacy_ipn_hint_enabled
            loc={
                'request':h.request,'db':h.db,'app':h.app,
                'os':type('O',(),{'environ':h.env,'getenv':lambda obj,k,default='':h.env.get(k,default)})(),
                're':re,'jsonify':lambda obj:obj,
                '_release_billing_ready':lambda:True,'_env_bool':lambda *args:True,
                'is_legacy_ipn_payment_hint':is_legacy_ipn_payment_hint,
                'hml_legacy_ipn_hint_enabled':hml_legacy_ipn_hint_enabled,
                'verify_webhook_signature':verify_webhook_signature,
                '_processar_pagamento':h.process,
                '_release_mp_client':lambda:self.client,
                'BillingError':BillingError,'BillingIntent':Mock(),
            }
            exec(EXEC,loc)
            return loc['webhook']()
        run.h = h
        return run
    def test_valid_signed_invoice_activates_with_verified_invoice_id(self):
        resp,code=self.signed()()
        self.assertEqual(code,200)
        self.assertEqual(resp['result'],'activated')
        self.assertEqual(self.calls,[('187800000001','6114264375')])
    def test_invoice_not_paid_no_activation(self):
        resp,code=self.signed(paid=False)()
        self.assertEqual((code,resp['result']),(200,'unchanged'))
        self.assertEqual(self.calls,[])
    def test_invalid_signature_no_api_call_or_activation(self):
        resp,code=self.signed(invalid_signature=True)()
        self.assertEqual(code,401)
        self.client.get_authorized_payment.assert_not_called()
        self.assertEqual(self.calls,[])
    def test_invalid_paid_info_no_activation(self):
        resp,code=self.signed(invoice={**INVOICE,'payment':{}})()
        self.assertEqual(code,200)
        self.assertEqual(self.calls,[])
    def test_api_failure_retriable_without_activation(self):
        resp,code=self.signed(failure=RuntimeError('provider offline'))()
        self.assertEqual(code,503)
        self.assertEqual(self.calls,[])
    def test_signed_invoice_duplicate_returns_200(self):
        run=self.signed()
        def process(pid,*,invoice_id=None):
            self.calls.append((pid,invoice_id))
            return {'duplicado':True}
        run.h.process=process
        resp,code=run()
        self.assertEqual((code,resp['result']),(200,'duplicate'))
        self.assertEqual(self.calls,[('187800000001','6114264375')])

if __name__=='__main__': unittest.main()
