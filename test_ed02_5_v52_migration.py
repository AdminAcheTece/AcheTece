"""Testes isolados da politica de migracao V5.2, sem conexao externa."""
import os
import unittest
from unittest.mock import patch
import ed02_5_v52_migration as m

class V52MigrationSafetyTests(unittest.TestCase):
    def test_url_homolog_autorizada(self):
        m.validate_url('postgresql+psycopg://tester:senha@'+m.HOST+'/'+m.DB)
    def test_host_diferente_bloqueado(self):
        with self.assertRaises(RuntimeError):
            m.validate_url('postgresql+psycopg://user:pass@outro.example.com/'+m.DB)
    def test_banco_diferente_bloqueado(self):
        with self.assertRaises(RuntimeError):
            m.validate_url('postgresql+psycopg://user:pass@'+m.HOST+'/production')
    def test_migracao_sem_gatilho_bloqueada(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'autorizacao'):
                m.main()
    def test_ddl_sem_operacoes_destrutivas(self):
        sql = (m.DDL+' '+m.INDEX).upper()
        for forbidden in ('DROP ', 'DELETE ', 'TRUNCATE ', 'UPDATE ', 'ALTER '):
            self.assertNotIn(forbidden, sql)

if __name__ == '__main__':
    unittest.main()
