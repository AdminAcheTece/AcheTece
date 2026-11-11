"""HML V2.0.1 isolated webhook tests; stdlib only; no network/DB/app import."""
import ast
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch
import re

SOURCE = Path(__file__).resolve().parents[1] / 'main.py'
AST = ast.parse(SOURCE.read_text(encoding='utf-8'), filename=str(SOURCE))
FN = next(n for n in AST.body if isinstance(n, ast.FunctionDef) and n.name == 'webhook')
FN.decorator_list = []
MODULE = ast.Module(body=[FN], type_ignores=[])
CODE = compile(ast.fix_missing_locations(MODULE), str(SOURCE), 'exec')

class BillingError(ValueError):
    pass

class ProviderError(RuntimeError):
    def __init__(self, status=None):
        self.http_status = status

class WebhookTests(TestCase):
    def setUp(self):
        self.env = {
            'ACHETECE_BILLING_ENV': 'sandbox',
            'ACHETECE_HML_EXPECTED_DB_NAME': 'achetece_release_v1_test',
            'ACHETECE_MP_TEST_TOKEN_CONFIRMED': '1',
            'ACHETECE_BILLING_CALLBACK_BASE_URL': 'https://achetece-assinaturas-hml.onrender.com',
        }
        self.args = {'data.id': '123456', 'type': 'payment'}
        self.payload = {'type': 'payment', 'data': {'id': '123456'}}
        self.process = Mock(side_effect=ProviderError(404))
        self.session = Mock()
        self.app = SimpleNamespace(logger=Mock())

    def run_event(self, *, changes=None, signed=True, legacy=False):
        env = dict(self.env)
        env.update(changes or {})
        args = {'topic':'payment', 'id':'123456'} if legacy else dict(self.args)
        request = SimpleNamespace(
            args=args,
            get_json=lambda silent=True: dict(self.payload),
            headers={'X-Signature':'placeholder', 'X-Request-Id':'placeholder'},
        )
        scope = {
            'request': request,
            're': re,
            'os': SimpleNamespace(getenv=lambda k, default=None:env.get(k,default), environ=env),
            '_release_billing_ready': lambda: True,
            '_env_bool': lambda key, default=False: key == 'ACHETECE_RELEASE_SCHEMA_READY',
            'is_legacy_ipn_payment_hint': lambda a,p: legacy,
            'hml_legacy_ipn_hint_enabled':lambda e:True,
            'verify_webhook_signature': lambda *x: signed,
            '_processar_pagamento':self.process,
            'BillingError':BillingError,
            'ProviderError':ProviderError,
            'db':SimpleNamespace(session=self.session),
            'app':self.app,
            'jsonify':lambda data:data,
        }
        exec(CODE,scope)
        return scope['webhook']()

    def test_404_signed_isolated_hml_ack_without_activation(self):
        body,status=self.run_event()
        self.assertEqual(status,200)
        self.assertEqual(body['result'],'ignored_missing_hml_payment')
        self.process.assert_called_once_with('123456')
        self.session.commit.assert_not_called()
        self.session.rollback.assert_called_once()

    def test_404_production_no_ack(self):
        self.assertEqual(self.run_event(changes={'ACHETECE_BILLING_ENV':'production'})[1],503)
    def test_404_wrong_database_no_ack(self):
        self.assertEqual(self.run_event(changes={'ACHETECE_HML_EXPECTED_DB_NAME':'another'})[1],503)
    def test_404_unconfirmed_test_token_no_ack(self):
        self.assertEqual(self.run_event(changes={'ACHETECE_MP_TEST_TOKEN_CONFIRMED':'0'})[1],503)
    def test_404_wrong_callback_no_ack(self):
        self.assertEqual(self.run_event(changes={'ACHETECE_BILLING_CALLBACK_BASE_URL':'https://other.invalid'})[1],503)
    def test_404_legacy_no_ack(self):
        self.assertEqual(self.run_event(legacy=True)[1],503)
    def test_429_retry(self):
        self.process.side_effect=ProviderError(429)
        self.assertEqual(self.run_event()[1],503)
    def test_503_retry(self):
        self.process.side_effect=ProviderError(503)
        self.assertEqual(self.run_event()[1],503)
    def test_unknown_network_error_retry(self):
        self.process.side_effect=ProviderError(None)
        self.assertEqual(self.run_event()[1],503)
    def test_unexpected_exception_retry(self):
        self.process.side_effect=RuntimeError('private diagnostic should not leak')
        body,status=self.run_event()
        self.assertEqual(status,503)
        self.assertNotIn('private',str(body))
    def test_reconciliation_failure_still_422(self):
        self.process.side_effect=BillingError('not reconciled')
        self.assertEqual(self.run_event()[1],422)
    def test_approved_activates_only_via_processor(self):
        self.process.side_effect=None
        self.process.return_value={'aplicado':True,'duplicado':False}
        self.assertEqual(self.run_event(),({'ok':True,'result':'activated'},200))
    def test_duplicate_is_ack(self):
        self.process.side_effect=None
        self.process.return_value={'aplicado':False,'duplicado':True}
        self.assertEqual(self.run_event(),({'ok':True,'result':'duplicate'},200))
    def test_invalid_signature_blocks_before_provider(self):
        self.assertEqual(self.run_event(signed=False)[1],401)
        self.process.assert_not_called()
    def test_invalid_payment_id_blocks_before_provider(self):
        self.args['data.id']='not-a-number'
        self.payload['data']['id']='not-a-number'
        self.assertEqual(self.run_event()[1],400)
        self.process.assert_not_called()

if __name__=='__main__':
    main()
