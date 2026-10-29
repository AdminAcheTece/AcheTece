"""ED02.3D: testes isolados da função real _processar_pagamento.

Extrai somente a função do main.py por AST; nunca importa/inicializa Flask,
nunca faz HTTP, não precisa de credenciais e não conecta ao PostgreSQL.
Documenta expressamente o problema legado de renovar a data em replays.
"""
import ast
import datetime as real_datetime
import pathlib
import sys
import types
import unittest
from unittest.mock import Mock, patch

ROOT = pathlib.Path(__file__).resolve().parent
MAIN = ROOT / 'main.py'


def load_payment_function():
    tree = ast.parse(MAIN.read_text(encoding='utf-8'), filename=str(MAIN))
    nodes = [node for node in tree.body if isinstance(node, ast.FunctionDef) and node.name == '_processar_pagamento']
    if len(nodes) != 1:
        raise AssertionError('Esperada uma única função _processar_pagamento no main.py')
    code = compile(ast.Module(body=nodes, type_ignores=[]), str(MAIN), 'exec')
    env = {'datetime': real_datetime.datetime}
    exec(code, env)
    return env['_processar_pagamento'], env


class FakeEmpresa:
    def __init__(self):
        self.id = 1
        self.email = 'empresa-ficticia@example.invalid'
        self.status_pagamento = 'pendente'
        self.data_pagamento = None


class ED023DFluxoMock(unittest.TestCase):
    def setUp(self):
        self.fn, self.env = load_payment_function()
        self.empresa = FakeEmpresa()
        self.shadow_calls = []
        self.payment = {'id': '123456789', 'status': 'approved', 'status_detail': 'accredited',
                        'external_reference': 'achetece:1:ed023d-fixture',
                        'currency_id': 'BRL', 'transaction_amount': 2.0,
                        'payer': {'email': self.empresa.email}}
        self.db = types.SimpleNamespace(session=types.SimpleNamespace(commit=Mock(), rollback=Mock()))
        self.env.update({
            'db': self.db,
            'Empresa': types.SimpleNamespace(query=types.SimpleNamespace(get=lambda i: self.empresa if i == 1 else None)),
            '_mp_get_payment': Mock(return_value=self.payment),
            '_parse_empresa_id_from_external_reference': lambda ref: 1 if ref.startswith('achetece:1:') else None,
            '_env_bool': Mock(side_effect=lambda name, default=False: False),
            '_make_magic_link': Mock(return_value='https://example.invalid/magic/token'),
            '_email_ativacao_html': Mock(return_value='<p>Teste</p>'),
            '_send_email': Mock(),
            'app': types.SimpleNamespace(logger=types.SimpleNamespace(info=Mock(), exception=Mock(), error=Mock())),
        })
        module = types.ModuleType('ed02_payment_shadow')
        module.shadow_record_payment = Mock(side_effect=lambda db, payment, empresa_id:
                                            self.shadow_calls.append((payment['id'], empresa_id)))
        self.module_patcher = patch.dict(sys.modules, {'ed02_payment_shadow': module})
        self.module_patcher.start()
        self.addCleanup(self.module_patcher.stop)

    def test_flag_off_does_not_invoke_shadow(self):
        result = self.fn('123456789')
        self.assertTrue(result['ok'])
        self.assertEqual(self.shadow_calls, [])
        self.env['_env_bool'].assert_called_with('ED02_SHADOW_LEDGER', False)
        self.env['_mp_get_payment'].assert_called_once_with('123456789')

    def test_flag_on_invokes_shadow_once_with_verified_provider_result(self):
        self.env['_env_bool'].side_effect = lambda name, default=False: name == 'ED02_SHADOW_LEDGER'
        self.fn('123456789')
        self.assertEqual(self.shadow_calls, [('123456789', 1)])

    def test_shadow_error_does_not_block_legacy_but_rolls_back(self):
        self.env['_env_bool'].side_effect = lambda name, default=False: name == 'ED02_SHADOW_LEDGER'
        sys.modules['ed02_payment_shadow'].shadow_record_payment.side_effect = RuntimeError('falha fictícia')
        result = self.fn('123456789')
        self.assertTrue(result['ok'])
        self.db.session.rollback.assert_called_once()
        self.env['app'].logger.error.assert_called_once_with(
            '[ED02 SHADOW] falha observacional; detalhes omitidos')
        self.env['app'].logger.exception.assert_not_called()

    def test_replayed_approved_payment_resets_legacy_payment_date__known_issue(self):
        self.env['_env_bool'].side_effect = lambda name, default=False: name == 'ED02_SHADOW_LEDGER'
        self.fn('123456789')
        self.empresa.data_pagamento = real_datetime.datetime(2025, 1, 1, 12, 0)
        self.fn('123456789')
        self.assertNotEqual(self.empresa.data_pagamento, real_datetime.datetime(2025, 1, 1, 12, 0),
                            'Contrato atual alterou: reavaliar risco de replay legado')
        self.assertEqual(len(self.shadow_calls), 2)
        self.env['_send_email'].assert_called_once()  # sem e-mail duplicado, mas vigência é reescrita

    def test_pending_does_not_create_shadow_when_disabled(self):
        self.payment['status'] = 'pending'
        result = self.fn('123456789')
        self.assertFalse(result['ativou'])
        self.assertEqual(self.empresa.status_pagamento, 'pendente')
        self.assertIsNone(self.empresa.data_pagamento)
        self.assertEqual(self.shadow_calls, [])


if __name__ == '__main__':
    unittest.main(verbosity=2)
