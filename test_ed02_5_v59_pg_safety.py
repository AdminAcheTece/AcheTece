import unittest
from ed02_5_v59_pg_shadow_readonly import guarda_execucao, SQL_EMPRESAS, SQL_PERIODOS, BANCO, HOST

VALID = dict(ED02_V59_READONLY_AUTORIZADA='true',
 ED02_V59_COMMIT_MESSAGE='ED02_RUN_SHADOW_ED025_V59_READONLY',
 ED02_HOMOLOG_DATABASE_URL=f'postgresql://test:secret@{HOST}:5432/{BANCO}')


class ShadowPgSafetyV59(unittest.TestCase):
    def test_sem_gatilho_rejeita(self):
        with self.assertRaises(RuntimeError):guarda_execucao({})
    def test_url_canonica_permitida(self):
        self.assertEqual(guarda_execucao(VALID), VALID['ED02_HOMOLOG_DATABASE_URL'])
    def test_banco_producao_rejeitado(self):
        a=dict(VALID,ED02_HOMOLOG_DATABASE_URL='postgresql://test:secret@prod.example.com/prod')
        with self.assertRaises(RuntimeError):guarda_execucao(a)
    def test_banco_errado_no_host_certo_rejeitado(self):
        a=dict(VALID,ED02_HOMOLOG_DATABASE_URL=f'postgresql://test:secret@{HOST}/outro')
        with self.assertRaises(RuntimeError):guarda_execucao(a)
    def test_gatilho_inesperado_rejeita(self):
        a=dict(VALID, ED02_V59_COMMIT_MESSAGE='outro')
        with self.assertRaises(RuntimeError):guarda_execucao(a)
    def test_query_somente_leitura(self):
        for sql in (SQL_EMPRESAS,SQL_PERIODOS):
            self.assertEqual(sql.strip().split()[0].upper(),'SELECT')
            self.assertNotIn(';',sql)
            self.assertFalse(any(k in sql.upper() for k in ('INSERT INTO','UPDATE ','DELETE FROM','DROP ','ALTER ','TRUNCATE ')))


if __name__=='__main__': unittest.main()
