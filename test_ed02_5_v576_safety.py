"""ED02.5 V5.7.6 R3 — testes isolados de segurança do ensaio."""
import inspect
import os
import unittest
from unittest.mock import patch
import test_ed02_5_v576_elegibilidade_rollback as v


class SafetyV576R3(unittest.TestCase):
    def test_sem_gatilho_bloqueia(self):
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'BLOQUEADO'):
                v.configure()

    def test_producao_bloqueada(self):
        env = dict(ED02_V576_AUTHORIZED='true', GITHUB_EVENT_NAME='push',
                   GITHUB_REF='refs/heads/main',
                   ED02_V576_COMMIT_MESSAGE=v.TRIGGER,
                   GITHUB_ACTOR='AdminAcheTece')
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'BLOQUEADO'):
                v.configure()

    def test_sem_credenciais_bloqueia(self):
        env = dict(ED02_V576_AUTHORIZED='true', GITHUB_EVENT_NAME='push',
                   GITHUB_REF='refs/heads/ed02-assinaturas-vigencia-ledger',
                   ED02_V576_COMMIT_MESSAGE=v.TRIGGER,
                   GITHUB_ACTOR='AdminAcheTece',
                   ED02_HOMOLOG_DATABASE_URL='postgresql://a@db.example.com/test')
        with patch.dict(os.environ, env, clear=True):
            with self.assertRaisesRegex(RuntimeError, 'BLOQUEADO'):
                v.configure()

    def test_ids_positivos_para_perfil_flask(self):
        source = inspect.getsource(v.run)
        self.assertIn('1_500_000_000', source)
        self.assertIn('company_ids', source)
        self.assertIn("f'/empresa/{company_ids[0]}'", source)

    def test_get_bind_do_flask_sqlalchemy_fixado_na_conexao(self):
        source = inspect.getsource(v.run)
        self.assertIn('bind_on_test_connection', source)
        self.assertIn("patch.object(session_cls, 'get_bind'", source)
        self.assertIn('m.db.session().get_bind() is conn', source)

    def test_prova_sql_de_vigencia_antes_de_http(self):
        source = inspect.getsource(v.run)
        self.assertIn('visible == 1', source)
        self.assertIn('visible_tears == 1', source)

    def test_quatro_rotas_originais(self):
        source = inspect.getsource(v.run)
        for route in ("get('/')", "'/exportar'", "'/utils/empresas_json?escopo=pagantes'",
                      "f'/empresa/{company_ids[0]}'"):
            self.assertIn(route, source)

    def test_rollback_em_finally_e_auditoria_em_falha(self):
        source = inspect.getsource(v.run)
        self.assertIn('finally:', source)
        self.assertIn('outer_tx.rollback()', source)
        self.assertIn('after == baseline', source)
        self.assertIn('baseline_teares', source)
        self.assertIn('zero residuos', source)

    def test_sem_commit_e_ids_explicitos_de_tear(self):
        source = inspect.getsource(v.run)
        self.assertNotIn('.commit(', source)
        self.assertIn('id=tear_ids[i]', source)

    def test_fixture_preenche_apelido_do_perfil_publico(self):
        source = inspect.getsource(v.run)
        self.assertIn('id=company_ids[i], nome=name, apelido=name', source)
        self.assertIn('perfil_ativo = get(', source)
        self.assertIn("re.escape(active)", source)
        self.assertIn('titulo do perfil nao corresponde ao apelido', source)

    def test_gatilho_r3_distinto_do_r2(self):
        self.assertEqual(v.TRIGGER, 'ED02_RUN_ELIGIBILITY_ED025_V576_R3')
        self.assertIn('R3_FIXTURE_', v.PREFIX)
        self.assertNotIn('.commit(', inspect.getsource(v.run))

    def test_rota_nao_pode_escrever(self):
        source = inspect.getsource(v.run)
        self.assertIn('readonly_http', source)
        self.assertIn('BLOQUEADO: escrita SQL durante HTTP', source)
        self.assertIn('consulta a empresa/tear fora da transacao', source)


if __name__ == '__main__':
    unittest.main()
