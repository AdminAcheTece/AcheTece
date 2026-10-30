"""ED02.5 V5.4 - Flask -> contrato -> ledger/vigencia no PostgreSQL real.

SOMENTE CI autorizado. Nao acessa a API Mercado Pago, nao envia e-mail,
nao ativa empresa e nunca confirma a transacao do ensaio.
Exige PostgreSQL externo de homologacao; toda escrita ficticia e revertida.
"""
import ast
import os
import secrets
import uuid
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import urlparse
from unittest.mock import MagicMock

from sqlalchemy import create_engine, text

DB_NAME = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'


def check_url(url):
    p = urlparse(url)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg', 'postgresql+psycopg2')
            and p.hostname == HOST and p.path == '/' + DB_NAME
            and p.username and p.password and p.port in (None, 5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: destino fora da homologacao')


class NoCommitSession:
    """A transacao externa e a unica autoridade para rollback."""
    def __init__(self, conn):
        self.conn = conn
    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)
    def commit(self):
        if not self.conn.in_transaction():
            raise RuntimeError('BLOQUEADO: transacao nao iniciada')
        # NO-OP, obrigatorio: nao persistir dados de ensaio.
    def rollback(self):
        self.conn.rollback()


class DBFacade:
    def __init__(self, engine, conn):
        self.engine = engine
        self.session = NoCommitSession(conn)


def load_flask_processor(db, fake_payments, company):
    tree = ast.parse(Path(__file__).with_name('main.py').read_text(encoding='utf-8'))
    fn = next(node for node in tree.body if isinstance(node, ast.FunctionDef)
              and node.name == '_processar_pagamento')
    scope = {
        '__name__': '_ed025_v54_test_',
        '_mp_get_payment': lambda pid: fake_payments[str(pid)],
        '_env_bool': lambda key, default=False: key in {
            'ED01_HOMOLOGACAO', 'ED02_FINANCIAL_GUARD_HOMOLOG',
            'ED02_CHECKOUT_LEDGER_HOMOLOG', 'ED02_EXACT_ONCE_HOMOLOG',
            'ED02_VIGENCIA_WRITE_HOMOLOG'},
        'db': db,
        'Empresa': SimpleNamespace(query=SimpleNamespace(get=lambda eid: company if int(eid) == 1 else None)),
        'app': SimpleNamespace(logger=MagicMock()),
        'datetime': datetime,
        '_send_email': MagicMock(side_effect=AssertionError('email proibido')),
        '_make_magic_link': MagicMock(side_effect=AssertionError('link proibido')),
    }
    exec(compile(ast.Module(body=[fn], type_ignores=[]), '<main.py>', 'exec'), scope)
    return scope['_processar_pagamento'], scope


def main():
    if os.environ.get('ED02_V54_HOMOLOG_AUTORIZADA') != 'true':
        raise RuntimeError('BLOQUEADO: gatilho autorizado ausente')
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    check_url(url)
    engine = create_engine(url, hide_parameters=True, pool_pre_ping=True,
                           connect_args={'sslmode': 'require'}, echo=False)
    original = os.environ.get('ED01_HOMOLOGACAO')
    os.environ['ED01_HOMOLOGACAO'] = 'true'
    ids = []
    refs = []
    tx = None
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                if conn.execute(text('SELECT current_database()')).scalar_one() != DB_NAME:
                    raise RuntimeError('BLOQUEADO: banco conectado nao autorizado')
                conn.exec_driver_sql("SET LOCAL lock_timeout = '3s'")
                conn.exec_driver_sql("SET LOCAL statement_timeout = '25s'")
                for table in ('public.empresa', 'public.contratacao_checkout',
                              'public.pagamento_evento', 'public.ed02_assinatura_periodo'):
                    if conn.execute(text('SELECT to_regclass(:table)'), {'table': table}).scalar_one() is None:
                        raise RuntimeError('BLOQUEADO: estrutura obrigatoria ausente')
                company_row = conn.execute(text('''SELECT id,status_pagamento,data_pagamento
                    FROM public.empresa WHERE id=1 FOR UPDATE''')).mappings().one_or_none()
                if (company_row is None or (company_row['status_pagamento'] or '').lower() != 'pendente'
                        or company_row['data_pagamento'] is not None):
                    raise RuntimeError('BLOQUEADO: empresa ficticia fora do estado esperado')
                snapshot = (company_row['status_pagamento'], company_row['data_pagamento'])
                # Confirma disputa de bloqueio entre duas conexoes reais.
                with engine.connect() as other:
                    try:
                        other.execute(text('SELECT id FROM public.empresa WHERE id=1 FOR UPDATE NOWAIT')).first()
                    except Exception as exc:
                        if getattr(getattr(exc, 'orig', None), 'sqlstate', None) != '55P03':
                            raise RuntimeError('bloqueio concorrente inesperado') from None
                        print('PASSOU: segunda conexao bloqueada (55P03).')
                    else:
                        raise AssertionError('segunda conexao obteve bloqueio indevido')
                    finally:
                        other.rollback()
                db = DBFacade(engine, conn)
                payments = {}
                for plano in ('mensal', 'anual'):
                    ref = f'achetece:1:{uuid.uuid4().hex}'
                    pid = str(920000000000000000 + secrets.randbelow(10000000000))
                    pref = f'ED025-V54-{uuid.uuid4().hex}'
                    refs.append(ref)
                    ids.append(pid)
                    conn.execute(text('''INSERT INTO public.contratacao_checkout
                      (empresa_id,external_reference,plano,valor_esperado,moeda,status,provedor,preference_id)
                      VALUES (1,:ref,:plano,2.00,'BRL','preferencia_criada','mercadopago',:pref)'''),
                      {'ref': ref, 'plano': plano, 'pref': pref})
                    payments[pid] = {'id': pid, 'status': 'approved', 'currency_id': 'BRL',
                                     'transaction_amount': 2.0, 'external_reference': ref}
                company = SimpleNamespace(id=1, status_pagamento=snapshot[0],
                                          data_pagamento=snapshot[1])
                process, scope = load_flask_processor(db, payments, company)
                first = process(ids[0]); replay = process(ids[0]); annual = process(ids[1])
                assert first['modo'] == 'ed02_5_vigencia_homologacao' and not first['duplicado']
                assert replay['duplicado'] and replay['ativou'] is False
                assert not annual['duplicado'] and annual['ativou'] is False
                assert scope['_send_email'].call_count == 0
                assert (company.status_pagamento, company.data_pagamento) == snapshot
                assert conn.execute(text('''SELECT count(*) FROM public.ed02_assinatura_periodo
                       WHERE payment_id = ANY(:ids)'''), {'ids': ids}).scalar_one() == 2
                assert conn.execute(text('''SELECT count(*) FROM public.pagamento_evento
                       WHERE payment_id = ANY(:ids)'''), {'ids': ids}).scalar_one() == 2
                periods = conn.execute(text('''SELECT plano,inicio,fim_exclusivo
                      FROM public.ed02_assinatura_periodo WHERE payment_id = ANY(:ids)
                      ORDER BY inicio'''), {'ids': ids}).all()
                assert periods[0][0] == 'mensal' and periods[1][0] == 'anual'
                assert periods[1][1] == periods[0][2], 'renovacao nao preservou vencimento'
                assert conn.execute(text('''SELECT status_pagamento,data_pagamento FROM public.empresa
                      WHERE id=1''')).one() == snapshot
                print('PASSOU: Flask validou contrato e registrou mensal/anual no banco real.')
                print('PASSOU: replay sem duplicidade; renovacao preservada.')
                print('PASSOU: sem ativacao de empresa e sem emails.')
            finally:
                if tx is not None and tx.is_active:
                    tx.rollback()
                    print('PASSOU: rollback externo obrigatorio realizado.')
                else:
                    raise RuntimeError('CRITICO: transacao externa ja foi encerrada')
        with engine.connect() as check:
            assert check.execute(text('SELECT current_database()')).scalar_one() == DB_NAME
            assert check.execute(text("SELECT to_regclass('public.ed02_assinatura_periodo')")).scalar_one() is not None
            for table in ('public.ed02_assinatura_periodo', 'public.pagamento_evento'):
                n = check.execute(text(f'''SELECT count(*) FROM {table}
                     WHERE payment_id = ANY(:ids)'''), {'ids': ids}).scalar_one()
                assert n == 0, 'dados ficticios residuais'
            n = check.execute(text('''SELECT count(*) FROM public.contratacao_checkout
                 WHERE external_reference = ANY(:refs)'''), {'refs': refs}).scalar_one()
            assert n == 0, 'contratos ficticios residuais'
            persisted = check.execute(text('SELECT status_pagamento,data_pagamento FROM public.empresa WHERE id=1')).one()
            assert (persisted[0] or '').lower() == 'pendente' and persisted[1] is None
            print('PASSOU: segunda conexao confirma zero residuos e estrutura intacta.')
    finally:
        if original is None:
            os.environ.pop('ED01_HOMOLOGACAO', None)
        else:
            os.environ['ED01_HOMOLOGACAO'] = original
        engine.dispose()

if __name__ == '__main__':
    main()
