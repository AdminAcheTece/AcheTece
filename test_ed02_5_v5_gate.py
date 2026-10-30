import os, unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from datetime import datetime, timezone
from ed02_5_visibility_gate import consultar_vigencia_homolog, VigenciaReadBlocked

class V5GateTests(unittest.TestCase):
    def test_ambiente_errado(self):
        db=MagicMock()
        with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'false'}):
            with self.assertRaises(VigenciaReadBlocked): consultar_vigencia_homolog(db,1)
        db.session.execute.assert_not_called()
    def test_dialeto_errado(self):
        db=MagicMock();db.engine.dialect.name='sqlite'
        with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'true'}):
            with self.assertRaises(VigenciaReadBlocked): consultar_vigencia_homolog(db,1)
    def test_banco_errado(self):
        db=MagicMock();db.engine.dialect.name='postgresql'
        db.session.execute.return_value.scalar_one.return_value='postgres'
        with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'true'}):
            with self.assertRaises(VigenciaReadBlocked): consultar_vigencia_homolog(db,1)
    def test_ausencia_periodo_falha_fechada(self):
        db=MagicMock();db.engine.dialect.name='postgresql'
        db.session.execute.side_effect=[MagicMock(scalar_one=lambda:'achetece_ed01_test'),MagicMock(mappings=lambda:MagicMock(one_or_none=lambda:None))]
        with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'true'}):
            self.assertEqual(consultar_vigencia_homolog(db,1,datetime.now(timezone.utc))['vigente'],False)
    def test_sem_fallback_se_tabela_ausente(self):
        db=MagicMock();db.engine.dialect.name='postgresql'
        db.session.execute.side_effect=[MagicMock(scalar_one=lambda:'achetece_ed01_test'),RuntimeError('tabela inexistente')]
        with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'true'}):
            with self.assertRaises(RuntimeError): consultar_vigencia_homolog(db,1,datetime.now(timezone.utc))
    def test_main_preserva_flag_desligada(self):
        source=Path(__file__).with_name('main.py').read_text()
        self.assertIn('if _env_bool("ED02_VIGENCIA_WRITE_HOMOLOG", False):',source)
        self.assertIn('"ativou": False, "duplicado": periodo["duplicado"]',source)
        self.assertIn('registrar_periodo_homolog(',source)
        self.assertIn('if _env_bool("ED02_EXACT_ONCE_HOMOLOG", False):',source)
        self.assertIn('func.lower(func.coalesce(cls.status_pagamento, "")) == "ativo"',source)

if __name__ == '__main__': unittest.main()
