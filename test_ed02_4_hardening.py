"""ED02.4: contrato de segurança estático/isolado, sem banco nem chamadas de rede."""
import ast
from pathlib import Path
import unittest
from types import SimpleNamespace
from unittest.mock import Mock

ROOT = Path(__file__).resolve().parent
SRC = (ROOT / 'main.py').read_text(encoding='utf-8')
TREE = ast.parse(SRC)

def rota(name):
    fn = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == name)
    fn.decorator_list = []
    env = {}
    exec(compile(ast.Module(body=[fn], type_ignores=[]), str(ROOT / 'main.py'), 'exec'), env)
    return env[name], env

class ED024HardeningTests(unittest.TestCase):
    def setUp(self):
        self.fn, self.env = rota('pagamento_aprovado')
        self.env.update(request=SimpleNamespace(args={}),
                        _processar_pagamento=Mock(), render_template=lambda name: name,
                        app=SimpleNamespace(logger=SimpleNamespace(info=Mock(), error=Mock())))

    def test_sem_id_nao_mostra_aprovado(self):
        self.assertEqual(self.fn(), 'pagamento_pendente.html')
        self.env['_processar_pagamento'].assert_not_called()

    def test_aprovado_confirmado(self):
        self.env['request'].args = {'payment_id': '123'}
        self.env['_processar_pagamento'].return_value = {'ok': True, 'ativou': True}
        self.assertEqual(self.fn(), 'pagamento_aprovado.html')

    def test_idempotente_confirmado(self):
        self.env['request'].args = {'payment_id': '123'}
        self.env['_processar_pagamento'].return_value = {'ok': True, 'ativou': False, 'duplicado': True}
        self.assertEqual(self.fn(), 'pagamento_aprovado.html')

    def test_pendente_ou_erro_nao_mostra_aprovado(self):
        self.env['request'].args = {'payment_id': '123'}
        for ret in ({'ok': True, 'ativou': False}, {'ok': False}, None):
            self.env['_processar_pagamento'].return_value = ret
            self.assertEqual(self.fn(), 'pagamento_pendente.html')
        self.env['_processar_pagamento'].side_effect = RuntimeError('SENHA_FICTICIA_SECRETA')
        self.assertEqual(self.fn(), 'pagamento_pendente.html')
        self.assertNotIn('SENHA_FICTICIA_SECRETA', str(self.env['app'].logger.error.call_args_list))

    def test_logs_financeiros_sem_payload_ou_exception(self):
        pay = SRC[SRC.index('def _processar_pagamento('):SRC.index("@app.route('/checkout')")]
        check = SRC[SRC.index("@app.route('/checkout')"):SRC.index('@app.route("/webhook"')]
        for section in (pay,check):
            self.assertNotIn('logger.exception(', section)
            self.assertNotIn('Resposta MP:', section)
            self.assertNotIn('payment_id={payment_id}', section)
            self.assertNotIn('ext_ref={ext_ref}', section)

    def test_retorno_legado_nao_mostra_aprovado(self):
        fn,env=rota('pagamento_sucesso')
        env['render_template']=lambda name:name
        self.assertEqual(fn(), 'pagamento_pendente.html')

if __name__ == '__main__':
    unittest.main(verbosity=2)
