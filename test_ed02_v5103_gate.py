"""Testes estáticos e bloqueios V5.10.3; não acessam rede, banco ou Render."""
from __future__ import annotations
import ast
import json
import os
from pathlib import Path
import unittest
from unittest.mock import patch
import ed02_v5103_gate as gate

ROOT = Path(__file__).resolve().parent


class GateV5103Tests(unittest.TestCase):
    def test_main_candidato_sha(self):
        self.assertEqual(gate.sha_file(ROOT / 'main.py'), gate.TARGET_MAIN_HASH)

    def test_shadow_candidato_sha(self):
        self.assertEqual(gate.sha_file(ROOT / 'ed02_5_v510_http_shadow.py'),
                         gate.TARGET_SHADOW_HASH)

    def test_python_compila(self):
        for f in ('main.py','ed02_5_v510_http_shadow.py','ed02_v5103_gate.py'):
            compile((ROOT/f).read_text(encoding='utf-8'), f, 'exec')

    def test_quatro_rotas_nao_alteradas(self):
        old = (ROOT/'main_V5101_BASE.py').read_text(encoding='utf-8')
        new = (ROOT/'main.py').read_text(encoding='utf-8')
        for route in gate.FOUR_ROUTES:
            with self.subTest(route=route):
                self.assertEqual(gate.route_ast(old, route),gate.route_ast(new, route))

    def test_manifesto_nega_producao(self):
        m = json.loads((ROOT/'ED02_V5103_MANIFESTO.json').read_text(encoding='utf-8'))
        self.assertFalse(m['producao_autorizada'])
        self.assertFalse(m['render_alterado'])
        self.assertFalse(m['merge_autorizado'])

    def test_branch_incorrreta_bloqueada(self):
        with patch.dict(os.environ, {'GITHUB_REF_NAME':'main'},clear=True):
            with self.assertRaisesRegex(RuntimeError, 'somente branch'):
                gate.assert_safe_environment()

    def test_flag_financeira_bloqueada(self):
        for flag in gate.FLAGS:
            with self.subTest(flag=flag),patch.dict(os.environ,
                     {'GITHUB_REF_NAME':gate.CANDIDATE_BRANCH,flag:'true'},clear=True):
                with self.assertRaisesRegex(RuntimeError, 'flag financeira'):
                    gate.assert_safe_environment()

    def test_segredo_env_nao_e_permitido(self):
        for secret in gate.SENSITIVE_ENV:
            with self.subTest(secret=secret),patch.dict(os.environ,
                     {'GITHUB_REF_NAME':gate.CANDIDATE_BRANCH,secret:'fake'},clear=True):
                with self.assertRaisesRegex(RuntimeError, 'credencial inesperada'):
                    gate.assert_safe_environment()

    def test_ambiente_sanitizado_desliga_flags(self):
        e=gate.sanitized_environment()
        self.assertTrue(all(e[x]=='false' for x in gate.FLAGS))
        self.assertFalse(any(x in e for x in gate.SENSITIVE_ENV))

    def test_nao_ha_funcao_publica_duplicada(self):
        t=ast.parse((ROOT/'main.py').read_text(encoding='utf-8'))
        for name in (*gate.FOUR_ROUTES,'_processar_pagamento'):
            with self.subTest(name=name):
                ns=[x for x in t.body if isinstance(x,ast.FunctionDef) and x.name==name]
                self.assertEqual(len(ns),1)

    def test_manifesto_arquivos_exatos(self):
        m=json.loads((ROOT/'ED02_V5103_MANIFESTO.json').read_text(encoding='utf-8'))
        self.assertEqual(m['sha256']['main.py'],gate.TARGET_MAIN_HASH)
        self.assertEqual(m['sha256']['ed02_5_v510_http_shadow.py'],gate.TARGET_SHADOW_HASH)

    def test_escopo_limitado(self):
        self.assertTrue({'main.py','ed02_5_v510_http_shadow.py'}<=gate.ALLOWED_CHANGES)
        self.assertNotIn('.env',gate.ALLOWED_CHANGES)
        self.assertNotIn('requirements.txt',gate.ALLOWED_CHANGES)
        self.assertNotIn('Procfile',gate.ALLOWED_CHANGES)

    def test_modulos_regressao_nao_incluem_postgres_real(self):
        for module in gate.REGRESSION_MODULES:
            self.assertFalse(module.endswith('_pg_real'))
        self.assertNotIn('test_ed02_5_v5101_integracao',gate.REGRESSION_MODULES)

    def test_antes_do_mp_valida_flags(self):
        src=(ROOT/'main.py').read_text(encoding='utf-8')
        n=[x for x in ast.parse(src).body if isinstance(x,ast.FunctionDef) and x.name=='_processar_pagamento'][0]
        fn=ast.get_source_segment(src,n)
        self.assertLess(fn.index('ED02_VIGENCIA_WRITE_HOMOLOG'),fn.index('payment = _mp_get_payment'))

    def test_caminho_email_v510_exige_quatro_argumentos(self):
        src=(ROOT/'main.py').read_text(encoding='utf-8')
        n=[x for x in ast.parse(src).body if isinstance(x,ast.FunctionDef) and x.name=='_processar_pagamento'][0]
        calls=sorted((x for x in ast.walk(n) if isinstance(x,ast.Call) and
                     isinstance(x.func,ast.Name) and x.func.id=='_send_email'),key=lambda x:x.lineno)
        self.assertEqual(len(calls),3)
        self.assertEqual(len(calls[0].args),4)


if __name__ == '__main__':
    unittest.main()
