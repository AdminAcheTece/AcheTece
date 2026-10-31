import unittest
from dataclasses import replace
from datetime import datetime, timedelta, timezone

from ed02_5_v58_consolidacao import (
    LinhaConsolidacao, PUBLICACAO_AUTOMATICA_AUTORIZADA,
    classificar, comparar, resumo_agrupado,
)

NOW = datetime(2026, 10, 31, 12, tzinfo=timezone.utc)


def amostra(**overrides):
    x = LinhaConsolidacao(empresa_id=1, ed01_visivel_observado=False,
                          status_pagamento="ativo", plano="mensal",
                          data_pagamento_legado=None)
    return replace(x, **overrides)


def periodo(inicio=-1, fim=1):
    return (NOW + timedelta(days=inicio), NOW + timedelta(days=fim))


class ConsolidacaoV58Tests(unittest.TestCase):
    def test_ed01_pendente_com_periodo_positivo(self):
        x = amostra(status_pagamento="pendente", periodos_ed02=(periodo(),), financeiro_ed02_conferido=True)
        self.assertEqual(classificar(x, NOW), "BLOQUEADO_STATUS")
        self.assertFalse(comparar(x, NOW).autoriza_publicar)

    def test_ed02_vigente_confirmado_apenas_diagnostico(self):
        p = comparar(amostra(periodos_ed02=(periodo(),), financeiro_ed02_conferido=True), NOW)
        self.assertTrue(p.ed02_candidato_diagnostico)
        self.assertFalse(p.autoriza_publicar)
        self.assertEqual(p.divergencia, "ED02_CANDIDATO_MAS_ED01_OCULTO")

    def test_periodo_sem_confirmacao_financeira(self):
        self.assertEqual(classificar(amostra(periodos_ed02=(periodo(),)), NOW), "ED02_VIGENTE_SEM_CONFERENCIA")

    def test_fim_exclusivo_nao_vigente(self):
        self.assertEqual(classificar(amostra(periodos_ed02=(periodo(-1, 0),), financeiro_ed02_conferido=True), NOW),
                         "ED02_SEM_VIGENCIA_ATUAL")

    def test_periodo_futuro(self):
        self.assertEqual(classificar(amostra(periodos_ed02=(periodo(1, 2),)), NOW), "ED02_SEM_VIGENCIA_ATUAL")

    def test_periodo_sobreposto_revisao(self):
        x = amostra(periodos_ed02=(periodo(-2, 1), periodo(0, 4)))
        self.assertEqual(classificar(x, NOW), "REVISAO_SOBREPOSICAO")

    def test_periodos_adjacentes_permitidos(self):
        x = amostra(periodos_ed02=(periodo(-2, 0), periodo(0, 1)), financeiro_ed02_conferido=True)
        self.assertEqual(classificar(x, NOW), "ED02_VIGENTE_CONFERIDO_AUDITORIA")

    def test_periodo_invertido(self):
        self.assertEqual(classificar(amostra(periodos_ed02=(periodo(1, -1),)), NOW), "REVISAO_PERIODO_INVALIDO")

    def test_periodo_sem_timezone(self):
        self.assertEqual(classificar(amostra(periodos_ed02=((NOW.replace(tzinfo=None), NOW),)), NOW),
                         "REVISAO_PERIODO_INVALIDO")

    def test_data_legada_sem_timezone(self):
        self.assertEqual(classificar(amostra(data_pagamento_legado=NOW.replace(tzinfo=None)), NOW),
                         "REVISAO_DATA_LEGADA")

    def test_legado_a_conciliar_nao_publica(self):
        p = comparar(amostra(ed01_visivel_observado=True, data_pagamento_legado=NOW), NOW)
        self.assertEqual(p.classificacao, "LEGADO_A_CONCILIAR")
        self.assertEqual(p.divergencia, "ED01_VISIVEL_REQUER_CONCILIACAO")
        self.assertFalse(p.autoriza_publicar)

    def test_legado_conciliado_ainda_nao_migra(self):
        x = amostra(data_pagamento_legado=NOW, legado_conciliado=True)
        self.assertEqual(classificar(x, NOW), "LEGADO_CONCILIADO_AGUARDA_TRANSICAO")

    def test_estorno_prioridade(self):
        p = comparar(amostra(ed01_visivel_observado=True, estorno_confirmado=True,
                              periodos_ed02=(periodo(),), financeiro_ed02_conferido=True), NOW)
        self.assertEqual(p.divergencia, "ALERTA_FINANCEIRO_ED01_VISIVEL")
        self.assertFalse(p.ed02_candidato_diagnostico)

    def test_disputa_exige_revisao(self):
        self.assertEqual(classificar(amostra(disputa_financeira=True), NOW), "REVISAO_FINANCEIRA_URGENTE")

    def test_ausencia_pagamento(self):
        self.assertEqual(classificar(amostra(), NOW), "SEM_EVIDENCIA_FINANCEIRA")

    def test_plano_desconhecido(self):
        self.assertEqual(classificar(amostra(plano="premium"), NOW), "REVISAO_PLANO")

    def test_ids_invalidos(self):
        for item in (0, -1, True, "1"):
            with self.subTest(item=item), self.assertRaises(ValueError):
                classificar(amostra(empresa_id=item), NOW)

    def test_flags_invalidas(self):
        with self.assertRaises(ValueError):
            classificar(amostra(financeiro_ed02_conferido="true"), NOW)
        with self.assertRaises(ValueError):
            classificar(amostra(ed01_visivel_observado=1), NOW)

    def test_agora_sem_fuso(self):
        with self.assertRaises(ValueError):
            classificar(amostra(), NOW.replace(tzinfo=None))

    def test_resumo_so_agrupado_e_zero_autorizacao(self):
        r = resumo_agrupado((comparar(amostra(empresa_id=10), NOW),
                             comparar(amostra(empresa_id=20, status_pagamento="pendente"), NOW)))
        self.assertEqual(r["quantidade"], 2)
        self.assertEqual(r["autorizacoes_publicas"], 0)
        self.assertNotIn("empresa_id", r)
        self.assertNotIn("10", str(r))
        self.assertFalse(PUBLICACAO_AUTOMATICA_AUTORIZADA)


if __name__ == "__main__":
    unittest.main()
