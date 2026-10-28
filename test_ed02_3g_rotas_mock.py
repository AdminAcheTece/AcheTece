"""ED02.3G: teste integrado de rotas Flask extraidas de main.py por AST.
Sem rede, MP, DB, envio de email ou modificacoes em producao.
Verifica comportamento de rotas existentes; nao habilita flags.
"""
import ast
from pathlib import Path
import unittest
from unittest.mock import Mock
from types import SimpleNamespace

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / 'main.py'


def carregar_rota(nome):
    arvore = ast.parse(MAIN.read_text(encoding='utf-8'))
    funcao = next(n for n in arvore.body if isinstance(n, ast.FunctionDef) and n.name == nome)
    funcao.decorator_list = []
    ambiente = {}
    exec(compile(ast.Module(body=[funcao], type_ignores=[]), str(MAIN), 'exec'), ambiente)
    return ambiente[nome], ambiente


class ED023GRotasMock(unittest.TestCase):
    def setUp(self):
        self.webhook, self.envw = carregar_rota('webhook')
        self.retorno, self.envr = carregar_rota('pagamento_aprovado')
        self.processar = Mock(return_value={'ok': True, 'empresa_id': 1, 'duplicado': False})
        self.logger = SimpleNamespace(info=Mock(), warning=Mock(), exception=Mock())
        self.jsonify = lambda obj: obj
        self.envw.update(app=SimpleNamespace(logger=self.logger), jsonify=self.jsonify,
                         _processar_pagamento=self.processar,
                         _extract_payment_id=lambda req: ('123456', {'id': '123456'}))
        self.envr.update(app=SimpleNamespace(logger=self.logger),
                         _processar_pagamento=self.processar,
                         render_template=lambda nome: nome)

    def test_webhook_get_nao_processa(self):
        self.envw['request'] = SimpleNamespace(method='GET')
        resposta, status = self.webhook()
        self.assertEqual(status, 200)
        self.assertTrue(resposta['ok'])
        self.processar.assert_not_called()

    def test_webhook_post_sem_identificador_ignora(self):
        self.envw['request'] = SimpleNamespace(method='POST', args={})
        self.envw['_extract_payment_id'] = lambda req: (None, {})
        resposta, status = self.webhook()
        self.assertEqual(status, 200)
        self.assertTrue(resposta['ignored'])
        self.processar.assert_not_called()

    def test_webhook_post_chama_processamento_uma_vez(self):
        self.envw['request'] = SimpleNamespace(method='POST', args={})
        resposta, status = self.webhook()
        self.assertEqual(status, 200)
        self.assertEqual(resposta['empresa_id'], 1)
        self.processar.assert_called_once_with('123456')

    def test_retorno_com_payment_id_chama_mesmo_processador(self):
        self.envr['request'] = SimpleNamespace(args={'payment_id': '123456'})
        pagina = self.retorno()
        self.assertEqual(pagina, 'pagamento_aprovado.html')
        self.processar.assert_called_once_with('123456')

    def test_retorno_sem_payment_id_nao_processa(self):
        self.envr['request'] = SimpleNamespace(args={})
        self.assertEqual(self.retorno(), 'pagamento_aprovado.html')
        self.processar.assert_not_called()

    def test_falha_no_webhook_nao_ativa_empresa(self):
        self.envw['request'] = SimpleNamespace(method='POST', args={})
        self.processar.side_effect = RuntimeError('FALHA_SIMULADA_INTERNA')
        resposta, status = self.webhook()
        self.assertEqual(status, 200)
        self.assertEqual(self.processar.call_count, 1)
        self.assertTrue(resposta.get('ok'))
        # Problema legado conhecido: resposta inclui detalhes internos.
        # A correcao deve substituir este teste por assertNotIn.
        self.assertIn('FALHA_SIMULADA_INTERNA', resposta.get('error', ''))


if __name__ == '__main__':
    unittest.main(verbosity=2)
