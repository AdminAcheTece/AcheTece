"""ED02.3E — testa a chamada da nova rota por AST, sem conexao, email real ou MP."""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock, patch
import sys, types

ROOT = Path(__file__).parent

def load():
    tree=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
    f=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_processar_pagamento')
    d={}
    exec(compile(ast.Module(body=[f],type_ignores=[]),'main.py','exec'),d)
    return d['_processar_pagamento'],d

class Dummy:
    id=1
    email='tester@example.invalid'
    status_pagamento='pendente'
    data_pagamento=None

class Tests(unittest.TestCase):
    def setUp(self):
        self.fn,self.env=load(); self.e=Dummy()
        self.payment={'id':'12345','status':'approved','external_reference':'achetece:1:fixture', 'currency_id':'BRL', 'transaction_amount':2.0}
        self.db=types.SimpleNamespace(session=types.SimpleNamespace(commit=Mock(),rollback=Mock()))
        self.processor=Mock(return_value={'aplicado':True,'duplicado':False,'primeira_ativacao':True,'status':'approved'})
        mod=types.ModuleType('ed02_payment_idempotency'); mod.process_verified_payment_exact_once=self.processor
        self.patcher=patch.dict(sys.modules,{'ed02_payment_idempotency':mod});self.patcher.start();self.addCleanup(self.patcher.stop)
        self.env.update(db=self.db,Empresa=types.SimpleNamespace(query=types.SimpleNamespace(get=lambda i:self.e)),
            _mp_get_payment=Mock(return_value=self.payment),_parse_empresa_id_from_external_reference=lambda r:1,
            _env_bool=lambda k,d=False:k=='ED02_EXACT_ONCE_HOMOLOG',
            _make_magic_link=Mock(return_value='https://example.invalid'),_email_ativacao_html=Mock(return_value='abc'),
            _send_email=Mock(),app=types.SimpleNamespace(logger=types.SimpleNamespace(info=Mock(),exception=Mock())))
    def test_first_payment_calls_atomic_and_emails_once(self):
        response=self.fn('12345')
        self.assertTrue(response['ativou']);self.processor.assert_called_once_with(self.db,self.payment,1)
        self.env['_send_email'].assert_called_once()
        self.db.session.commit.assert_not_called()  # no legacy updates
    def test_replay_no_email_or_legacy_writes(self):
        self.processor.return_value={'aplicado':False,'duplicado':True,'primeira_ativacao':False,'status':'approved'}
        response=self.fn('12345')
        self.assertTrue(response['duplicado']);self.assertFalse(response['ativou'])
        self.env['_send_email'].assert_not_called();self.db.session.commit.assert_not_called()
    def test_non_approved_never_emails(self):
        self.processor.return_value={'aplicado':False,'duplicado':False,'primeira_ativacao':False,'status':'pending'}
        self.payment['status']='pending'; self.fn('12345')
        self.env['_send_email'].assert_not_called()

if __name__=='__main__':unittest.main(verbosity=2)
