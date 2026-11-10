"""Regressoes da conciliacao HML apos o pagamento de teste anual."""
import unittest
from datetime import datetime,timezone
from pathlib import Path
import hmac,hashlib,sys,ast
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from billing_release import verify_webhook_signature
ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main.py').read_text(encoding='utf-8')
TREE=ast.parse(SOURCE)

def method(name):
    return next(x for x in TREE.body if isinstance(x,ast.FunctionDef) and x.name==name)

class TestHmlVersion12(unittest.TestCase):
    def setUp(self):
        self.now=datetime(2026,11,10,19,43,tzinfo=timezone.utc)
        self.secret='fictitious-hmac-key'
        self.id='187822707179'
        self.req='random-request-id'
    def signature(self,ts):
        message=f'id:{self.id};request-id:{self.req};ts:{ts};'
        digest=hmac.new(self.secret.encode(),message.encode(),hashlib.sha256).hexdigest()
        return f'ts={ts},v1={digest}'
    def test_milliseconds(self):
        ts=str(int(self.now.timestamp()*1000))
        self.assertTrue(verify_webhook_signature(self.signature(ts),self.req,self.id,self.secret,now=self.now))
    def test_seconds(self):
        ts=str(int(self.now.timestamp()))
        self.assertTrue(verify_webhook_signature(self.signature(ts),self.req,self.id,self.secret,now=self.now))
    def test_timestamp_stale_rejected(self):
        ts=str(int(self.now.timestamp())-1800)
        self.assertFalse(verify_webhook_signature(self.signature(ts),self.req,self.id,self.secret,now=self.now))
    def test_wrong_hmac_rejected_in_both_formats(self):
        for ts in (str(int(self.now.timestamp())),str(int(self.now.timestamp()*1000))):
            self.assertFalse(verify_webhook_signature(self.signature(ts),self.req,self.id,'other-secret',now=self.now))
    def test_reconciliation_is_POST_authenticated_and_HML_only(self):
        c=ast.get_source_segment(SOURCE,method('conciliar_pagamento_hml'))
        decorators=' '.join(ast.get_source_segment(SOURCE,d) for d in method('conciliar_pagamento_hml').decorator_list)
        self.assertIn("'GET', 'POST'",decorators)
        self.assertIn('ACHETECE_HML_EXPECTED_DB_NAME',c)
        self.assertIn("session.get('empresa_id')",c)
        self.assertIn('empresa_id=empresa.id',c)
        self.assertIn('_processar_pagamento(payment_id)',c)
        self.assertIn("if request.method == 'GET':",c)
        self.assertIn("return render_template('conciliacao_hml.html'",c)
        self.assertLess(c.index("if request.method == 'GET':"),c.index('_processar_pagamento(payment_id)'))
    def test_missing_request_id_is_signed_without_request_id_section(self):
        ts=str(int(self.now.timestamp()*1000))
        message=f'id:{self.id};ts:{ts};'
        sig='ts='+ts+',v1='+hmac.new(self.secret.encode(),message.encode(),hashlib.sha256).hexdigest()
        self.assertTrue(verify_webhook_signature(sig,'',self.id,self.secret,now=self.now))
        self.assertFalse(verify_webhook_signature(sig,'fake-id',self.id,self.secret,now=self.now))
    def test_pending_page_never_prompts_repeat_payment(self):
        page=(ROOT/'templates/pagamento_pendente.html').read_text()
        self.assertNotIn('Realizar pagamento',page)
        self.assertIn('não pague novamente',page.lower())
    def test_polling_route_read_only(self):
        c=ast.get_source_segment(SOURCE,method('api_pagamento_status'))
        self.assertNotIn('_processar_pagamento',c)
        self.assertNotIn('db.session.commit',c)
        self.assertIn('session.get',c)
        self.assertIn('no-store',c)
    def test_webhook_never_ignores_invalid_signature(self):
        c=ast.get_source_segment(SOURCE,method('webhook'))
        self.assertIn('verify_webhook_signature(',c)
        self.assertIn("return jsonify({'ok':False,'error':'unauthorized'}),401",c)
    def test_reconciliation_form_has_csrf(self):
        p=(ROOT/'templates/conciliacao_hml.html').read_text()
        self.assertIn('csrf_token()',p)
        self.assertIn('name="payment_id"',p)

if __name__=='__main__':unittest.main()
