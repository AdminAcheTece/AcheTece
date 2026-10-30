import unittest
from datetime import datetime, timedelta, timezone
from ed02_5_v56_elegibilidade import classificar

NOW = datetime(2026,10,30,tzinfo=timezone.utc)
class ElegibilidadeV56(unittest.TestCase):
    def test_pendente_com_periodo_nao_elegivel(self):
        self.assertEqual(classificar('pendente', None, [(NOW-timedelta(days=1), NOW+timedelta(days=1))], NOW), 'BLOQUEADO_STATUS')
    def test_ativo_com_periodo_vigente(self):
        self.assertEqual(classificar('ativo', None, [(NOW-timedelta(days=1), NOW+timedelta(days=1))], NOW), 'ED02_VIGENTE_AUDITORIA')
    def test_fim_exclusivo_expira(self):
        self.assertEqual(classificar('ativo', None, [(NOW-timedelta(days=1), NOW)], NOW), 'ED02_SEM_VIGENCIA_ATUAL')
    def test_historico_sem_vigencia_requer_conciliacao(self):
        self.assertEqual(classificar('ativo', NOW, [], NOW), 'HISTORICO_REQUER_CONCILIACAO')
    def test_sem_pagamento_sem_vigencia(self):
        self.assertEqual(classificar('ativo', None, [], NOW), 'SEM_COMPROVACAO_FINANCEIRA')
    def test_periodo_ilegal_bloqueia(self):
        self.assertEqual(classificar('ativo', None, [(NOW,NOW)], NOW), 'REVISAR_PERIODO_INVALIDO')
    def test_data_sem_fuso_bloqueia(self):
        with self.assertRaises(ValueError): classificar('ativo', None, [], NOW.replace(tzinfo=None))
if __name__ == '__main__': unittest.main()
