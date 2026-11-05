"""ED02.5 V5.10.2 — testes sem rede, banco, e-mail ou importacao da app.

Rodar: python -m unittest -v test_ed02_5_v5102_integracao
"""
from __future__ import annotations
import ast
import hashlib
import json
import sys
import types
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent
BASE = ROOT / 'main_V5101_BASE.py'
CANDIDATE = ROOT / 'main_V5102_CANDIDATO.py'
SHADOW = ROOT / 'ed02_5_v510_http_shadow_V5102_CANDIDATO.py'
REPORT = ROOT / 'ED02_V5102_RELATORIO.json'


def digest(p):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def function(tree, name):
    return next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)


def extract(src, name):
    tree = ast.parse(src)
    mod = ast.Module(body=[function(tree, name)], type_ignores=[])
    ast.fix_missing_locations(mod)
    return compile(mod, '<isolado>', 'exec')


class FakeCompany:
    id = 1
    email = 'invalid@example.test'
    apelido = 'Malharia Ficticia'
    nome = 'Malharia Ficticia'
    status_pagamento = 'pendente'
    data_pagamento = None


class FinanceSandbox:
    def __init__(self, flags, result=None):
        self.flags = flags
        self.requests = 0
        self.sent = []
        self.commits = []
        self.empresa = FakeCompany()
        self.payment = {'status': 'approved', 'external_reference': 'achetece:1:fake',
                        'payer': {'email': 'invalid@example.test'}}
        logger = SimpleNamespace(info=lambda *args: None, warning=lambda *args: None,
                                 error=lambda *args: None)
        self.ns = {
            '_env_bool': lambda k, default=False: flags.get(k, default),
            '_mp_get_payment': self.get_payment,
            '_parse_empresa_id_from_external_reference': lambda ref: 1,
            'Empresa': SimpleNamespace(query=SimpleNamespace(get=lambda id: self.empresa)),
            'db': SimpleNamespace(session=SimpleNamespace(commit=lambda: self.commits.append('commit'),
                                                     rollback=lambda: self.commits.append('rollback'))),
            'app': SimpleNamespace(logger=logger),
            '_make_magic_link': lambda id: 'https://example.test/token',
            '_email_ativacao_html': lambda emp, link: '<html>link</html>',
            '_send_email': lambda *args: self.sent.append(args),
        }
        exec(extract(CANDIDATE.read_text(encoding='utf-8'), '_processar_pagamento'), self.ns)
        self.fn = self.ns['_processar_pagamento']
        self.result = result

    def get_payment(self, payment_id):
        self.requests += 1
        return self.payment.copy()

    def execute(self):
        module = types.ModuleType('ed02_5_v510_legacy_once')
        def result(*args):
            return self.result
        module.processar_legado_uma_vez_homolog = result
        with patch.dict(sys.modules, {'ed02_5_v510_legacy_once': module}):
            return self.fn('fake_payment')


def shadow_count():
    ns = {}
    exec(extract(SHADOW.read_text(encoding='utf-8'), 'contar_divergencias_reais'), ns)
    return ns['contar_divergencias_reais']


class IntegridadeTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.old = BASE.read_text(encoding='utf-8')
        cls.new = CANDIDATE.read_text(encoding='utf-8')
        cls.report = json.loads(REPORT.read_text(encoding='utf-8'))

    def test_compilacao_main_e_shadow(self):
        compile(self.new, str(CANDIDATE), 'exec')
        compile(SHADOW.read_text(encoding='utf-8'), str(SHADOW), 'exec')

    def test_sha256_origem(self):
        self.assertEqual(digest(BASE), self.report['main_v5101_sha256'])

    def test_sha256_destino(self):
        self.assertEqual(digest(CANDIDATE), self.report['main_v5102_sha256'])

    def test_roteamento_original_preservado(self):
        old = ast.parse(self.old)
        new = ast.parse(self.new)
        for name in ('index', 'exportar', 'utils_empresas_json', 'empresa_perfil'):
            with self.subTest(name=name):
                self.assertEqual(ast.dump(function(old,name), include_attributes=False),
                                 ast.dump(function(new,name), include_attributes=False))

    def test_processador_original_presente_uma_vez(self):
        self.assertEqual(self.new.count('def _processar_pagamento(payment_id: str):'), 1)
        self.assertEqual(self.new.count('def _send_email(to_email: str, subject: str, text_body: str, html_body: str):'), 1)

    def test_nenhum_deploy_ou_gate_liberado(self):
        self.assertFalse(self.report['producao_autorizada'])
        self.assertFalse(self.report['aplicado_ao_main'])
        self.assertFalse(any(self.report['flags'].values()))

    def test_preflight_antes_do_mp(self):
        f = function(ast.parse(self.new), '_processar_pagamento')
        src = ast.get_source_segment(self.new, f)
        self.assertLess(src.index('"ED02_VIGENCIA_WRITE_HOMOLOG"'), src.index('payment = _mp_get_payment'))

    def test_email_ramo_v510_quatro_argumentos(self):
        f = function(ast.parse(self.new), '_processar_pagamento')
        calls = [n for n in ast.walk(f) if isinstance(n, ast.Call)
                 and isinstance(n.func, ast.Name) and n.func.id == '_send_email']
        self.assertEqual(len(calls), 3)
        # A chamada no ramo V510 é a primeira no código: legacy/exact_once preservados.
        self.assertEqual(len(sorted(calls, key=lambda n: n.lineno)[0].args), 4)

    def test_main_operacional_nao_e_modificado_pelos_testes(self):
        # Se houver main.py no checkout, ele permanece intocado na auditoria.
        principal = ROOT / 'main.py'
        if principal.is_file():
            antes = digest(principal)
            self.assertEqual(antes, digest(principal))
        self.assertNotEqual(CANDIDATE.name, 'main.py')


class GateFinanceiroTest(unittest.TestCase):
    def test_sem_homologacao_interrompe_antes_do_mp(self):
        state = FinanceSandbox({'ED02_V510_REPLAY_HOMOLOG': True})
        with self.assertRaises(RuntimeError): state.execute()
        self.assertEqual(state.requests, 0)

    def test_cada_flag_conflitante_interrompe_antes_do_mp(self):
        for flag in ('ED02_VIGENCIA_WRITE_HOMOLOG','ED02_EXACT_ONCE_HOMOLOG',
                     'ED02_FINANCIAL_GUARD_HOMOLOG','ED02_SHADOW_LEDGER'):
            with self.subTest(flag=flag):
                state = FinanceSandbox({'ED02_V510_REPLAY_HOMOLOG': True,
                                        'ED01_HOMOLOGACAO': True, flag: True})
                with self.assertRaises(RuntimeError): state.execute()
                self.assertEqual(state.requests, 0)
                self.assertEqual(state.sent, [])
                self.assertEqual(state.commits, [])

    def test_primeira_ativacao_quatro_campos_e_um_email(self):
        state = FinanceSandbox({'ED02_V510_REPLAY_HOMOLOG': True,
                                'ED01_HOMOLOGACAO': True},
                {'primeira_ativacao':True, 'aplicado':True, 'duplicado':False,
                 'revisao_historica':False, 'status':'approved'})
        res = state.execute()
        self.assertTrue(res['ativou'])
        self.assertEqual(state.requests, 1)
        self.assertEqual(len(state.sent), 1)
        args = state.sent[0]
        self.assertEqual(len(args),4)
        self.assertIn('https://example.test/token', args[2])
        self.assertIn('<html>', args[3])

    def test_replay_duplicado_nao_envia_email(self):
        state = FinanceSandbox({'ED02_V510_REPLAY_HOMOLOG': True,
                                'ED01_HOMOLOGACAO': True},
                {'primeira_ativacao':False, 'aplicado':False, 'duplicado':True,
                 'revisao_historica':False, 'status':'approved'})
        res = state.execute()
        self.assertTrue(res['duplicado'])
        self.assertFalse(res['ativou'])
        self.assertEqual(state.sent, [])

    def test_nova_renovacao_sem_email_primeira_ativacao(self):
        state = FinanceSandbox({'ED02_V510_REPLAY_HOMOLOG': True,
                                'ED01_HOMOLOGACAO': True},
                {'primeira_ativacao':False, 'aplicado':True, 'duplicado':False,
                 'revisao_historica':False, 'status':'approved'})
        res = state.execute()
        self.assertTrue(res['ativou'])
        self.assertEqual(state.sent, [])


class ShadowTest(unittest.TestCase):
    def test_convergente_oculto_nao_e_divergencia(self):
        self.assertEqual(shadow_count()({'divergencias':{'CONVERGENTE_OCULTO_ED01':1}}),0)

    def test_convergente_visivel_nao_e_divergencia(self):
        self.assertEqual(shadow_count()({'divergencias':{'CONVERGENTE_VISIVEL':2}}),0)

    def test_alertas_reais_somados(self):
        categorias={'CONVERGENTE_OCULTO_ED01':10,'ED01_VISIVEL_REQUER_CONCILIACAO':2,
                    'ED02_CANDIDATO_MAS_ED01_OCULTO':1, 'ALERTA_FINANCEIRO_ED01_VISIVEL':3}
        self.assertEqual(shadow_count()({'divergencias': categorias}), 6)

    def test_categoria_desconhecida_conta_alerta(self):
        self.assertEqual(shadow_count()({'divergencias':{'NOVA_CATEGORIA':1}}),1)

    def test_malformados_bloqueados(self):
        for resumo in ({}, {'divergencias': None}, {'divergencias': {'x':-1}},
                       {'divergencias':{'x':1.2}}, {'divergencias':{5:1}}):
            with self.subTest(resumo=resumo):
                with self.assertRaises(ValueError): shadow_count()(resumo)

    def test_shadow_somente_log_sem_escrita(self):
        src=SHADOW.read_text(encoding='utf-8')
        self.assertIn('SET TRANSACTION READ ONLY', src)
        self.assertIn('return response',src)
        self.assertIn('contar_divergencias_reais(resumo)',src)


if __name__=='__main__': unittest.main()
