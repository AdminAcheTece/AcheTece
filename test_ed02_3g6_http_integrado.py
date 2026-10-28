"""ED02.3G.6 — Flask test_client HTTP/WSGI, sem rede, MP, banco ou e-mails.
Extrai a rota webhook do main.py com AST; usa SQLAlchemy apenas para construir text().
O banco e o processador sao mocks e nunca sao acessados externamente.
"""
import ast
from contextlib import nullcontext
import hashlib
import hmac
import os
from pathlib import Path
import time
import unittest
from types import SimpleNamespace
from unittest.mock import Mock, patch

from flask import Flask, request, jsonify

ROOT = Path(__file__).resolve().parent

def load_webhook():
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'webhook')
    node.decorator_list = []
    scope = {'os': os, 'request': request, 'jsonify': jsonify}
    exec(compile(ast.Module(body=[node], type_ignores=[]), str(ROOT / 'main.py'), 'exec'), scope)
    return scope['webhook'], scope

class IntegratedHTTPTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask('ed02_3g6_http_only')
        self.app.config['TESTING'] = True
        self.webhook, self.scope = load_webhook()
        self.app.add_url_rule('/webhook', 'ed02_webhook', self.webhook, methods=['GET', 'POST'])
        self.client = self.app.test_client()
        self.payment = Mock(return_value={'ok': True, 'empresa_id': 1})
        self.extract = Mock(return_value=('123456', {'data': {'id': '123456'}}))
        self.query = Mock(return_value=SimpleNamespace(scalar_one=lambda: 'achetece_ed01_test'))
        self.db = SimpleNamespace(session=SimpleNamespace(no_autoflush=nullcontext(), execute=self.query),
                                  engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')))
        self.scope.update(app=self.app, db=self.db,
                          _processar_pagamento=self.payment, _extract_payment_id=self.extract)
        env = patch.dict(os.environ, {
            'ED02_WEBHOOK_SIGNATURE_HOMOLOG': 'true',
            'ED01_HOMOLOGACAO': 'true',
            'ED02_HOMOLOG_DATABASE_NAME': 'achetece_ed01_test',
            'ED02_MP_WEBHOOK_SECRET': 'secret-ficticio-ED02',
        }, clear=False)
        env.start()
        self.addCleanup(env.stop)

    def post(self, *, valid=True, body_id='123456', query_id='123456', with_signature=True):
        request_id = 'ED02-3G6-MOCK-001'
        ts = str(int(time.time()))
        manifest = f'id:{query_id};request-id:{request_id};ts:{ts};'
        digest = hmac.new(b'secret-ficticio-ED02', manifest.encode(), hashlib.sha256).hexdigest()
        if not valid:
            digest = '0'*64 if digest != '0'*64 else '1'*64
        headers = {'x-request-id': request_id}
        if with_signature:
            headers['x-signature'] = f'ts={ts},v1={digest}'
        return self.client.post(f'/webhook?type=payment&data.id={query_id}',
                                json={'type': 'payment', 'data': {'id': body_id}}, headers=headers)

    def test_01_valid_http_processes_once(self):
        response = self.post()
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.get_json()['ok'])
        self.query.assert_called_once()
        self.payment.assert_called_once_with('123456')

    def test_02_invalid_hmac_denied_before_processing(self):
        response = self.post(valid=False)
        self.assertEqual(response.status_code, 401)
        self.payment.assert_not_called()
        self.extract.assert_not_called()

    def test_03_missing_signature_denied(self):
        response = self.post(with_signature=False)
        self.assertEqual(response.status_code, 401)
        self.payment.assert_not_called()

    def test_04_body_query_mismatch_denied(self):
        response = self.post(body_id='123457')
        self.assertEqual(response.status_code, 401)
        self.payment.assert_not_called()

    def test_05_real_db_name_mismatch_denied(self):
        self.query.return_value = SimpleNamespace(scalar_one=lambda: 'achetece_db')
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertEqual(response.get_json()['error'], 'temporarily_unavailable')
        self.payment.assert_not_called()

    def test_06_real_db_query_failure_denied_without_error_leak(self):
        self.query.side_effect = RuntimeError('INTERNAL_CONNECTION_PASSWORD_TEST')
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('INTERNAL_CONNECTION_PASSWORD_TEST', response.get_data(as_text=True))
        self.payment.assert_not_called()

    def test_07_missing_secret_denied(self):
        os.environ.pop('ED02_MP_WEBHOOK_SECRET', None)
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.payment.assert_not_called()

    def test_08_processor_failure_returns_generic_503(self):
        self.payment.side_effect = RuntimeError('MP_INTERNAL_SECRET_TEST')
        response = self.post()
        self.assertEqual(response.status_code, 503)
        self.assertNotIn('MP_INTERNAL_SECRET_TEST', response.get_data(as_text=True))

    def test_09_no_flag_uses_legacy_without_db_guard(self):
        os.environ['ED02_WEBHOOK_SIGNATURE_HOMOLOG'] = 'false'
        response = self.post(with_signature=False)
        self.assertEqual(response.status_code, 200)
        self.query.assert_not_called()
        self.payment.assert_called_once()

    def test_10_get_is_nonfinancial(self):
        response = self.client.get('/webhook')
        self.assertEqual(response.status_code, 200)
        self.payment.assert_not_called()
        self.query.assert_not_called()

if __name__ == '__main__':
    unittest.main(verbosity=2)
