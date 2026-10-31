import unittest
from datetime import datetime, timedelta, timezone
from ed02_5_v58_consolidacao import LinhaConsolidacao
from ed02_5_v59_shadow import comparar_observacoes
from ed02_5_v59_gate import avaliar_gate, PREREQUISITOS, BLOQUEADORES_V59

NOW=datetime(2026,10,31,15,0,tzinfo=timezone.utc)


def linha(i, visible=False, status='pendente', periodos=(), conferido=False, data=None):
    return LinhaConsolidacao(empresa_id=i, ed01_visivel_observado=visible,
        status_pagamento=status, plano='mensal', data_pagamento_legado=data,
        periodos_ed02=periodos, financeiro_ed02_conferido=conferido)


class ShadowV59(unittest.TestCase):
    def test_pendente_nao_publica(self):
        s=comparar_observacoes([linha(1)],NOW)
        self.assertEqual(s['publicacoes_autorizadas'],0)
        self.assertEqual(s['empresas_auditadas'],1)
    def test_legado_visivel_sem_periodo_gera_alerta(self):
        s=comparar_observacoes([linha(2,True,'ativo',data=NOW-timedelta(days=2))],NOW)
        self.assertEqual(s['divergencias']['ED01_VISIVEL_REQUER_CONCILIACAO'],1)
    def test_ed02_vigente_sem_financeiro_nao_publica(self):
        s=comparar_observacoes([linha(3,False,'ativo',[(NOW-timedelta(days=1),NOW+timedelta(days=5))])],NOW)
        self.assertEqual(s['ed02_candidatos_diagnosticos'],0)
    def test_ed02_conferido_nao_concede_publicacao(self):
        s=comparar_observacoes([linha(3,False,'ativo',[(NOW-timedelta(days=1),NOW+timedelta(days=5))],True)],NOW)
        self.assertEqual(s['ed02_candidatos_diagnosticos'],1)
        self.assertEqual(s['publicacoes_autorizadas'],0)
    def test_empresa_repetida_bloqueada(self):
        with self.assertRaises(ValueError): comparar_observacoes([linha(1),linha(1)],NOW)
    def test_versao_release_nao_habilita(self):
        r=avaliar_gate({k:True for k in (*PREREQUISITOS,*BLOQUEADORES_V59)})
        self.assertFalse(r.permite_producao)
        self.assertFalse(r.permite_visibilidade_ed02)
        self.assertEqual(r.modo,'BLOQUEADO')
    def test_gate_desconhecido_bloqueia(self):
        with self.assertRaises(ValueError): avaliar_gate({'HABILITAR_PRODUCAO':True})
    def test_gate_tipo_invalido_bloqueia(self):
        with self.assertRaises(TypeError): avaliar_gate([])
        with self.assertRaises(ValueError): avaliar_gate({PREREQUISITOS[0]:'true'})


if __name__=='__main__':unittest.main()
