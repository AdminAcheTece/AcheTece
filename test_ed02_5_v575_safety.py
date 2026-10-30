import os
import unittest
from unittest.mock import patch
from test_ed02_5_v575_rotas_originais_pg import configure

class V575Safety(unittest.TestCase):
    def test_sem_autorizacao(self):
        with patch.dict(os.environ, {}, clear=True), self.assertRaisesRegex(RuntimeError, 'autorizacao'):
            configure()

    def test_branch_errada(self):
        with patch.dict(os.environ, {'ED02_V575_READONLY_AUTHORIZED': 'true', 'GITHUB_REF': 'refs/heads/main'}, clear=True), self.assertRaisesRegex(RuntimeError, 'branch'):
            configure()

    def test_sem_gatilho(self):
        with patch.dict(os.environ, {'ED02_V575_READONLY_AUTHORIZED':'true', 'GITHUB_REF':'refs/heads/ed02-assinaturas-vigencia-ledger', 'GITHUB_EVENT_NAME':'push'}, clear=True), self.assertRaisesRegex(RuntimeError, 'commit'):
            configure()

    def test_banco_produtivo(self):
        with patch.dict(os.environ, {'ED02_V575_READONLY_AUTHORIZED':'true','GITHUB_REF':'refs/heads/ed02-assinaturas-vigencia-ledger','GITHUB_EVENT_NAME':'push','ED02_V575_COMMIT_MESSAGE':'ED02_RUN_ORIGINAL_HTTP_PG_ED025_V575','ED02_HOMOLOG_DATABASE_URL':'postgresql://a:b@db.example.com/production'}, clear=True), self.assertRaisesRegex(RuntimeError, 'URL'):
            configure()

if __name__ == '__main__':
    unittest.main()
