"""ED02.3E — contrato de regressao, sem rede, banco ou Mercado Pago.

IMPORTANTE: este teste devera falhar no codigo legado ao detectar que
um pagamento aprovado repetido redefine data_pagamento. Nao habilitar
no workflow obrigatorio antes de implementar a correcao.
"""
import datetime
import unittest
from unittest.mock import Mock

from test_ed02_3d_fluxo_mock import ED023DFluxoMock


class ED023EContrato(ED023DFluxoMock):
    def test_replay_mesmo_payment_id_nao_muda_data_pagamento(self):
        self.fn('123456789')
        data_inicial = datetime.datetime(2025, 1, 1, 12, 0)
        self.empresa.data_pagamento = data_inicial
        self.fn('123456789')
        self.assertEqual(
            self.empresa.data_pagamento, data_inicial,
            'ED02.3E REPRODUZ: a confirmacao repetida prolongou indevidamente a vigencia.'
        )

    def test_primeira_confirmacao_ativa_sem_enviar_email_duplicado(self):
        self.fn('123456789')
        self.assertEqual(self.empresa.status_pagamento, 'ativo')
        self.assertIsNotNone(self.empresa.data_pagamento)
        self.env['_send_email'].assert_called_once()


if __name__ == '__main__':
    unittest.main(verbosity=2)
