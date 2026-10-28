"""ED02.3G.2: testes de seguranca de respostas do webhook, sem rede ou banco."""
import unittest
from types import SimpleNamespace
from unittest.mock import Mock
from test_ed02_3g_rotas_mock import carregar_rota


class WebhookSeguroTests(unittest.TestCase):
    def setUp(self):
        self.webhook, self.env = carregar_rota('webhook')
        self.logger = SimpleNamespace(info=Mock(), warning=Mock(), error=Mock(), exception=Mock())
        self.processar = Mock(return_value={'ok': True, 'empresa_id': 1})
        self.env.update(request=SimpleNamespace(method='POST', args={'token': 'TOKEN_SECRETO'}),
                        _extract_payment_id=lambda req: ('ID_SENSIVEL', {'access_token': 'SEGREDO'}),
                        _processar_pagamento=self.processar,
                        app=SimpleNamespace(logger=self.logger), jsonify=lambda x: x)

    def test_sucesso_e_200_sem_exposicao_do_payload(self):
        body, status = self.webhook()
        self.assertEqual(status, 200)
        self.assertTrue(body['ok'])
        logs = str(self.logger.info.call_args_list)
        self.assertNotIn('TOKEN_SECRETO', logs)
        self.assertNotIn('SEGREDO', logs)
        self.assertNotIn('ID_SENSIVEL', logs)

    def test_erro_interno_503_e_sem_detalhes(self):
        self.processar.side_effect = RuntimeError('SENHA_SUPER_SECRETA')
        body, status = self.webhook()
        self.assertEqual(status, 503)
        self.assertEqual(body, {'ok': False, 'error': 'temporarily_unavailable'})
        self.assertNotIn('SENHA_SUPER_SECRETA', str(body))

    def test_resultado_sem_confirmacao_nao_retorna_200(self):
        self.processar.return_value = {'ok': False, 'detail': 'NAO_PROCESSADO'}
        body, status = self.webhook()
        self.assertEqual(status, 503)
        self.assertNotIn('NAO_PROCESSADO', str(body))

    def test_resultado_invalido_nao_retorna_200(self):
        self.processar.return_value = None
        body, status = self.webhook()
        self.assertEqual(status, 503)
        self.assertFalse(body['ok'])

    def test_sem_id_ignorado_sem_processamento(self):
        self.env['_extract_payment_id'] = lambda req: (None, {})
        body, status = self.webhook()
        self.assertEqual(status, 200)
        self.assertTrue(body['ignored'])
        self.processar.assert_not_called()

if __name__ == '__main__':
    unittest.main(verbosity=2)
