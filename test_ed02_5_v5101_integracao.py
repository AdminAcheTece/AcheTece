"""ED02.5 V5.10.1: regressao do main.py REAL via AST; sem importacao da app.

Não conecta PostgreSQL, provedor ou SMTP. Gera candidato em memória e executa
exclusivamente a função original _processar_pagamento com dependências fakes.
"""
import ast
import os
import tempfile
import unittest
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from aplicar_ed02_v5101_main import gerar_candidato, salvar, _integrado
from aplicar_ed02_v510_main import REPLAY, INSERT_SHADOW

SOURCE_PATH = Path(__file__).with_name("main.py")


def original():
    return SOURCE_PATH.read_text(encoding="utf-8")


def candidato():
    return gerar_candidato(original())[0]


def extrair_processador(src: str):
    arvore=ast.parse(src)
    funcs=[x for x in arvore.body if isinstance(x,ast.FunctionDef) and x.name=='_processar_pagamento']
    if len(funcs)!=1: raise AssertionError("funcao alterada")
    mod=ast.Module(body=funcs, type_ignores=[])
    ast.fix_missing_locations(mod)
    return compile(mod,"main.py","exec")


class EmpresaFake:
    id=1
    email='financeiro@example.invalid'
    apelido='Empresa Teste'
    nome='Empresa Teste'
    status_pagamento='pendente'
    data_pagamento=None


class ProcessadorEmMemoria:
    def __init__(self, flags):
        self.flags=dict(flags)
        self.empresa=EmpresaFake()
        self.compromissos=[]
        self.emails=[]
        self.consultas=[]
        self.rec={"id":"00101", "status":"approved", "external_reference":"achetece:1:"+"a"*32,
                  "payer":{"email":"financeiro@example.invalid"}}
        self.logger=SimpleNamespace(info=lambda *a,**kw:None,
                                    warning=lambda *a,**kw:None,
                                    error=lambda *a,**kw:None)
        self.db=SimpleNamespace(session=SimpleNamespace(commit=lambda:self.compromissos.append("commit"),
                                                       rollback=lambda:self.compromissos.append("rollback")))
        fake_query=SimpleNamespace(get=lambda _id:self.empresa,
                                   filter=lambda *a,**kw:SimpleNamespace(first=lambda:self.empresa))
        fake_emp=SimpleNamespace(query=fake_query,email=SimpleNamespace(ilike=lambda v:v))
        self.ns={"_mp_get_payment":lambda _:dict(self.rec),
                 "_env_bool":lambda key, default=False:self.flags.get(key,default),
                 "_parse_empresa_id_from_external_reference":lambda _:1,
                 "Empresa":fake_emp,"db":self.db,"app":SimpleNamespace(logger=self.logger),
                 "datetime":datetime,
                 "_make_magic_link":lambda id:"link-inerte",
                 "_email_ativacao_html":lambda *a:"email-inerte",
                 "_send_email":lambda *a:self.emails.append(a),}
        exec(extrair_processador(candidato()),self.ns)
        self.fn=self.ns['_processar_pagamento']


class CandidatoV5101(unittest.TestCase):
    def test_candidato_compila(self):
        compile(candidato(),"main_V5101_CANDIDATO.py","exec")
    def test_main_original_preservado(self):
        before=SOURCE_PATH.read_bytes()
        gerar_candidato(original())
        self.assertEqual(before,SOURCE_PATH.read_bytes())
    def test_apenas_insercoes_estritas(self):
        src=original();can,modo=gerar_candidato(src)
        if not _integrado(src):
            self.assertEqual(can.replace(REPLAY,"",1).replace(INSERT_SHADOW,"",1),src)
        else:
            self.assertEqual(can,src)
    def test_segunda_integracao_nao_duplica(self):
        can,_=gerar_candidato(original())
        novamente,modo=gerar_candidato(can)
        self.assertEqual(can,novamente);self.assertEqual(modo,"ja_integrado")
    def test_patch_parcial_bloqueado(self):
        src=original()
        # Antes da instalacao, um marcador solto e um patch parcial.
        # Depois da integracao, repetir marcador nao representa patch parcial.
        # Simular perda de uma das duas ancoras ja presentes.
        if _integrado(src):
            danificado=src.replace('ED02_V510_HTTP_SHADOW_HOMOLOG', 'ED02_V510_HTTP_SHADOW_AUSENTE')
            self.assertNotEqual(src, danificado)
        else:
            danificado=src+"\n# ED02_V510_REPLAY_HOMOLOG\n"
        with self.assertRaises(RuntimeError):
            gerar_candidato(danificado)
    def test_ed01_sem_filtro_bloqueado(self):
        src=original().replace('Empresa.assinatura_ativa','Empresa.id',1)
        with self.assertRaises(RuntimeError): gerar_candidato(src)
    def test_sem_funcao_de_pagamento_bloqueado(self):
        src=original().replace('def _processar_pagamento(payment_id: str):',
                                'def _processar_pagamento_antigo(payment_id: str):',1)
        with self.assertRaises(RuntimeError): gerar_candidato(src)
    def test_grava_artefatos_sem_alterar_main(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);s=p/'main.py';s.write_text(original(),encoding='utf8')
            before=s.read_bytes()
            r=salvar(s,p/'main_V5101_CANDIDATO.py',p/'RELATORIO.json',p/'DIFF.patch')
            self.assertEqual(s.read_bytes(),before)
            self.assertFalse(r['aprovacao_producao'])
            self.assertTrue((p/'main_V5101_CANDIDATO.py').exists())
    def test_nao_sobrescreve_main(self):
        with tempfile.TemporaryDirectory() as d:
            p=Path(d);s=p/'main.py';s.write_text(original(),encoding='utf8')
            with self.assertRaises(RuntimeError): salvar(s,s,p/'rel.json',p/'diff.patch')
    def test_flags_desligadas_por_padrao(self):
        c=candidato()
        self.assertIn('_env_bool("ED02_V510_REPLAY_HOMOLOG", False)',c)
        self.assertIn('_env_bool("ED02_V510_HTTP_SHADOW_HOMOLOG", False)',c)
    def test_quatro_rotas_continuam_originais(self):
        a=ast.parse(original());b=ast.parse(candidato())
        for nome in ('index','exportar','utils_empresas_json','empresa_perfil'):
            get=lambda tree:next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name==nome)
            self.assertEqual(ast.dump(get(a),include_attributes=False),ast.dump(get(b),include_attributes=False))


class RamoFinanceiroFlaskV5101(unittest.TestCase):
    def test_flag_off_preserva_legado_sem_invocar_nucleo(self):
        state=ProcessadorEmMemoria({})
        with patch('ed02_5_v510_legacy_once.processar_legado_uma_vez_homolog',side_effect=AssertionError('nao chamar')):
            result=state.fn('00101')
        self.assertTrue(result['ativou']);self.assertEqual(state.compromissos,['commit'])
        self.assertEqual(len(state.emails),1)
    def test_sem_homologacao_bloqueia_antes_de_consultar_provedor(self):
        """A protecao V5102 permanece obrigatoria no codigo ja integrado.

        Nao usa banco, nao chama provedor e nao habilita nenhum modo real.
        """
        state=ProcessadorEmMemoria({'ED02_V510_REPLAY_HOMOLOG':True})
        def chamada_proibida(_):
            raise AssertionError('nao consultar provedor sem homologacao')
        state.ns['_mp_get_payment']=chamada_proibida
        with self.assertRaisesRegex(RuntimeError, 'autorizado apenas em homologacao'):
            state.fn('00101')
        self.assertEqual(state.compromissos,[])
        self.assertEqual(state.emails,[])

    def test_primeiro_pagamento_novo_caminho_so_um_email(self):
        state=ProcessadorEmMemoria({'ED02_V510_REPLAY_HOMOLOG':True,'ED01_HOMOLOGACAO':True})
        efeito={'primeira_ativacao':True,'aplicado':True,'duplicado':False,'revisao_historica':False,'status':'approved'}
        with patch('ed02_5_v510_legacy_once.processar_legado_uma_vez_homolog',return_value=efeito) as f:
            result=state.fn('00101')
        f.assert_called_once();self.assertEqual(state.compromissos,[])
        self.assertEqual(len(state.emails),1);self.assertTrue(result['ativou'])
    def test_replay_duplicado_nao_renova_e_nao_envia_email(self):
        state=ProcessadorEmMemoria({'ED02_V510_REPLAY_HOMOLOG':True,'ED01_HOMOLOGACAO':True})
        efeito={'primeira_ativacao':False,'aplicado':False,'duplicado':True,'revisao_historica':False,'status':'approved'}
        with patch('ed02_5_v510_legacy_once.processar_legado_uma_vez_homolog',return_value=efeito):
            result=state.fn('00101')
        self.assertTrue(result['duplicado']);self.assertEqual(state.compromissos,[])
        self.assertIsNone(state.empresa.data_pagamento);self.assertEqual(state.emails,[])
    def test_novo_pagamento_renova_sem_email_de_primeira_ativacao(self):
        state=ProcessadorEmMemoria({'ED02_V510_REPLAY_HOMOLOG':True,'ED01_HOMOLOGACAO':True})
        efeito={'primeira_ativacao':False,'aplicado':True,'duplicado':False,'revisao_historica':False,'status':'approved'}
        with patch('ed02_5_v510_legacy_once.processar_legado_uma_vez_homolog',return_value=efeito):
            result=state.fn('00202')
        self.assertTrue(result['ativou']);self.assertEqual(state.emails,[])
        self.assertEqual(state.compromissos,[])
    def test_revisao_historica_sem_ativar(self):
        state=ProcessadorEmMemoria({'ED02_V510_REPLAY_HOMOLOG':True,'ED01_HOMOLOGACAO':True})
        efeito={'primeira_ativacao':False,'aplicado':False,'duplicado':False,'revisao_historica':True,'status':'approved'}
        with patch('ed02_5_v510_legacy_once.processar_legado_uma_vez_homolog',return_value=efeito):
            result=state.fn('00303')
        self.assertTrue(result['revisao_historica']);self.assertFalse(result['ativou'])
        self.assertEqual(state.emails,[])
    def test_flags_financeiras_conflitantes_bloqueadas(self):
        state=ProcessadorEmMemoria({'ED02_V510_REPLAY_HOMOLOG':True,'ED01_HOMOLOGACAO':True,'ED02_EXACT_ONCE_HOMOLOG':True})
        with self.assertRaises(RuntimeError):state.fn('00101')
        self.assertEqual(state.compromissos,[])
    def test_erro_financeiro_nao_faz_fallback_legado(self):
        state=ProcessadorEmMemoria({'ED02_V510_REPLAY_HOMOLOG':True,'ED01_HOMOLOGACAO':True})
        with patch('ed02_5_v510_legacy_once.processar_legado_uma_vez_homolog',side_effect=RuntimeError('fail')):
            with self.assertRaises(RuntimeError):state.fn('00101')
        self.assertEqual(state.compromissos,[]);self.assertEqual(state.emails,[])


if __name__=='__main__':unittest.main()
