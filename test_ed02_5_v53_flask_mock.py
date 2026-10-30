"""V5.3: execucao isolada da funcao Flask, sem iniciar app, MP ou conexao.
Os mocks verificam as transicoes, nao substituem o teste real de duas requisições.
"""
import ast
import os
import unittest
from pathlib import Path
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock, patch


def load_processor():
    tree = ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf-8'))
    fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == '_processar_pagamento')
    scope = {'__name__': '_ed025_v53_test_'}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<main.py / _processar_pagamento>', 'exec'), scope)
    return scope, scope['_processar_pagamento']


class V53FlaskMocks(unittest.TestCase):
    def setUp(self):
        self.g, self.process = load_processor()
        self.company = SimpleNamespace(id=1, status_pagamento='pendente', data_pagamento=None)
        self.db = MagicMock()
        self.g.update({
            '_mp_get_payment': MagicMock(return_value={'id': '100200', 'status': 'approved', 'external_reference': 'achetece:1:abc', 'payer': {'email': 'ficticio@example.invalid'}}),
            '_env_bool': lambda key, default=False: key in ('ED02_VIGENCIA_WRITE_HOMOLOG','ED01_HOMOLOGACAO','ED02_FINANCIAL_GUARD_HOMOLOG','ED02_CHECKOUT_LEDGER_HOMOLOG','ED02_EXACT_ONCE_HOMOLOG'),
            'db': self.db,
            'Empresa': SimpleNamespace(query=SimpleNamespace(get=lambda company_id: self.company)),
            'app': SimpleNamespace(logger=MagicMock()),
            'datetime': datetime,
        })

    def test_registra_periodo_sem_ativar_empresa_ou_enviar_email(self):
        self.g['_send_email'] = MagicMock()
        from ed02_payment_contract_guard import verify_payment_against_contract
        from ed02_5_transacao import registrar_periodo_homolog
        with patch('ed02_payment_contract_guard.verify_payment_against_contract', return_value={'empresa_id':1}), patch('ed02_5_transacao.registrar_periodo_homolog',return_value={'duplicado':False}) as register:
            result=self.process('100200')
        self.assertEqual(result['modo'],'ed02_5_vigencia_homologacao')
        self.assertFalse(result['ativou'])
        self.assertEqual(self.company.status_pagamento,'pendente')
        self.g['_send_email'].assert_not_called()
        register.assert_called_once()

    def test_replay_informa_duplicado_sem_ativar(self):
        with patch('ed02_payment_contract_guard.verify_payment_against_contract', return_value={'empresa_id':1}), patch('ed02_5_transacao.registrar_periodo_homolog',return_value={'duplicado':True}):
            result=self.process('100200')
        self.assertTrue(result['duplicado'])
        self.assertFalse(result['ativou'])

    def test_guarda_desligada_bloqueia_vigencia(self):
        self.g['_env_bool'] = lambda key,default=False: key in ('ED02_VIGENCIA_WRITE_HOMOLOG','ED02_EXACT_ONCE_HOMOLOG')
        self.g['_parse_empresa_id_from_external_reference']=lambda ref:1
        with self.assertRaisesRegex(RuntimeError,'configuracao de homologacao incompleta'):
            self.process('100200')
        self.db.session.commit.assert_not_called()

    def test_erro_de_persistencia_falha_fechada_sem_dados_sensiveis(self):
        with patch('ed02_payment_contract_guard.verify_payment_against_contract',return_value={'empresa_id':1}), patch('ed02_5_transacao.registrar_periodo_homolog',side_effect=RuntimeError('SEGREDO_FICTICIO')):
            with self.assertRaisesRegex(RuntimeError,'processamento nao confirmado') as ctx:
                self.process('100200')
        self.db.session.rollback.assert_called()
        logged=str(self.g['app'].logger.error.call_args)
        self.assertNotIn('SEGREDO_FICTICIO',logged)
        self.assertEqual(self.company.status_pagamento,'pendente')

if __name__ == '__main__': unittest.main()
