import unittest
from datetime import datetime, timedelta, timezone
from ed02_5_v57_politica import CasoHistorico, avaliar, PERMITE_PUBLICACAO_AUTOMATICA

NOW = datetime(2026, 10, 30, 12, tzinfo=timezone.utc)

def caso(**overrides):
    dados = dict(status='ativo', plano='mensal', data_pagamento=None, periodos=())
    dados.update(overrides)
    return CasoHistorico(**dados)

class PoliticaHistoricaV57(unittest.TestCase):
    def test_pendente_mesmo_com_periodo_bloqueia(self):
        self.assertEqual(avaliar(caso(status='pendente', periodos=((NOW-timedelta(days=1), NOW+timedelta(days=1)),)), NOW), 'BLOQUEADO_STATUS')
    def test_sem_prova_nao_publica(self):
        self.assertEqual(avaliar(caso(), NOW), 'SEM_PROVA_FINANCEIRA')
    def test_legado_sem_conciliacao(self):
        self.assertEqual(avaliar(caso(data_pagamento=NOW-timedelta(days=40)), NOW), 'LEGADO_A_VALIDAR')
    def test_legado_com_direito_comprovado_ainda_nao_migra(self):
        self.assertEqual(avaliar(caso(data_pagamento=NOW-timedelta(days=2), direito_herdado_verificado=True), NOW), 'LEGADO_VALIDADO_AGUARDA_MIGRACAO')
    def test_ed02_vigente_apenas_diagnostico(self):
        self.assertEqual(avaliar(caso(periodos=((NOW-timedelta(days=1), NOW+timedelta(days=1)),)), NOW), 'ED02_VIGENTE_AUDITORIA')
    def test_periodo_vencido(self):
        self.assertEqual(avaliar(caso(periodos=((NOW-timedelta(days=32), NOW),)), NOW), 'ED02_SEM_VIGENCIA_ATUAL')
    def test_estorno_exige_revisao(self):
        self.assertEqual(avaliar(caso(estorno_confirmado=True, periodos=((NOW-timedelta(days=1), NOW+timedelta(days=1)),)), NOW), 'REVISAO_FINANCEIRA_OBRIGATORIA')
    def test_disputa_exige_revisao(self):
        self.assertEqual(avaliar(caso(disputa_financeira=True), NOW), 'REVISAO_FINANCEIRA_OBRIGATORIA')
    def test_inconsistencia_de_data(self):
        self.assertEqual(avaliar(caso(data_pagamento=datetime(2026,10,1)), NOW), 'REVISAO_DATA_LEGADA')
    def test_periodo_invalido(self):
        self.assertEqual(avaliar(caso(periodos=((NOW,NOW),)), NOW), 'REVISAO_PERIODO_INVALIDO')
    def test_plano_invalido(self):
        self.assertEqual(avaliar(caso(plano='outro'), NOW), 'REVISAO_PLANO')
    def test_sem_fuso_bloqueado(self):
        with self.assertRaises(ValueError): avaliar(caso(), datetime(2026,10,30))
    def test_nenhuma_classificacao_publica_sozinha(self):
        self.assertEqual(PERMITE_PUBLICACAO_AUTOMATICA, frozenset())

if __name__ == '__main__': unittest.main()
