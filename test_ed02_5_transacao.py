import os,unittest
from unittest.mock import MagicMock,patch
from datetime import datetime,timezone
from ed02_5_transacao import registrar_periodo_homolog,ED025Blocked

class TransacaoSegura(unittest.TestCase):
 def test_bloqueio_ambiente_sem_escrita(self):
  db=MagicMock()
  with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'false'}):
   with self.assertRaises(ED025Blocked):
    registrar_periodo_homolog(db,{},1,datetime.now(timezone.utc))
  db.session.execute.assert_not_called()
  db.session.rollback.assert_called_once()
 def test_dialeto_sem_escrita(self):
  db=MagicMock(); db.engine.dialect.name='sqlite'
  with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'true'}):
   with self.assertRaises(ED025Blocked):
    registrar_periodo_homolog(db,{},1,datetime.now(timezone.utc))
  db.session.execute.assert_not_called()
 def test_data_naive_bloqueada_antes_de_inserir(self):
  db=MagicMock(); db.engine.dialect.name='postgresql'
  db.session.execute.return_value.scalar_one.return_value='achetece_ed01_test'
  with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'true'}):
   with self.assertRaises(ED025Blocked):
    registrar_periodo_homolog(db,{},1,datetime(2026,10,29))
  self.assertEqual(db.session.execute.call_count,1)
 def test_database_errado_bloqueado(self):
  db=MagicMock(); db.engine.dialect.name='postgresql'
  db.session.execute.return_value.scalar_one.return_value='some_other_database'
  with patch.dict(os.environ,{'ED01_HOMOLOGACAO':'true'}):
   with self.assertRaises(ED025Blocked):
    registrar_periodo_homolog(db,{},1,datetime.now(timezone.utc))
  self.assertEqual(db.session.execute.call_count,1)
if __name__=='__main__':unittest.main()
