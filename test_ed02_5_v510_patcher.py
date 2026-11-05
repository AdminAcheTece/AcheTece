"""Contrato do patch V5.10: aceita apenas main anterior ou main ja integrado."""
from pathlib import Path
import unittest
from aplicar_ed02_v510_main import construir, _verify_tree

class PatchV510(unittest.TestCase):
    def setUp(self):
        self.main=Path(__file__).parent/'main.py'
        self.source=self.main.read_text(encoding='utf-8')
        self.ja_integrado='ED02_V510_REPLAY_HOMOLOG' in self.source

    def test_gera_ou_reconhece_main_completo_compilavel(self):
        candidate=self.source if self.ja_integrado else construir(self.source)
        compile(candidate,'main.py','exec')
        self.assertIn('ED02_V510_REPLAY_HOMOLOG',candidate)
        self.assertIn('ED02_V510_HTTP_SHADOW_HOMOLOG',candidate)
    def test_patcher_bloqueia_duplicacao(self):
        candidato=self.source if self.ja_integrado else construir(self.source)
        with self.assertRaises(RuntimeError): construir(candidato)
    def test_nao_modifica_main_em_memoria(self):
        before=self.main.read_bytes()
        if self.ja_integrado: _verify_tree(self.source)
        else: construir(self.source)
        self.assertEqual(before,self.main.read_bytes())
    def test_sem_ancora_falha_fechado(self):
        if self.ja_integrado:
            # A segunda aplicação deve falhar mesmo que os anchors ainda existam.
            with self.assertRaises(RuntimeError): construir(self.source)
        else:
            with self.assertRaises(RuntimeError): construir(self.source.replace(
                '# ED02.3E: exclusivamente homologacao','## removido'))
if __name__=='__main__':unittest.main()
