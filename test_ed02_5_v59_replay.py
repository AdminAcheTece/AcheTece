"""Regression tests against REAL ED02 exact-once function, fake PostgreSQL driver.

A passing test does not fix legacy replay. It proves only ED02 gated path
behaviour with controlled SQL results and two distinct payment identities.
"""
import os
import unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
from ed02_payment_idempotency import process_verified_payment_exact_once
from ed02_5_v59_regressao import diagnosticar_replay_legado


class Result:
    def __init__(self, row=None, scalar=None):
        self.row, self.scalar = row, scalar
    def scalar_one(self): return self.scalar
    def mappings(self): return self
    def first(self): return self.row


class MemoryPostgresSession:
    def __init__(self, database='achetece_ed01_test'):
        self.database = database
        self.company = {1: {'id':1,'status_pagamento':'pendente','data_version':0}}
        self.events = {}
        self.commit_count = 0
        self.rollback_count = 0
        self.commands = []
    def execute(self, statement, params=None):
        sql = ' '.join(str(statement).lower().split())
        par = dict(params or {})
        self.commands.append(sql)
        if 'select current_database()' in sql:
            return Result(scalar=self.database)
        if 'from public.empresa' in sql and 'for update' in sql:
            e = self.company.get(par['empresa_id'])
            return Result(row={'id': e['id'], 'status':e['status_pagamento']} if e else None)
        if 'from public.pagamento_evento' in sql and 'for update' in sql:
            e = self.events.get(par['payment_id'])
            return Result(row=dict(e) if e else None)
        if 'insert into public.pagamento_evento' in sql:
            pid = par['payment_id']
            self.events[pid] = {'id':len(self.events)+1, 'empresa_id':par['empresa_id'],
                                'external_reference':par['external_reference'],
                                'status_provedor':par['status_provedor'],
                                'estado_processamento':'recebido'}
            return Result()
        if 'update public.pagamento_evento' in sql and 'set contador_recebimentos' in sql:
            self.events[par['payment_id']]['status_provedor'] = par['status_provedor']
            return Result()
        if 'update public.empresa' in sql and "set status_pagamento='ativo'" in sql:
            e = self.company[par['empresa_id']]
            e['status_pagamento'] = 'ativo'
            e['data_version'] += 1
            return Result()
        if 'update public.pagamento_evento' in sql and "set estado_processamento='aplicado_ed02'" in sql:
            self.events[par['payment_id']]['estado_processamento'] = 'aplicado_ed02'
            return Result()
        raise AssertionError('SQL inesperado no teste: ' + sql[:110])
    def commit(self): self.commit_count += 1
    def rollback(self): self.rollback_count += 1


def payment(pid='1001', status='approved', ref='achetece:1:contrato-a'):
    return {'id':pid,'external_reference':ref, 'status':status,
            'currency_id':'BRL','transaction_amount':'2.00'}


class ReplayV59(unittest.TestCase):
    def setUp(self):
        self.s = MemoryPostgresSession()
        self.db = SimpleNamespace(session=self.s)
        self.patch = patch.dict(os.environ, {'ED02_FINANCIAL_GUARD_HOMOLOG':'false'})
        self.patch.start()
        self.addCleanup(self.patch.stop)
    def apply(self, p): return process_verified_payment_exact_once(self.db,p,1)
    def test_primeiro_pagamento_aplica_uma_vez(self):
        r = self.apply(payment())
        self.assertTrue(r['aplicado'])
        self.assertTrue(r['primeira_ativacao'])
        self.assertEqual(self.s.company[1]['data_version'],1)
    def test_replay_mesmo_payment_id_nao_prorroga_vigencia(self):
        self.apply(payment())
        r = self.apply(payment())
        self.assertTrue(r['duplicado'])
        self.assertFalse(r['aplicado'])
        self.assertEqual(self.s.company[1]['data_version'],1)
    def test_novo_payment_id_renova(self):
        self.apply(payment())
        r = self.apply(payment(pid='1002',ref='achetece:1:contrato-b'))
        self.assertTrue(r['aplicado'])
        self.assertFalse(r['primeira_ativacao'])
        self.assertEqual(self.s.company[1]['data_version'],2)
    def test_pendente_nao_ativa(self):
        r=self.apply(payment(status='pending'))
        self.assertFalse(r['aplicado'])
        self.assertEqual(self.s.company[1]['data_version'],0)
    def test_pendente_e_depois_aprovado_aplica_uma_vez(self):
        self.apply(payment(status='pending'))
        self.apply(payment(status='approved'))
        self.apply(payment(status='approved'))
        self.assertEqual(self.s.company[1]['data_version'],1)
    def test_replay_ref_divergente_bloqueia(self):
        self.apply(payment())
        with self.assertRaises(ValueError):
            self.apply(payment(ref='achetece:1:contrato-trocado'))
        self.assertEqual(self.s.company[1]['data_version'],1)
        self.assertEqual(self.s.rollback_count,1)
    def test_empresa_da_referencia_divergente_bloqueia(self):
        with self.assertRaises(ValueError):
            self.apply(payment(ref='achetece:2:contrato-a'))
    def test_banco_diferente_bloqueia_sem_dml(self):
        self.s.database='prod'
        with self.assertRaises(RuntimeError): self.apply(payment())
        self.assertFalse(any('update public' in q or 'insert into' in q for q in self.s.commands))
    def test_estorno_nao_produz_ativacao(self):
        self.apply(payment(status='refunded'))
        self.assertEqual(self.s.company[1]['data_version'],0)
    def test_replay_estornado_nao_atualiza_data_mas_pede_politica_reversao(self):
        self.apply(payment())
        r=self.apply(payment(status='refunded'))
        self.assertFalse(r['aplicado'])
        self.assertEqual(self.s.company[1]['data_version'],1)
        # Um estorno ainda NAO desativa a empresa nesse caminho; gate bloqueado.
        self.assertEqual(self.s.company[1]['status_pagamento'],'ativo')
    def test_auditoria_legado_reconhece_risco_sem_conceder_release(self):
        main=Path(__file__).resolve().parent/'main.py'
        a=diagnosticar_replay_legado(main)
        self.assertTrue(a['funcao_encontrada'])
        self.assertFalse(a['replay_legado_provado_seguro'])
        self.assertFalse(a['liberacao_permitida'])


if __name__ == '__main__':unittest.main()
