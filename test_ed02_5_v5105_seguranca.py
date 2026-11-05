"""Testes de segurança offline V5.10.5. Nenhum acesso a banco/rede."""
from __future__ import annotations
import unittest
from pathlib import Path
from unittest.mock import patch

import test_ed02_5_v5105_pg_posmerge as gate


URL_OK = ('postgresql://usuario_teste:senha_falsa@'
          + gate.HOST + ':5432/' + gate.DATABASE)


def valid_env():
    return {
        'GITHUB_ACTIONS': 'true',
        'GITHUB_EVENT_NAME': 'push',
        'GITHUB_ACTOR': 'AdminAcheTece',
        'GITHUB_REF': gate.BRANCH,
        'ED02_V5105_READONLY_AUTHORIZED': 'true',
        'ED02_V5105_COMMIT_MESSAGE': gate.TRIGGER,
        'ED02_HOMOLOG_DATABASE_URL': URL_OK,
    }


class V5105GuardTests(unittest.TestCase):
    def test_contexto_autorizado_sem_abrir_conexao(self):
        self.assertEqual(gate.verify_execution_context(valid_env()), URL_OK)

    def test_bloqueia_branch_main(self):
        e = valid_env(); e['GITHUB_REF'] = 'refs/heads/main'
        with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_branch_candidata(self):
        e = valid_env(); e['GITHUB_REF'] = 'refs/heads/ed02-v5103-integracao-candidata'
        with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_actor_incorreto(self):
        e = valid_env(); e['GITHUB_ACTOR'] = 'outro'
        with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_gatilho_diferente(self):
        e = valid_env(); e['ED02_V5105_COMMIT_MESSAGE'] = 'outro'
        with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_sem_ativacao_explicita(self):
        e = valid_env(); e.pop('ED02_V5105_READONLY_AUTHORIZED')
        with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_workflow_manual(self):
        e = valid_env(); e['GITHUB_EVENT_NAME'] = 'workflow_dispatch'
        with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_flag_financeira_ligada(self):
        for flag in gate.FINANCIAL_FLAGS:
            e = valid_env(); e[flag] = 'true'
            with self.subTest(flag=flag):
                with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_banco_producao(self):
        for url in (
            'postgresql://usuario:senha@outra-maquina.oregon-postgres.render.com/achetece_ed01_test',
            'postgresql://usuario:senha@' + gate.HOST + '/achetece_prod',
            'sqlite:///achetece.db',
            'postgresql://usuario@' + gate.HOST + '/' + gate.DATABASE,
            'postgresql://usuario:senha@' + gate.HOST + ':5433/' + gate.DATABASE,
            'postgresql://usuario:senha@' + gate.HOST + '/' + gate.DATABASE + '#fragmento',
        ):
            with self.subTest(url=url[:16]):
                with self.assertRaises(gate.GateError): gate.validate_database_url(url)

    def test_bloqueia_url_ausente(self):
        e = valid_env(); e['ED02_HOMOLOG_DATABASE_URL'] = ''
        with self.assertRaises(gate.GateError): gate.verify_execution_context(e)

    def test_bloqueia_modo_outro(self):
        with patch.object(gate, 'verify_execution_context', side_effect=AssertionError('nao chamar')):
            with self.assertRaises(gate.GateError): gate.configure('write')

    def test_verifica_hashes_reais_sem_conectar(self):
        gate.verify_files(Path(__file__).resolve().parent)

    def test_quatro_rotas_conhecidas(self):
        self.assertEqual(len(gate.ROUTES), 8)
        self.assertEqual(set(gate.ALVOS),
                         {'index','exportar','utils_empresas_json','empresa_perfil'})

    def test_codigo_nao_chama_processador_financeiro(self):
        source = (Path(__file__).with_name('test_ed02_5_v5105_pg_posmerge.py')
                  .read_text('utf-8'))
        self.assertNotIn('_processar_pagamento(', source)
        self.assertNotIn('client.post(', source)
        self.assertIn("'MAIL_SUPPRESS_SEND': 'true'", source)
        self.assertIn('default_transaction_read_only=on', source)


if __name__ == '__main__':
    unittest.main()
