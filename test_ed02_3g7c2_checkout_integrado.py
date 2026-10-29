"""ED02.3G.7C.2 - testes isolados de checkout e ledger.

Nao cria pagamentos, nao acessa redes, nao conecta no PostgreSQL e nao envia email.
Requer SQLAlchemy; usa o main.py atual no GitHub por extracao AST da rota.
"""
import ast
import importlib.util
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main, mock

ROOT = Path(__file__).resolve().parent
MAIN = ROOT / 'main.py'
LEDGER = ROOT / 'ed02_checkout_ledger.py'


def load_ledger():
    spec = importlib.util.spec_from_file_location('ed02_checkout_ledger', LEDGER)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def load_checkout():
    tree = ast.parse(MAIN.read_text(encoding='utf-8'))
    candidates = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == 'checkout']
    if len(candidates) != 1:
        raise AssertionError('Era esperada exatamente uma rota checkout()')
    func = candidates[0]
    func.decorator_list = []
    env = {}
    exec(compile(ast.Module(body=[func], type_ignores=[]), str(MAIN), 'exec'), env)
    return env['checkout'], env


class LedgerUnitTests(TestCase):
    def setUp(self):
        self.mod = load_ledger()
        self.db = mock.Mock()
        self.db.engine.dialect.name = 'postgresql'
        self.db.session.execute.return_value.scalar.return_value = 'achetece_ed01_test'
        self.ref = 'achetece:1:' + 'a' * 32
        self.flag = mock.patch.dict(os.environ, {'ED01_HOMOLOGACAO':'true'})
        self.flag.start()
        self.addCleanup(self.flag.stop)

    def test_insert_usa_parametros_e_commit(self):
        self.mod.registrar_contratacao(self.db, 1, self.ref, 'mensal', '2.00')
        self.assertEqual(self.db.session.commit.call_count, 1)
        call = self.db.session.execute.call_args_list[-1]
        self.assertIn('INSERT INTO public.contratacao_checkout', str(call.args[0]))
        self.assertEqual(call.args[1]['empresa'], 1)
        self.assertEqual(call.args[1]['ref'], self.ref)
        self.assertEqual(str(call.args[1]['valor']), '2.00')

    def test_fora_de_homologacao_bloqueia_antes_de_insert(self):
        with mock.patch.dict(os.environ, {'ED01_HOMOLOGACAO':'false'}):
            with self.assertRaises(RuntimeError):
                self.mod.registrar_contratacao(self.db, 1, self.ref, 'mensal', '2.00')
        self.assertFalse(any('INSERT' in str(c.args[0]) for c in self.db.session.execute.call_args_list))

    def test_banco_errado_bloqueia(self):
        self.db.session.execute.return_value.scalar.return_value = 'achetece_db'
        with self.assertRaises(RuntimeError):
            self.mod.registrar_contratacao(self.db, 1, self.ref, 'mensal', '2.00')
        self.db.session.commit.assert_not_called()

    def test_sqlite_bloqueia(self):
        self.db.engine.dialect.name = 'sqlite'
        with self.assertRaises(RuntimeError):
            self.mod.registrar_contratacao(self.db, 1, self.ref, 'mensal', '2.00')

    def test_referencia_de_outra_empresa_bloqueia(self):
        with self.assertRaises(ValueError):
            self.mod.registrar_contratacao(self.db, 2, self.ref, 'mensal', '2.00')

    def test_valor_ou_plano_invalido_bloqueia(self):
        for plano, valor in [('semestral','2.00'), ('anual','2.001'),('mensal','0.00')]:
            with self.subTest(plano=plano, valor=valor):
                with self.assertRaises(ValueError):
                    self.mod.registrar_contratacao(self.db,1,self.ref,plano,valor)

    def test_falha_insert_faz_rollback(self):
        def execute(stmt, *args, **kw):
            if 'INSERT' in str(stmt):
                raise RuntimeError('erro de gravacao simulado')
            return mock.Mock(scalar=lambda: 'achetece_ed01_test')
        self.db.session.execute.side_effect = execute
        with self.assertRaises(RuntimeError):
            self.mod.registrar_contratacao(self.db,1,self.ref,'mensal','2.00')
        self.db.session.rollback.assert_called_once()
        self.db.session.commit.assert_not_called()

    def test_preference_id_atualiza_somente_estado(self):
        self.db.session.execute.return_value.scalar_one_or_none.return_value = 55
        self.mod.registrar_resultado_preferencia(self.db,self.ref,'pref_abc')
        call = self.db.session.execute.call_args_list[-1]
        sql = str(call.args[0])
        self.assertIn("status='criada'",sql)
        self.assertNotIn('valor_esperado =',sql)
        self.assertNotIn('plano =',sql)
        self.assertEqual(call.args[1]['pref'],'pref_abc')
        self.db.session.commit.assert_called_once()

    def test_preferencia_duplicada_ou_ausente_nao_confirma(self):
        self.db.session.execute.return_value.scalar_one_or_none.return_value = None
        with self.assertRaises(RuntimeError):
            self.mod.registrar_resultado_preferencia(self.db,self.ref,'pref_abc')
        self.db.session.rollback.assert_called_once()
        self.db.session.commit.assert_not_called()


class CheckoutRouteTests(TestCase):
    def setUp(self):
        self.fn, self.env = load_checkout()
        self.ledger = mock.Mock()
        self.ledger.registrar_contratacao = mock.Mock()
        self.ledger.registrar_resultado_preferencia = mock.Mock()
        self.empresa = SimpleNamespace(id=1,email='teste@example.invalid')
        self.events = []
        self.ledger.registrar_contratacao.side_effect = lambda *a:self.events.append('insert')
        self.ledger.registrar_resultado_preferencia.side_effect = lambda *a:self.events.append('update')
        self.pref = mock.Mock()
        self.pref.create.side_effect = lambda data:(self.events.append('mp'), {'response': {'id':'pref_123', 'init_point':'https://example.invalid/test'}})[1]
        self.sdk = mock.Mock()
        self.sdk.preference.return_value = self.pref
        self.url_id = mock.Mock(hex='a'*32)
        import sys
        self.module_patch = mock.patch.dict(sys.modules, {'ed02_checkout_ledger':self.ledger})
        self.module_patch.start(); self.addCleanup(self.module_patch.stop)
        self.env.update({
            'session':{'empresa_id':1},
            'Empresa':SimpleNamespace(query=SimpleNamespace(get=lambda k:self.empresa)),
            'request':SimpleNamespace(args={'plano':'anual'}),
            '_public_base_url':lambda:'https://example.invalid',
            'PLAN_MONTHLY':2.0,'PLAN_YEARLY':2.0,
            'uuid':SimpleNamespace(uuid4=lambda:self.url_id),
            '_env_bool':lambda key, default: True,
            'db':mock.Mock(),
            'mercadopago':SimpleNamespace(SDK=lambda token:self.sdk),
            'os':SimpleNamespace(environ={}),
            'app':SimpleNamespace(logger=mock.Mock()),
            'redirect':lambda url:('REDIRECT',url),
            'url_for':lambda name:name,
        })

    def test_checkout_persiste_antes_de_chamar_mp_e_atualiza_depois(self):
        outcome = self.fn()
        self.assertEqual(self.events,['insert','mp','update'])
        self.assertEqual(outcome,('REDIRECT','https://example.invalid/test'))
        args = self.ledger.registrar_contratacao.call_args.args
        self.assertEqual(args[1:],(1,'achetece:1:'+'a'*32,'anual',2.0))
        payload = self.pref.create.call_args.args[0]
        self.assertEqual(payload['external_reference'],args[2])
        self.assertEqual(payload['items'][0]['unit_price'], args[4])
        self.ledger.registrar_resultado_preferencia.assert_called_once_with(self.env['db'],args[2],'pref_123')

    def test_flag_desligada_nao_utiliza_ledger(self):
        self.env['_env_bool'] = lambda *a:False
        self.assertEqual(self.fn()[0],'REDIRECT')
        self.assertEqual(self.events,['mp'])

    def test_falha_insert_bloqueia_mp(self):
        self.ledger.registrar_contratacao.side_effect = RuntimeError('insert falhou')
        result=self.fn()
        self.assertEqual(result[1],503)
        self.pref.create.assert_not_called()

    def test_falha_mp_marca_preferencia_falha_sem_redirecionar(self):
        self.pref.create.side_effect = RuntimeError('falha externa simulada')
        self.assertEqual(self.fn()[1],500)
        self.ledger.registrar_resultado_preferencia.assert_called_once_with(self.env['db'],'achetece:1:'+'a'*32,None)

    def test_preferencia_sem_id_bloqueia_redirecionamento(self):
        self.pref.create.side_effect = lambda payload:{'response':{'init_point':'https://example.invalid/test'}}
        self.assertEqual(self.fn()[1],503)
        self.ledger.registrar_resultado_preferencia.assert_called_once_with(self.env['db'],'achetece:1:'+'a'*32,None)

    def test_falha_atualizar_preferencia_bloqueia_redirecionamento(self):
        self.ledger.registrar_resultado_preferencia.side_effect=RuntimeError('falha simulado')
        self.assertEqual(self.fn()[1],503)

    def test_sem_sessao_nao_faz_chamada(self):
        self.env['session']={}
        self.assertEqual(self.fn(),('REDIRECT','login'))
        self.pref.create.assert_not_called()
        self.ledger.registrar_contratacao.assert_not_called()

if __name__ == '__main__':
    main(verbosity=2)
