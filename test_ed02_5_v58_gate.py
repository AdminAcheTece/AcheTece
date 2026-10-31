import unittest
from pathlib import Path

from ed02_5_v58_gate_ativacao import (
    ATIVACAO_AUTOMATICA_PERMITIDA, PREREQUISITOS,
    ler_gate_arquivo, verificar_gate,
)

BASE = Path(__file__).resolve().parent


class GateV58Tests(unittest.TestCase):
    def test_manifesto_restringido_por_padrao(self):
        resultado = ler_gate_arquivo(BASE / "ED02_5_V58_GATE_ATIVACAO.json")
        self.assertFalse(resultado.prontidao_documental)
        self.assertEqual(len(resultado.pendencias), len(PREREQUISITOS))
        self.assertFalse(resultado.pode_ativar_producao)

    def test_documentacao_completa_tambem_nao_liga_producao(self):
        resultado = verificar_gate({chave: True for chave in PREREQUISITOS})
        self.assertTrue(resultado.prontidao_documental)
        self.assertFalse(resultado.pode_ativar_producao)
        self.assertFalse(ATIVACAO_AUTOMATICA_PERMITIDA)

    def test_ausencia_de_requisito_bloqueia(self):
        p = verificar_gate({PREREQUISITOS[0]: True})
        self.assertIn(PREREQUISITOS[-1], p.pendencias)

    def test_valor_nao_booleano_bloqueia(self):
        for valor in ("true", 1, None):
            with self.subTest(valor=valor), self.assertRaises(ValueError):
                verificar_gate({PREREQUISITOS[0]: valor})

    def test_chave_desconhecida_nao_ignorada(self):
        with self.assertRaises(ValueError):
            verificar_gate({"liberado": True})

    def test_v58_nao_faz_efeitos_externos(self):
        source = (BASE / "ed02_5_v58_consolidacao.py").read_text(encoding="utf-8")
        gate = (BASE / "ed02_5_v58_gate_ativacao.py").read_text(encoding="utf-8")
        for codigo in (source, gate):
            self.assertNotIn("import main", codigo)
            self.assertNotIn("db.session", codigo)
            self.assertNotIn("requests.post(", codigo)
            self.assertNotIn("subprocess", codigo)


if __name__ == "__main__":
    unittest.main()
