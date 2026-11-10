"""Regression tests of real webhook function, using a lightweight Flask-free harness.

The production route source is compiled, with decorators omitted, and exercised
with dummy request/response objects. No network/database or MP credentials.
"""
import ast
import hashlib
import hmac
import logging
import os
import re
import sys
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT))
from billing_release import (BillingError, verify_webhook_signature, is_legacy_ipn_payment_hint,
                             hml_legacy_ipn_hint_enabled)
SOURCE = (ROOT/'main.py').read_text(encoding='utf-8')
TREE = ast.parse(SOURCE)
WH = next(x for x in TREE.body if isinstance(x,ast.FunctionDef) and x.name=='webhook')
WH.decorator_list = []
EXEC = compile(ast.Module(body=[WH],type_ignores=[]),'app_webhook_route','exec')

class Harness:
    def __init__(self, *, query=None, body=None, signature='',request_id='', env=None,
                 provider_result=None, provider_error=None):
        self.calls=[]
        self.args=query or {}
        self.body={} if body is None else body
        self.headers={'X-Signature':signature,'X-Request-Id':request_id}
        self.env={}
        self.env.update(env or {})
        self.provider_result=provider_result or {'aplicado':True}
        self.provider_error=provider_error
        self.logger=logging.getLogger('test_hml_webhook')
        self.rollback=Mock()
        self.app=type('App',(),{'logger':self.logger})()
        self.request=type('Request',(),{
            'args':self.args,'headers':self.headers,
            'get_json':lambda obj,silent: self.body
        })()
        self.session=type('S',(),{'rollback':self.rollback,'commit':Mock()})()
        self.db=type('Database',(),{'session':self.session})()
    def process(self,pid):
        self.calls.append(str(pid))
        if self.provider_error:raise self.provider_error
        return self.provider_result
    def run(self):
        local={
            'request':self.request,'db':self.db,'app':self.app,
            'os':type('O',(),{'environ':self.env,'getenv':lambda obj,key,default='':self.env.get(key,default)})(),
            're':re,
            'jsonify':lambda obj:obj,
            '_release_billing_ready':lambda :True,
            '_env_bool':lambda key,default=False:True,
            'is_legacy_ipn_payment_hint':is_legacy_ipn_payment_hint,
            'hml_legacy_ipn_hint_enabled':hml_legacy_ipn_hint_enabled,
            'verify_webhook_signature':verify_webhook_signature,
            '_processar_pagamento':self.process,
            '_release_mp_client':Mock(),
            'BillingError':BillingError,
            'BillingIntent':Mock(),
        }
        exec(EXEC,local)
        return local['webhook']()

class WebhookV14Tests(unittest.TestCase):
    def setup_signed(self, **kw):
        pid='187822707179'
        ts=str(int(datetime.now(timezone.utc).timestamp()*1000))
        reqid='signed-mp-request-v14'
        key='fictitious-signature-secret'
        manifest=f'id:{pid};request-id:{reqid};ts:{ts};'
        sig='ts='+ts+',v1='+hmac.new(key.encode(),manifest.encode(),hashlib.sha256).hexdigest()
        obj=Harness(query={'data.id':pid,'type':'payment'}, body={'data':{'id':pid},'type':'payment'},
                    signature=sig,request_id=reqid,env={'MP_WEBHOOK_SECRET':key},**kw)
        return obj
    def test_signed_real_route_checks_hmac_then_activates(self):
        h=self.setup_signed(); payload,code=h.run()
        self.assertEqual(code,200);self.assertEqual(payload['result'],'activated')
        self.assertEqual(h.calls,['187822707179'])
    def test_duplicate_signed_event_idempotent_outcome(self):
        h=self.setup_signed(provider_result={'aplicado':False,'duplicado':True})
        payload,code=h.run();self.assertEqual((code,payload['result']),(200,'duplicate'))
    def test_invalid_signature_never_processes(self):
        h=self.setup_signed();h.headers['X-Signature']='ts=1234567890,v1='+'0'*64
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
    def test_missing_signature_never_processes(self):
        h=self.setup_signed();h.headers['X-Signature']=''
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
    def test_mismatching_query_body_is_blocked(self):
        h=self.setup_signed();h.body['data']['id']='123'
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
    def test_invalid_topic_not_downgraded_to_legacy(self):
        h=self.setup_signed();h.body['type']='merchant_order'
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
    def test_invalid_provider_payment_fails_closed(self):
        h=self.setup_signed(provider_error=BillingError('Valor divergente'))
        payload,code=h.run();self.assertEqual(code,422);self.assertEqual(h.calls,['187822707179'])
        h.rollback.assert_called_once()
    def test_provider_failure_is_retriable(self):
        h=self.setup_signed(provider_error=RuntimeError('Provider offline'))
        payload,code=h.run();self.assertEqual(code,503)
    def test_legacy_hml_hint_requires_exact_env(self):
        q={'id':'187822707179','topic':'payment'}
        h=Harness(query=q)
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
        env={'ACHETECE_HML_LEGACY_IPN_HINTS':'1','ACHETECE_BILLING_ENV':'sandbox',
             'ACHETECE_HML_EXPECTED_DB_NAME':'achetece_release_v1_test',
             'ACHETECE_MP_TEST_TOKEN_CONFIRMED':'1',
             'ACHETECE_HML_TEST_BUYER_USER_ID':'2564853871'}
        h=Harness(query=q,env=env)
        payload,code=h.run();self.assertEqual(code,200)
        self.assertEqual(h.calls,['187822707179'])
    def test_legacy_never_enabled_in_production(self):
        env={'ACHETECE_HML_LEGACY_IPN_HINTS':'1',
             'ACHETECE_BILLING_ENV':'production',
             'ACHETECE_HML_EXPECTED_DB_NAME':'achetece_release_v1_test',
             'ACHETECE_MP_TEST_TOKEN_CONFIRMED':'1',
             'ACHETECE_HML_TEST_BUYER_USER_ID':'2564853871'}
        h=Harness(query={'id':'187822707179','topic':'payment'},env=env)
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
    def test_legacy_modern_format_not_downgraded(self):
        q={'topic':'payment','id':'187822707179','data.id':'187822707179'}
        h=Harness(query=q)
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
    def test_merchant_order_cannot_activate(self):
        h=Harness(query={'topic':'merchant_order','id':'1234567'})
        payload,code=h.run();self.assertEqual(code,200);self.assertEqual(h.calls,[])
    def test_unknown_legacy_does_not_activate(self):
        h=Harness(query={'topic':'payment','id':'../../etc/passwd'})
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])
    def test_provider_checkout_prefers_dashboard_signed_webhooks(self):
        s=SOURCE.split("@app.route('/checkout'",1)[1].split("@app.route('/assinatura/cancelar'",1)[0]
        self.assertNotIn("'notification_url':",s)
        self.assertIn("'back_urls':",s)
    def test_strict_legacy_classification(self):
        self.assertTrue(is_legacy_ipn_payment_hint({'topic':'payment','id':'187822707179'},{}))
        self.assertFalse(is_legacy_ipn_payment_hint({'topic':'payment','id':'187822707179','data.id':'187822707179'},{}))
        self.assertFalse(is_legacy_ipn_payment_hint({'topic':'merchant_order','id':'187822707179'},{}))
        self.assertFalse(is_legacy_ipn_payment_hint({'topic':'payment','id':'1'}, {'data':{'id':'2'}}))
    def test_body_with_only_payment_type_is_not_signed_confirmation(self):
        h=Harness(body={'type':'payment','data':{'id':'187822707179'}})
        payload,code=h.run();self.assertEqual(code,401);self.assertEqual(h.calls,[])

if __name__=='__main__':unittest.main()
