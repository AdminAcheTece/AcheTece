"""V5.10: testes de shadow que nunca publica e do gate histórico."""
from __future__ import annotations
import os
import sys
import types
import unittest
from unittest.mock import patch
from ed02_5_v510_historico import avaliar_conciliacao
from ed02_5_v510_http_shadow import escopo_filtros, instalar_shadow_http


class ShadowHistoricoV510(unittest.TestCase):
    def test_alvos_exatos_e_filtros_parametrizados(self):
        cond, par, join=escopo_filtros("index", {"tipo":"circular", "estado":"SC", "galga":"28"})
        self.assertTrue(join);self.assertIn("t.tipo",cond);self.assertEqual(par["galga"] if "galga" in par else par["filtro_finura"],28)
        self.assertNotIn("circular",cond)
    def test_perfil_com_id_invalido(self):
        with self.assertRaises(ValueError): escopo_filtros("empresa_perfil",{},-1)
    def test_rota_desconhecida_bloqueada(self):
        with self.assertRaises(ValueError): escopo_filtros("checkout",{})
    def test_filtro_injecao_sql_nao_entra_na_query(self):
        cond, par, _=escopo_filtros("exportar", {"tipo":"x';DROP TABLE empresa;--"})
        self.assertNotIn("DROP",cond);self.assertIn("DROP",par["tipo"])
    def test_uf_invalida_nao_entra(self):
        with self.assertRaises(ValueError): escopo_filtros("utils_empresas_json",{"uf":"SC;DROP"})
    def test_flag_off_nao_registra_hook(self):
        class FakeApp:
            def after_request(self,f): raise AssertionError("hook nao permitido")
        with patch.dict(os.environ, {"ED02_V510_HTTP_SHADOW_HOMOLOG":"false", "ED01_HOMOLOGACAO":"true"}):
            self.assertFalse(instalar_shadow_http(FakeApp(),object()))
    def test_hook_preserva_resposta_mesmo_se_auditoria_falha(self):
        class FakeApp:
            logger=types.SimpleNamespace(info=lambda *a:None, warning=lambda *a:None)
            callback=None
            def after_request(self,f): self.callback=f;return f
        a=FakeApp()
        fake_flask=types.ModuleType("flask")
        fake_flask.request=types.SimpleNamespace(endpoint="index", args=types.SimpleNamespace(to_dict=lambda **kw: {}),view_args={})
        with patch.dict(os.environ,{"ED02_V510_HTTP_SHADOW_HOMOLOG":"true","ED01_HOMOLOGACAO":"true"}),\
             patch.dict(sys.modules,{"flask":fake_flask}),\
             patch("ed02_5_v510_http_shadow.observar_http", side_effect=RuntimeError("segredo") ):
            self.assertTrue(instalar_shadow_http(a,object()))
            resposta=types.SimpleNamespace(status_code=200)
            self.assertIs(a.callback(resposta), resposta)
    def test_historico_com_prova_ainda_nao_publica(self):
        parecer=avaliar_conciliacao(ed01_status="ativo",data_legada_presente=True,
                 comprovante_verificado=True,referencia_unica=True,estorno_ou_disputa=False,conflito_de_periodos=False)
        self.assertEqual(parecer.situacao,"CANDIDATA_A_CONCILIACAO")
        self.assertFalse(parecer.permite_publicar);self.assertFalse(parecer.permite_migrar)
    def test_historico_estornado_bloqueia(self):
        parecer=avaliar_conciliacao(ed01_status="ativo",data_legada_presente=True,
                 comprovante_verificado=True,referencia_unica=True,estorno_ou_disputa=True,conflito_de_periodos=False)
        self.assertEqual(parecer.situacao,"BLOQUEADA")
    def test_historico_sem_prova_revisao(self):
        parecer=avaliar_conciliacao(ed01_status="ativo",data_legada_presente=True,
                 comprovante_verificado=False,referencia_unica=True,estorno_ou_disputa=False,conflito_de_periodos=False)
        self.assertEqual(parecer.situacao,"REVISAO_MANUAL")
    def test_historico_pendente_bloqueia(self):
        parecer=avaliar_conciliacao(ed01_status="pendente",data_legada_presente=True,
                 comprovante_verificado=True,referencia_unica=True,estorno_ou_disputa=False,conflito_de_periodos=False)
        self.assertEqual(parecer.situacao,"BLOQUEADA")

if __name__ == "__main__": unittest.main()
