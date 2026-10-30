"""Verificacoes offline do guard de commit real V5.5.1."""
import ast
import os
import unittest
from pathlib import Path
from unittest.mock import patch

SRC=Path(__file__).with_name('test_ed02_5_v551_commit_concorrente.py')
class V551Safety(unittest.TestCase):
    def test_codigo_compila(self):
        compile(SRC.read_text(encoding='utf-8'),str(SRC),'exec')
    def test_exige_gatilho_antes_da_conexao(self):
        tree=ast.parse(SRC.read_text(encoding='utf-8'))
        fn=next(n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name=='main')
        assert 'ED02_V551_COMMIT_AUTHORIZED' in ast.unparse(fn.body[0])
    def test_limpeza_limitada_por_identificadores(self):
        s=SRC.read_text(encoding='utf-8')
        assert 'AND preference_id=:pref' in s
        assert 'AND external_reference=:ref' in s
        assert 'DROP TABLE' not in s and 'TRUNCATE' not in s
    def test_duas_conexoes_e_verificacao_final(self):
        s=SRC.read_text(encoding='utf-8')
        assert 'ThreadPoolExecutor(max_workers=2)' in s
        assert 'checks != (0,0,0)' in s
        assert 'sorted(outcomes) != [False,True]' in s
if __name__=='__main__': unittest.main()
