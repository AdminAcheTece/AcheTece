"""Garantias locais do gatilho PostgreSQL V5101, sem acessar rede."""
import os
import unittest
from unittest.mock import patch
from test_ed02_5_v5101_pg_http import guard,TRIGGER,BRANCH

class GuardV5101(unittest.TestCase):
    def setUp(self):
        self.env={'GITHUB_ACTIONS':'true','GITHUB_EVENT_NAME':'push',
                  'GITHUB_ACTOR':'AdminAcheTece','GITHUB_REF':BRANCH,
                  'ED02_V5101_PG_AUTHORIZED':'true',
                  'ED02_V5101_COMMIT_MESSAGE':TRIGGER,
                  'ED02_HOMOLOG_DATABASE_URL':'postgresql://u:p@dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com:5432/achetece_ed01_test'}
    def call(self,**changes):
        d={**self.env,**changes}
        with patch.dict(os.environ,d,clear=True):return guard()
    def test_url_canonica(self):self.assertEqual(self.call(),self.env['ED02_HOMOLOG_DATABASE_URL'])
    def test_banco_producao_bloqueado(self):
        with self.assertRaises(RuntimeError):self.call(ED02_HOMOLOG_DATABASE_URL=self.env['ED02_HOMOLOG_DATABASE_URL'].replace('achetece_ed01_test','achetece'))
    def test_host_interno_bloqueado(self):
        with self.assertRaises(RuntimeError):self.call(ED02_HOMOLOG_DATABASE_URL=self.env['ED02_HOMOLOG_DATABASE_URL'].replace('.oregon-postgres.render.com',''))
    def test_sem_gatilho_bloqueado(self):
        with self.assertRaises(RuntimeError):self.call(ED02_V5101_COMMIT_MESSAGE='comum')
    def test_outra_branch_bloqueada(self):
        with self.assertRaises(RuntimeError):self.call(GITHUB_REF='refs/heads/main')
    def test_outro_actor_bloqueado(self):
        with self.assertRaises(RuntimeError):self.call(GITHUB_ACTOR='outro')
    def test_sem_credenciais_bloqueado(self):
        with self.assertRaises(RuntimeError):self.call(ED02_HOMOLOG_DATABASE_URL=self.env['ED02_HOMOLOG_DATABASE_URL'].replace('u:p@',''))
    def test_acao_nao_ativada_bloqueada(self):
        with self.assertRaises(RuntimeError):self.call(ED02_V5101_PG_AUTHORIZED='false')

if __name__=='__main__':unittest.main()
