import inspect
import os
import unittest
from unittest.mock import patch
import test_ed02_5_v576_elegibilidade_rollback as v

class SafetyV576(unittest.TestCase):
    def test_sem_gatilho_bloqueia(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'BLOQUEADO'):
                v.configure()
    def test_producao_bloqueada(self):
        env = dict(ED02_V576_AUTHORIZED='true', GITHUB_EVENT_NAME='push',
                   GITHUB_REF='refs/heads/main', ED02_V576_COMMIT_MESSAGE=v.TRIGGER,
                   GITHUB_ACTOR='AdminAcheTece')
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'BLOQUEADO'):
                v.configure()
    def test_rollback_finalmente(self):
        source = inspect.getsource(v.run)
        self.assertIn('finally:', source)
        self.assertIn('tx.rollback()', source)
        self.assertIn('after == baseline', source)
    def test_quatro_rotas_originais(self):
        source = inspect.getsource(v.run)
        for route in ("get('/')", "'/exportar'", "'/utils/empresas_json?escopo=pagantes'", "f'/empresa/{ids[0]}'"):
            self.assertIn(route, source)
    def test_sem_commit(self):
        self.assertNotIn('.commit(', inspect.getsource(v.run))

if __name__ == '__main__': unittest.main()
