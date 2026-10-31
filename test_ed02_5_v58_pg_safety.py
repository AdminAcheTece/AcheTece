import unittest
from ed02_5_v58_pg_auditoria import GATILHO, HOST, BANCO, guarda_execucao, SQL_RESUMO


class V58ReadOnlySafety(unittest.TestCase):
    def base(self):
        return {
            'ED02_V58_READONLY_AUTORIZADA': 'true',
            'ED02_V58_COMMIT_MESSAGE': GATILHO,
            'ED02_HOMOLOG_DATABASE_URL': f'postgresql+psycopg://tester:placeholder@{HOST}:5432/{BANCO}?sslmode=require',
        }

    def test_sem_autorizacao(self):
        e = self.base();e.pop('ED02_V58_READONLY_AUTORIZADA')
        with self.assertRaises(RuntimeError): guarda_execucao(e)

    def test_gatilho_errado(self):
        e = self.base();e['ED02_V58_COMMIT_MESSAGE']='outra_mensagem'
        with self.assertRaises(RuntimeError): guarda_execucao(e)

    def test_host_producao_bloqueado(self):
        e = self.base();e['ED02_HOMOLOG_DATABASE_URL']=e['ED02_HOMOLOG_DATABASE_URL'].replace(HOST,'db-producao.exemplo.com')
        with self.assertRaises(RuntimeError): guarda_execucao(e)

    def test_somente_url_homologacao(self):
        self.assertEqual(guarda_execucao(self.base()), self.base()['ED02_HOMOLOG_DATABASE_URL'])

    def test_sql_estritamente_agregado(self):
        normalized=SQL_RESUMO.upper()
        self.assertIn('COUNT(', normalized)
        self.assertNotIn('UPDATE ', normalized)
        self.assertNotIn('INSERT ', normalized)
        self.assertNotIn('DELETE ', normalized)
        self.assertNotIn('ALTER ', normalized)


if __name__ == '__main__': unittest.main()
