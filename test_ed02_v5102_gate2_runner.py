"""ED02.5 V5.10.2 Gate 2 - testes isolados, sem app/DB/rede."""
from __future__ import annotations
import hashlib
import os
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
import ed02_v5102_gate2_runner as gate


class Gate2Safety(unittest.TestCase):
    def test_rota_sem_duplicatas(self):
        src='def index():\n    return 1\n'
        self.assertIn('FunctionDef', gate.function_tree(src, 'index'))
        with self.assertRaises(RuntimeError):
            gate.function_tree(src + src, 'index')

    def test_rota_ausente_bloqueia(self):
        with self.assertRaises(RuntimeError):
            gate.function_tree('pass\n', 'exportar')

    def test_ambiente_producao_recusado(self):
        with patch.dict(os.environ, {'GITHUB_REF_NAME': 'main'}, clear=True):
            with self.assertRaises(RuntimeError):
                gate.assert_safe_environment()

    def test_ambiente_homologacao_real_recusado(self):
        with patch.dict(os.environ, {'GITHUB_REF_NAME': 'ed02-assinaturas-vigencia-ledger'}, clear=True):
            with self.assertRaises(RuntimeError):
                gate.assert_safe_environment()

    def test_branch_auditoria_permitida(self):
        with patch.dict(os.environ, {'GITHUB_REF_NAME': 'ed02-v5102-gate2-auditoria'}, clear=True):
            gate.assert_safe_environment()

    def test_flags_ativas_bloqueadas(self):
        for name in gate.GUARDED_FLAGS:
            with self.subTest(name=name):
                with patch.dict(os.environ, {name: 'true'}, clear=True):
                    with self.assertRaises(RuntimeError):
                        gate.assert_safe_environment()

    def test_credenciais_presentes_bloqueadas(self):
        for name in gate.SENSITIVE:
            with self.subTest(name=name):
                with patch.dict(os.environ, {name: 'TEST_ONLY_PLACEHOLDER'}, clear=True):
                    with self.assertRaises(RuntimeError):
                        gate.assert_safe_environment()

    def test_clean_env_remove_segredos(self):
        with patch.dict(os.environ, {'DATABASE_URL': 'dummy', 'MP_ACCESS_TOKEN': 'dummy', 'ED02_V510_REPLAY_HOMOLOG': 'true'}, clear=True):
            env = gate.clean_environment()
            self.assertNotIn('DATABASE_URL', env)
            self.assertNotIn('MP_ACCESS_TOKEN', env)
            self.assertEqual(env['ED02_V510_REPLAY_HOMOLOG'], 'false')

    def test_hash_arquivo_normal(self):
        with tempfile.TemporaryDirectory() as d:
            p = Path(d)/'ok.py';p.write_bytes(b'x')
            self.assertEqual(gate.digest(p), hashlib.sha256(b'x').hexdigest())

    def test_hash_ausente_recusado(self):
        with tempfile.TemporaryDirectory() as d:
            with self.assertRaises(RuntimeError):
                gate.digest(Path(d)/'missing.py')

    def test_regressao_nao_requer_modulos_de_postgres_real(self):
        names=' '.join(gate.REGRESSION_MODULES)
        self.assertNotIn('test_ed02_5_v510_pg_rollback', names)
        self.assertNotIn('test_ed02_5_v5101_pg_http', names)


if __name__ == '__main__':
    unittest.main()
