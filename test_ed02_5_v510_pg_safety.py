import unittest
from test_ed02_5_v510_pg_rollback import guarda, GATILHO, HOST, BANCO
class V510PGSafety(unittest.TestCase):
    def valid(self):
        return {"ED02_V510_PG_RUN_AUTHORIZED":"true", "ED02_V510_COMMIT_MESSAGE":GATILHO,
         "ED01_HOMOLOGACAO":"true", "ED02_V510_REPLAY_HOMOLOG":"true",
         "ED02_HOMOLOG_DATABASE_URL":f"postgresql+psycopg://v510_test:senha-ficticia@{HOST}:5432/{BANCO}"}
    def test_canonica(self):self.assertIn(BANCO,guarda(self.valid()))
    def test_gatilho_errado(self):
        e=self.valid();e["ED02_V510_COMMIT_MESSAGE"]="errado"
        with self.assertRaises(RuntimeError):guarda(e)
    def test_producao_bloqueada(self):
        e=self.valid();e["ED02_HOMOLOG_DATABASE_URL"]="postgresql://u:p@prod.render.com/achetece_db"
        with self.assertRaises(RuntimeError):guarda(e)
    def test_flag_ausente(self):
        e=self.valid();e.pop("ED02_V510_PG_RUN_AUTHORIZED")
        with self.assertRaises(RuntimeError):guarda(e)
    def test_credenciais_ausentes(self):
        e=self.valid();e["ED02_HOMOLOG_DATABASE_URL"]=f"postgresql://{HOST}/{BANCO}"
        with self.assertRaises(RuntimeError):guarda(e)
if __name__=='__main__':unittest.main()
