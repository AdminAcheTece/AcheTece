"""Testes isolados da integracao financeira ED02.3G.7D.2 (sem rede/DB)."""
import ast
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import Mock, patch

MAIN=Path(__file__).with_name('main.py')
IDEM=Path(__file__).with_name('ed02_payment_idempotency.py')
GUARD=Path(__file__).with_name('ed02_payment_contract_guard.py')

class IntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        tree=ast.parse(MAIN.read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='_processar_pagamento')
        cls.fn_code=compile(ast.Module(body=[fn], type_ignores=[]),str(MAIN),'exec')

    def environ(self, *, guard=True, exact=True, valid=True):
        empresa=SimpleNamespace(id=1, status_pagamento='pendente', email='teste@example.org')
        get=Mock(return_value=empresa)
        byemail=Mock()
        query=SimpleNamespace(get=get, filter=byemail)
        payment={'id': '123', 'external_reference': 'achetece:1:'+'a'*32,
                 'status':'approved','payer':{'email':'teste@example.org'}}
        verify=Mock(return_value={'empresa_id':1})
        if not valid: verify.side_effect=ValueError('invalido')
        atomic=Mock(return_value={'primeira_ativacao':False,'aplicado':True,'duplicado':False,'status':'approved'})
        env={'_mp_get_payment':Mock(return_value=payment),'_env_bool':lambda key,default=False: {'ED02_FINANCIAL_GUARD_HOMOLOG':guard,'ED02_EXACT_ONCE_HOMOLOG':exact}.get(key,False),
             '_parse_empresa_id_from_external_reference':Mock(return_value=1),'Empresa':SimpleNamespace(query=query),
             'db':SimpleNamespace(session=SimpleNamespace(rollback=Mock())),
             'app':SimpleNamespace(logger=SimpleNamespace(info=Mock(), warning=Mock(),exception=Mock())),
             '_make_magic_link':Mock(),'_email_ativacao_html':Mock(),'_send_email':Mock()}
        exec(self.fn_code, env)
        module_guard=SimpleNamespace(verify_payment_against_contract=verify)
        module_idem=SimpleNamespace(process_verified_payment_exact_once=atomic)
        return env, verify, atomic, byemail, get, module_guard, module_idem

    def test_guarda_validada_antes_de_ativar(self):
        env,verify,atomic,byemail,get,modguard,modidem=self.environ()
        with patch.dict(sys.modules,{'ed02_payment_contract_guard':modguard,'ed02_payment_idempotency':modidem}):
            res=env['_processar_pagamento']('123')
        self.assertTrue(res['ativou']); verify.assert_called_once();atomic.assert_called_once(); byemail.assert_not_called()
    def test_contrato_invalido_falha_fechada(self):
        env,verify,atomic,byemail,get,modguard,modidem=self.environ(valid=False)
        with patch.dict(sys.modules,{'ed02_payment_contract_guard':modguard,'ed02_payment_idempotency':modidem}):
            with self.assertRaises(RuntimeError): env['_processar_pagamento']('123')
        atomic.assert_not_called();byemail.assert_not_called();get.assert_not_called()
        env['db'].session.rollback.assert_called_once()
    def test_guarda_exige_transacional(self):
        env,verify,atomic,byemail,get,modguard,modidem=self.environ(exact=False)
        with self.assertRaises(RuntimeError):env['_processar_pagamento']('123')
        verify.assert_not_called(); atomic.assert_not_called();get.assert_not_called()
    def test_flags_desligadas_preservam_fluxo_legado(self):
        env,verify,atomic,byemail,get,modguard,modidem=self.environ(guard=False,exact=True)
        with patch.dict(sys.modules,{'ed02_payment_contract_guard':modguard,'ed02_payment_idempotency':modidem}):
            env['_processar_pagamento']('123')
        verify.assert_not_called();atomic.assert_called_once()
    def test_validacao_repetida_no_modulo_transacional(self):
        src=IDEM.read_text()
        self.assertLess(src.index('verify_payment_against_contract'),src.index('session.execute(text(\'SELECT current_database()\')'))
    def test_bloqueio_no_banco_antes_de_qualquer_dml(self):
        src=IDEM.read_text()
        self.assertLess(src.index('verify_payment_against_contract'),src.index('UPDATE public.empresa'))
    def test_contrato_trava_linha(self):
        self.assertIn('FOR UPDATE',GUARD.read_text())
    def test_referencias_do_checkout_preservadas(self):
        src=MAIN.read_text()
        self.assertIn('ext_ref = f"achetece:{empresa.id}:{uuid.uuid4().hex}"',src)
        self.assertIn('ED02_CHECKOUT_LEDGER_HOMOLOG',src)

if __name__=='__main__': unittest.main(verbosity=2)
