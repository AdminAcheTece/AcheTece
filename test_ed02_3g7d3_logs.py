"""Contrato de seguranca: traceback de webhook nao deve registrar segredos."""
import ast
from pathlib import Path
import unittest

MAIN = Path(__file__).with_name('main.py')

class WebhookLogSafetyTests(unittest.TestCase):
    def test_webhook_nao_usa_exception_log_com_traceback(self):
        tree = ast.parse(MAIN.read_text(encoding='utf-8'))
        fn = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'webhook')
        for call in ast.walk(fn):
            if isinstance(call, ast.Call) and isinstance(call.func, ast.Attribute):
                self.assertNotEqual(call.func.attr, 'exception', 'Webhook nao deve imprimir traceback com segredos')
                if call.func.attr in ('error', 'warning', 'info'):
                    self.assertFalse(any(k.arg=='exc_info' for k in call.keywords), 'Webhook nao deve usar exc_info')

    def test_ambos_erros_fecham_sem_detalhes(self):
        s = MAIN.read_text(encoding='utf-8')
        self.assertIn('falha na verificação do banco ED02; detalhes omitidos', s)
        self.assertIn('falha interna no processamento; detalhes omitidos', s)

if __name__ == '__main__':
    unittest.main()
