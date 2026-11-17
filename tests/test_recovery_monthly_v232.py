"""HML V2.3.2 isolated recovery tests (stdlib only; no API, database or app import)."""
import ast
import os
import re
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parents[1]
SOURCE = ROOT / 'main.py'
AST = ast.parse(SOURCE.read_text(encoding='utf-8'), filename=str(SOURCE))
FN = next(n for n in AST.body if isinstance(n, ast.FunctionDef) and n.name == 'conciliar_pagamento_hml')
FN.decorator_list = []
CODE = compile(ast.fix_missing_locations(ast.Module(body=[FN], type_ignores=[])), str(SOURCE), 'exec')

class BillingError(ValueError):
    pass

class ProviderError(RuntimeError):
    pass

class AbortError(Exception):
    def __init__(self, code):
        self.code = code

class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.env = {
            'ACHETECE_BILLING_ENV':'sandbox',
            'ACHETECE_HML_EXPECTED_DB_NAME':'achetece_release_v1_test',
            'ACHETECE_MP_TEST_TOKEN_CONFIRMED':'1',
            'ACHETECE_BILLING_CALLBACK_BASE_URL':'https://achetece-assinaturas-hml.onrender.com',
        }
        self.company = SimpleNamespace(id=3)
        self.intent = SimpleNamespace(empresa_id=3, external_reference='ref-for-3', plano='mensal',
                                     provider_resource_id='sub-3', amount_cents=4700, currency='BRL')
        self.payment = {'external_reference':'ref-for-3'}
        self.invoice = {'id':'7033912330','preapproval_id':'sub-3','external_reference':'ref-for-3',
                        'transaction_amount':47,'currency_id':'BRL','status':'processed',
                        'payment':{'id':'188079970805','status':'approved'}}
        self.client=SimpleNamespace(get_payment=Mock(return_value=self.payment),
                                    get_authorized_payment=Mock(return_value=self.invoice))
        self.verify=Mock(side_effect=self.verify_invoice)
        self.process=Mock(return_value={'aplicado':True})
        self.query=Mock()
        self.query.filter_by.return_value.first.return_value=self.intent
        self.db=SimpleNamespace(session=SimpleNamespace(get=Mock(return_value=self.company),rollback=Mock()))
        self.values={'payment_id':'188079970805','invoice_id':'7033912330'}

    def verify_invoice(self, obj, *, invoice_id, subscription_id, reference, payment_id, cents, currency):
        if str(obj.get('id')) != invoice_id or str(obj.get('preapproval_id')) != subscription_id or \
           str(obj.get('external_reference')) != reference or \
           str(obj.get('payment', {}).get('id')) != payment_id or \
           obj.get('payment', {}).get('status') != 'approved' or \
           obj.get('currency_id') != currency or round(float(obj.get('transaction_amount',0))*100) != cents:
            raise BillingError('invoice validation failed')
        return True

    def invoke(self, *, method='POST', session_id=3):
        args = SimpleNamespace(method=method,form=self.values)
        scope={
            'os':SimpleNamespace(getenv=lambda name,default=None:self.env.get(name,default)),
            're':re,
            '_env_bool': lambda name,default=False: (True if name == 'ACHETECE_RELEASE_SCHEMA_READY' else self.env.get(name)=='1'),
            'session':{'empresa_id':session_id} if session_id else {},
            'request':args,
            'abort':lambda code: (_ for _ in ()).throw(AbortError(code)),
            'redirect':lambda path,code=302:('redirect',path,code),
            'url_for':lambda name:'/fake-'+name,
            'render_template':lambda name,**kw:('template',name),
            'db':self.db,
            'Empresa':SimpleNamespace(),
            'BillingIntent':SimpleNamespace(query=self.query),
            '_release_billing_ready':lambda:True,
            '_release_mp_client':lambda:self.client,
            'verify_authorized_invoice':self.verify,
            '_processar_pagamento':self.process,
            'BillingError':BillingError,
            'ProviderError':ProviderError,
            'app':SimpleNamespace(logger=SimpleNamespace(warning=Mock(),exception=Mock())),
        }
        exec(CODE,scope)
        return scope['conciliar_pagamento_hml']()

    def test_monthly_success_uses_existing_processor_with_invoice(self):
        self.assertEqual(self.invoke(),('redirect','/fake-painel_malharia',303))
        self.process.assert_called_once_with('188079970805', invoice_id='7033912330')
        self.verify.assert_called_once()
    def test_monthly_duplicate_is_success_without_second_processor(self):
        self.process.return_value={'duplicado':True,'aplicado':False}
        self.assertEqual(self.invoke()[2],303)
        self.process.assert_called_once()
    def test_monthly_pending_payment_rejected_by_processor(self):
        self.process.return_value={'aplicado':False}
        self.assertEqual(self.invoke()[1],409)
    def test_other_company_payment_reference_is_denied(self):
        self.query.filter_by.return_value.first.return_value=None
        self.assertEqual(self.invoke()[1],403)
        self.process.assert_not_called()
    # V2.3.4: assinatura recorrente pode omitir referencia no pagamento.
    # A fatura so identifica a contratacao da propria empresa autenticada.
    def test_missing_payment_reference_uses_invoice_subscription(self):
        self.payment['external_reference'] = None
        self.assertEqual(self.invoke(), ('redirect','/fake-painel_malharia',303))
        self.query.filter_by.assert_called_once_with(
            empresa_id=3, plano='mensal', provider_resource_id='sub-3')
        self.client.get_authorized_payment.assert_called_once_with('7033912330')
        self.verify.assert_called_once()
        self.process.assert_called_once_with('188079970805', invoice_id='7033912330')

    def test_missing_reference_requires_invoice(self):
        self.payment['external_reference'] = None
        self.values['invoice_id'] = ''
        self.assertEqual(self.invoke()[1], 400)
        self.client.get_authorized_payment.assert_not_called()
        self.process.assert_not_called()

    def test_missing_reference_foreign_company_is_denied(self):
        self.payment['external_reference'] = ''
        self.query.filter_by.return_value.first.return_value = None
        self.assertEqual(self.invoke()[1], 403)
        self.query.filter_by.assert_called_once_with(
            empresa_id=3, plano='mensal', provider_resource_id='sub-3')
        self.process.assert_not_called()

    def test_missing_reference_invoice_reference_mismatch_denied(self):
        self.payment['external_reference'] = None
        self.invoice['external_reference'] = 'someone-else'
        self.assertEqual(self.invoke()[1], 422)
        self.process.assert_not_called()

    def test_missing_reference_invoice_payment_mismatch_denied(self):
        self.payment['external_reference'] = None
        self.invoice['payment']['id'] = 'unrelated-payment'
        self.assertEqual(self.invoke()[1], 422)
        self.process.assert_not_called()

    def test_missing_reference_invoice_without_subscription_denied(self):
        self.payment['external_reference'] = None
        self.invoice['preapproval_id'] = None
        self.assertEqual(self.invoke()[1], 422)
        self.query.filter_by.assert_not_called()
        self.process.assert_not_called()

    def test_present_reference_never_uses_invoice_fallback(self):
        self.payment['external_reference'] = 'another-company'
        self.query.filter_by.return_value.first.return_value = None
        self.assertEqual(self.invoke()[1], 403)
        self.query.filter_by.assert_called_once_with(
            empresa_id=3, external_reference='another-company')
        self.client.get_authorized_payment.assert_not_called()
        self.process.assert_not_called()

    def test_missing_reference_non_processed_invoice_denied(self):
        self.payment['external_reference'] = None
        self.invoice['status'] = 'pending'
        self.assertEqual(self.invoke()[1], 422)
        self.process.assert_not_called()

    def test_missing_invoice_rejected(self):
        self.values['invoice_id']=''
        self.assertEqual(self.invoke()[1],400)
        self.process.assert_not_called()
    def test_invalid_invoice_id_rejected_before_any_provider_call(self):
        self.values['invoice_id']='hack'
        self.assertEqual(self.invoke()[1],400)
        self.client.get_payment.assert_not_called()
    def test_invalid_payment_id_rejected_before_provider_call(self):
        self.values['payment_id']='hack'
        self.assertEqual(self.invoke()[1],400)
        self.client.get_payment.assert_not_called()
    def test_invoice_reference_mismatch_rejected(self):
        self.invoice['external_reference']='other-ref'
        self.assertEqual(self.invoke()[1],422)
        self.process.assert_not_called()
    def test_invoice_payment_id_mismatch_rejected(self):
        self.invoice['payment']['id']='wrong'
        self.assertEqual(self.invoke()[1],422)
        self.process.assert_not_called()
    def test_invoice_amount_mismatch_rejected(self):
        self.invoice['transaction_amount']=47.01
        self.assertEqual(self.invoke()[1],422)
        self.process.assert_not_called()
    def test_invoice_not_processed_rejected(self):
        self.invoice['status']='pending'
        self.assertEqual(self.invoke()[1],422)
        self.process.assert_not_called()
    def test_invoice_subscription_mismatch_rejected(self):
        self.invoice['preapproval_id']='another-subscription'
        self.assertEqual(self.invoke()[1],422)
        self.process.assert_not_called()
    def test_invoice_status_not_approved_rejected(self):
        self.invoice['payment']['status']='pending'
        self.assertEqual(self.invoke()[1],422)
        self.process.assert_not_called()
    def test_missing_subscription_resource_rejected(self):
        self.intent.provider_resource_id=''
        self.assertEqual(self.invoke()[1],422)
        self.process.assert_not_called()
    def test_no_login_redirects_before_provider(self):
        self.assertEqual(self.invoke(session_id=None)[2],302)
        self.client.get_payment.assert_not_called()
    def test_hml_wrong_database_blocked(self):
        self.env['ACHETECE_HML_EXPECTED_DB_NAME']='other'
        with self.assertRaises(AbortError) as ctx:self.invoke()
        self.assertEqual(ctx.exception.code,404)
    def test_production_blocked(self):
        self.env['ACHETECE_BILLING_ENV']='production'
        with self.assertRaises(AbortError):self.invoke()
    def test_unknown_callback_blocked(self):
        self.env['ACHETECE_BILLING_CALLBACK_BASE_URL']='https://other.example'
        with self.assertRaises(AbortError):self.invoke()
    def test_unconfirmed_token_blocked(self):
        self.env['ACHETECE_MP_TEST_TOKEN_CONFIRMED']='0'
        with self.assertRaises(AbortError):self.invoke()
    def test_annual_compatibility_preserved(self):
        self.intent.plano='anual';self.values['invoice_id']=''
        self.assertEqual(self.invoke()[2],303)
        self.process.assert_called_once_with('188079970805')
    def test_annual_does_not_accept_invoice_id(self):
        self.intent.plano='anual'
        self.assertEqual(self.invoke()[1],400)
    def test_get_renders_form_without_api(self):
        self.assertEqual(self.invoke(method='GET'),('template','conciliacao_hml.html'))
        self.client.get_payment.assert_not_called()
    def test_template_csrf_and_invoice_field(self):
        html=(ROOT/'templates'/'conciliacao_hml.html').read_text(encoding='utf-8')
        self.assertIn('name="csrf_token"',html)
        self.assertIn('name="invoice_id"',html)
        self.assertIn('name="payment_id"',html)

if __name__ == '__main__':unittest.main()
