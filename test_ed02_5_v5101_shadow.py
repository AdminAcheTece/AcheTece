"""V5.10.1: contrato do shadow HTTP sem Flask/DB externos."""
import os
import sys
import types
import unittest
from unittest.mock import patch

from ed02_5_v510_http_shadow import instalar_shadow_http, ALVOS


class FakeApp:
    def __init__(self):
        self.handlers=[]
        self.info=[];self.warn=[]
        self.logger=types.SimpleNamespace(info=lambda *a:self.info.append(a),
                                          warning=lambda *a:self.warn.append(a))
    def after_request(self,fn): self.handlers.append(fn);return fn


class Resp:
    status_code=200
    body=b'corpo original'
    headers={'Cache-Control':'no-cache'}


class ShadowV5101(unittest.TestCase):
    def test_alvos_sao_quatro_rotas_publicas(self):
        self.assertEqual(ALVOS, frozenset({'index','exportar','utils_empresas_json','empresa_perfil'}))
    def test_flag_off_nao_registra(self):
        a=FakeApp()
        with patch.dict(os.environ,{'ED02_V510_HTTP_SHADOW_HOMOLOG':'false','ED01_HOMOLOGACAO':'true'}):
            self.assertFalse(instalar_shadow_http(a,None))
        self.assertEqual(a.handlers,[])
    def test_ambiente_homologacao_necessario(self):
        a=FakeApp()
        with patch.dict(os.environ,{'ED02_V510_HTTP_SHADOW_HOMOLOG':'true','ED01_HOMOLOGACAO':'false'}):
            self.assertFalse(instalar_shadow_http(a,None))
        self.assertEqual(a.handlers,[])
    def test_alvo_fora_do_escopo_ignorado(self):
        a=FakeApp();req=types.SimpleNamespace(endpoint='checkout')
        fake_flask=types.ModuleType('flask');fake_flask.request=req
        with patch.dict(os.environ,{'ED02_V510_HTTP_SHADOW_HOMOLOG':'true','ED01_HOMOLOGACAO':'true'}),\
             patch.dict(sys.modules,{'flask':fake_flask}),\
             patch('ed02_5_v510_http_shadow.observar_http',side_effect=AssertionError('no')):
            self.assertTrue(instalar_shadow_http(a,None))
            r=Resp();self.assertIs(a.handlers[0](r),r)
        self.assertFalse(a.info)
    def test_http_500_ignorado(self):
        a=FakeApp();req=types.SimpleNamespace(endpoint='index')
        fake_flask=types.ModuleType('flask');fake_flask.request=req
        with patch.dict(os.environ,{'ED02_V510_HTTP_SHADOW_HOMOLOG':'true','ED01_HOMOLOGACAO':'true'}),\
             patch.dict(sys.modules,{'flask':fake_flask}),\
             patch('ed02_5_v510_http_shadow.observar_http',side_effect=AssertionError('no')):
            instalar_shadow_http(a,None)
            r=Resp();r.status_code=503;self.assertIs(a.handlers[0](r),r)
    def test_todas_as_quatro_rotas_observadas_sem_mudar_resposta(self):
        a=FakeApp()
        req=types.SimpleNamespace(endpoint='index',args=types.SimpleNamespace(to_dict=lambda **k:{'estado':'SC'}),
                                  view_args={'empresa_id':1})
        fake_flask=types.ModuleType('flask');fake_flask.request=req
        chamadas=[]
        def audit(db,endpoint,args,id=None):
            chamadas.append((endpoint,args,id))
            return {'empresas_auditadas':1,'divergencias':{},'publicacoes_autorizadas':0}
        with patch.dict(os.environ,{'ED02_V510_HTTP_SHADOW_HOMOLOG':'true','ED01_HOMOLOGACAO':'true'}),\
             patch.dict(sys.modules,{'flask':fake_flask}),\
             patch('ed02_5_v510_http_shadow.observar_http',side_effect=audit):
            instalar_shadow_http(a,None)
            r=Resp()
            for endpoint in sorted(ALVOS):
                req.endpoint=endpoint
                self.assertIs(a.handlers[0](r),r)
                self.assertEqual(r.body,b'corpo original')
            self.assertEqual({x[0] for x in chamadas},ALVOS)
            self.assertEqual(len(a.info),4)
    def test_erro_na_observacao_nao_vaza_ou_modifica_resposta(self):
        a=FakeApp()
        req=types.SimpleNamespace(endpoint='index',args=types.SimpleNamespace(to_dict=lambda **k:{}),view_args={})
        fake_flask=types.ModuleType('flask');fake_flask.request=req
        with patch.dict(os.environ,{'ED02_V510_HTTP_SHADOW_HOMOLOG':'true','ED01_HOMOLOGACAO':'true'}),\
             patch.dict(sys.modules,{'flask':fake_flask}),\
             patch('ed02_5_v510_http_shadow.observar_http',side_effect=RuntimeError('SENHA_INDEVIDA')):
            instalar_shadow_http(a,None)
            r=Resp();self.assertIs(a.handlers[0](r),r)
        self.assertEqual(len(a.warn),1)
        self.assertNotIn('SENHA_INDEVIDA',str(a.warn))
    def test_gatilho_financeiro_nao_altera_logica_publica(self):
        from aplicar_ed02_v5101_main import gerar_candidato
        from pathlib import Path
        import ast
        src=Path(__file__).with_name('main.py').read_text(encoding='utf8')
        can,_=gerar_candidato(src)
        get=lambda s,n:next(x for x in ast.parse(s).body if isinstance(x,ast.FunctionDef) and x.name==n)
        for route in ALVOS:
            self.assertEqual(ast.dump(get(src,route),include_attributes=False),
                             ast.dump(get(can,route),include_attributes=False))

if __name__=='__main__':unittest.main()
