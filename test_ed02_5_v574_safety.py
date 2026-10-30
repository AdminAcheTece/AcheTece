import unittest
from ed02_5_v574_http_pg_readonly import validate_url, HOST, DB
class V574Safety(unittest.TestCase):
    def test_nao_permite_producao(self):
        with self.assertRaises(RuntimeError):
            validate_url('postgresql://x:y@prod.example.com/producao')
    def test_nao_permite_outro_banco(self):
        with self.assertRaises(RuntimeError):
            validate_url(f'postgresql://x:y@{HOST}/outra_base')
    def test_nao_permite_url_sem_credenciais(self):
        with self.assertRaises(RuntimeError):
            validate_url(f'postgresql://{HOST}/{DB}')
    def test_url_canonica(self):
        url=f'postgresql://x:y@{HOST}/{DB}'
        self.assertEqual(validate_url(url),url)
if __name__=='__main__': unittest.main()
