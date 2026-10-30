"""ED02.5 V5.7.6: rotas originais, fixtures nao confirmadas e rollback.

Executar apenas pelo workflow protegido. Falha fechada; NUNCA usar em producao.
"""
import csv
import io
import os
import re
import secrets
from datetime import datetime, timedelta
from urllib.parse import urlparse

HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DB = 'achetece_ed01_test'
TRIGGER = 'ED02_RUN_ELIGIBILITY_ED025_V576'
PREFIX = 'ED02_V576_FIXTURE_'


def configure():
    required = {
        'ED02_V576_AUTHORIZED': 'true',
        'GITHUB_EVENT_NAME': 'push',
        'GITHUB_REF': 'refs/heads/ed02-assinaturas-vigencia-ledger',
        'ED02_V576_COMMIT_MESSAGE': TRIGGER,
        'GITHUB_ACTOR': 'AdminAcheTece',
    }
    for key, expected in required.items():
        if os.getenv(key) != expected:
            raise RuntimeError('BLOQUEADO: gate de homologacao invalido')
    raw = os.getenv('ED02_HOMOLOG_DATABASE_URL', '')
    p = urlparse(raw)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg', 'postgresql+psycopg2')
            and p.hostname == HOST and p.path == '/' + DB and p.username and p.password
            and p.port in (None, 5432) and not p.fragment):
        raise RuntimeError('BLOQUEADO: banco fora da homologacao autorizada')
    from sqlalchemy.engine import make_url
    url = make_url(raw).set(drivername='postgresql+psycopg')
    os.environ.pop('INTERNAL_DATABASE_URL', None)
    os.environ.pop('DATABASE_URL_INTERNAL', None)
    os.environ['SQLALCHEMY_DATABASE_URI'] = url.render_as_string(hide_password=False)
    os.environ['DATABASE_URL'] = os.environ['SQLALCHEMY_DATABASE_URI']
    os.environ['ALLOW_SQLITE_FALLBACK'] = 'false'
    os.environ['MAIL_SUPPRESS_SEND'] = 'true'
    os.environ['ED01_HOMOLOGACAO'] = 'true'
    os.environ['ED02_VIGENCIA_WRITE_HOMOLOG'] = 'false'
    os.environ['ED02_FINANCIAL_GUARD_HOMOLOG'] = 'false'
    os.environ['ED02_EXACT_ONCE_HOMOLOG'] = 'false'


def run():
    configure()
    import main as m
    from sqlalchemy import event, insert, select, text

    # Impede callbacks de bootstrap e analytics que poderiam gerar commits ou DDL.
    m.app.before_request_funcs[None] = [
        fn for fn in m.app.before_request_funcs.get(None, [])
        if fn.__name__ not in {'_bootstrap_and_analytics_lazy'}
    ]
    m.track_event = lambda *a, **kw: None
    m.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

    tag = PREFIX + secrets.token_hex(5)
    ids = [-secrets.randbelow(500_000_000) - 100_000_000 - i for i in range(4)]
    names = [tag + suffix for suffix in ('_ATIVA', '_PENDENTE', '_VENCIDA', '_SEM_DATA')]
    now = datetime.utcnow()
    statuses = [('ativo', now - timedelta(days=1)),
                ('pendente', now - timedelta(days=1)),
                ('ativo', now - timedelta(days=60)),
                ('ativo', None)]
    engine = None
    guarded = False
    sql_seen = []
    with m.app.app_context():
        engine = m.db.engine
        with engine.connect() as separate:
            assert separate.execute(text('SELECT current_database()')).scalar_one() == DB
            baseline = separate.execute(text('SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id')).all()
            if len(baseline) != 1 or baseline[0][0] != 1 or (baseline[0][1] or '').lower() != 'pendente' or baseline[0][2] is not None:
                raise RuntimeError('BLOQUEADO: baseline de homologacao inesperado')
        # Uma transacao externa mantem todas as fixtures invisiveis a outras conexoes.
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                assert conn.execute(text('SELECT current_database()')).scalar_one() == DB
                assert conn.execute(text('SHOW transaction_read_only')).scalar_one() == 'off'
                for i, ((status, payment_dt), name) in enumerate(zip(statuses, names)):
                    conn.execute(insert(m.Empresa.__table__).values(
                        id=ids[i], nome=name, email=f'{tag.lower()}_{i}@example.invalid',
                        senha='INUTILIZAVEL-V576', plano='mensal',
                        estado='SC', cidade='Jaragua do Sul', status_pagamento=status,
                        data_pagamento=payment_dt))
                    conn.execute(insert(m.Tear.__table__).values(
                        marca='V576_TESTE', modelo=name, tipo='circular', finura=28,
                        diametro=30, alimentadores=90, elastano='Não', empresa_id=ids[i]))
                # Confirma que nao houve commit; outra conexao NAO enxerga fixtures.
                with engine.connect() as separate:
                    n = separate.execute(select(m.Empresa.id).where(m.Empresa.id.in_(ids))).all()
                    assert not n, 'FALHOU: fixtures ficaram publicas antes do rollback'

                # Flask-SQLAlchemy resolve as queries na mesma conexao transacional.
                m.db.session.remove()
                m.db.session.configure(bind=conn, join_transaction_mode='create_savepoint')

                def assert_readonly_sql(connection, cursor, statement, parameters, context, executemany):
                    if connection is not conn:
                        return
                    sql_seen.append(statement)
                    cleaned = re.sub(r'^\s*(?:/\*.*?\*/\s*)*', '', statement, flags=re.S).upper()
                    if re.match(r'^(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|MERGE|COPY|GRANT|REVOKE)\b', cleaned):
                        raise RuntimeError('BLOQUEADO: escrita durante chamadas HTTP')

                event.listen(engine, 'before_cursor_execute', assert_readonly_sql)
                guarded = True
                client = m.app.test_client()

                def get(path, expected=200):
                    response = client.get(path)
                    assert response.status_code == expected, f'FALHOU: {path}: HTTP {response.status_code} != {expected}'
                    return response

                active, pending, expired, no_date = names
                home = get('/').get_data(as_text=True)
                assert active in home, 'FALHOU: empresa ativa nao aparece na home'
                for name in (pending, expired, no_date):
                    assert name not in home, 'FALHOU: empresa bloqueada aparece na home'
                filtered = get('/?estado=SC&cidade=Jaragua%20do%20Sul&tipo=circular').get_data(as_text=True)
                assert active in filtered, 'FALHOU: ativa nao aparece com filtros'
                for name in (pending, expired, no_date):
                    assert name not in filtered, 'FALHOU: filtros divulgam empresa bloqueada'
                get('/?estado=SC&pagina=2&pp=1')
                print('PASSOU: home, filtros e paginacao com consulta SQL real.')

                for path in ('/exportar', '/exportar?estado=SC&tipo=circular'):
                    body = get(path).get_data(as_text=True).lstrip('\ufeff')
                    rows = list(csv.reader(io.StringIO(body)))
                    raw = '\n'.join(','.join(row) for row in rows)
                    assert active in raw, 'FALHOU: CSV nao inclui empresa ativa'
                    for name in (pending, expired, no_date):
                        assert name not in raw, 'FALHOU: CSV divulga bloqueada'
                print('PASSOU: CSV original inclui apenas a fixture elegivel.')

                for path in ('/utils/empresas_json?escopo=pagantes',
                             '/utils/empresas_json?escopo=pagantes&uf=SC'):
                    payload = get(path).get_json()
                    assert isinstance(payload, list)
                    seen = {row['id'] for row in payload}
                    assert ids[0] in seen and all(bad not in seen for bad in ids[1:])
                print('PASSOU: JSON original inclui ativa e bloqueia demais.')

                assert active in get(f'/empresa/{ids[0]}').get_data(as_text=True)
                for bad in ids[1:]:
                    get(f'/empresa/{bad}', 404)
                print('PASSOU: perfil original ativo HTTP 200; bloqueadas HTTP 404.')
                assert len(sql_seen) > 0, 'FALHOU: nao foi observada consulta SQL'
            finally:
                if guarded:
                    event.remove(engine, 'before_cursor_execute', assert_readonly_sql)
                m.db.session.remove()
                # Mesmo se alguma rota falhar, nenhuma fixture e confirmada.
                tx.rollback()
                # Evita vazamento de bind para uso futuro da sessao neste processo.
                m.db.session.configure(bind=None)
        with engine.connect() as separate:
            after = separate.execute(text('SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id')).all()
            assert after == baseline, 'FALHOU: empresa original modificada'
            residues = separate.execute(select(m.Empresa.id).where(m.Empresa.id.in_(ids))).all()
            assert residues == [], 'FALHOU: empresas temporarias persistiram'
            residues_teares = separate.execute(select(m.Tear.id).where(m.Tear.empresa_id.in_(ids))).all()
            assert residues_teares == [], 'FALHOU: teares temporarios persistiram'
        print('PASSOU: rollback e segunda conexao confirmam zero residuos; empresa original intacta.')
        print('LIMITACAO: ED01 vigente testada; politica de elegibilidade ED02 permanece desligada.')


if __name__ == '__main__':
    run()
