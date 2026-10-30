"""ED02.5 V5.7.6 R2 — rotas originais, PG real e rollback.

NUNCA executar em produção. Somente GitHub Actions com gatilho exato.
As empresas/teares temporários usam IDs positivos explícitos, não há commit.
"""
import csv
import io
import os
import re
import secrets
from datetime import datetime, timedelta
from unittest.mock import patch
from urllib.parse import urlparse

HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DB = 'achetece_ed01_test'
TRIGGER = 'ED02_RUN_ELIGIBILITY_ED025_V576_R2'
PREFIX = 'ED02_V576_R2_FIXTURE_'


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
            raise RuntimeError('BLOQUEADO: autorizacao de homologacao invalida')

    raw = os.getenv('ED02_HOMOLOG_DATABASE_URL', '')
    p = urlparse(raw)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg', 'postgresql+psycopg2')
            and p.hostname == HOST and p.path == '/' + DB
            and p.username and p.password and p.port in (None, 5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: banco fora da homologacao autorizada')

    from sqlalchemy.engine import make_url
    url = make_url(raw).set(drivername='postgresql+psycopg')
    # A aplicação deve conectar apenas à homologação; flags ED02 desligadas.
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

    # Não mexer nas quatro funções de rota originais.
    # Evitar bootstrap/analytics com DML ou DDL durante as requisições.
    m.app.before_request_funcs[None] = [
        fn for fn in m.app.before_request_funcs.get(None, [])
        if fn.__name__ not in {'_bootstrap_and_analytics_lazy'}
    ]
    m.track_event = lambda *args, **kwargs: None
    m.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

    tag = PREFIX + secrets.token_hex(6)
    # IDs positivos (< 2^31) são obrigatórios para /empresa/<int:empresa_id>.
    # IDs explícitos também evitam avanço de sequences não transacionais.
    base = 1_500_000_000 + secrets.randbelow(100_000_000)
    company_ids = [base + i for i in range(4)]
    tear_ids = [base + 500 + i for i in range(4)]
    names = [tag + suffix for suffix in ('_ATIVA', '_PENDENTE', '_VENCIDA', '_SEM_DATA')]
    now = datetime.utcnow()
    statuses = [
        ('ativo', now - timedelta(days=1)),
        ('pendente', now - timedelta(days=1)),
        ('ativo', now - timedelta(days=60)),
        ('ativo', None),
    ]

    with m.app.app_context():
        engine = m.db.engine
        with engine.connect() as independent:
            assert independent.execute(text('SELECT current_database()')).scalar_one() == DB
            baseline = independent.execute(text(
                'SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id'
            )).all()
            if (len(baseline) != 1 or baseline[0][0] != 1
                    or (baseline[0][1] or '').strip().lower() != 'pendente'
                    or baseline[0][2] is not None):
                raise RuntimeError('BLOQUEADO: baseline da empresa de homologacao inesperado')
            baseline_teares = independent.execute(text('SELECT COUNT(*) FROM tear')).scalar_one()
            if independent.execute(select(m.Empresa.id).where(m.Empresa.id.in_(company_ids))).first():
                raise RuntimeError('BLOQUEADO: colisao com identificadores de empresa')
            if independent.execute(select(m.Tear.id).where(m.Tear.id.in_(tear_ids))).first():
                raise RuntimeError('BLOQUEADO: colisao com identificadores de tear')

        # Auditoria independente também roda se uma validação HTTP falhar.
        try:
            with engine.connect() as conn:
                outer_tx = conn.begin()
                try:
                    assert conn.execute(text('SELECT current_database()')).scalar_one() == DB
                    assert conn.execute(text('SHOW transaction_read_only')).scalar_one() == 'off'

                    # Inserções exclusivamente dentro da transação externa.
                    for i, ((status, payment_dt), name) in enumerate(zip(statuses, names)):
                        conn.execute(insert(m.Empresa.__table__).values(
                            id=company_ids[i], nome=name,
                            email=f'{tag.lower()}_{i}@example.invalid',
                            senha='INUTILIZAVEL-V576-R2', plano='mensal',
                            estado='SC', cidade='Jaragua do Sul',
                            status_pagamento=status, data_pagamento=payment_dt,
                        ))
                        conn.execute(insert(m.Tear.__table__).values(
                            id=tear_ids[i], marca='ED02_TESTE_R2', modelo=name,
                            tipo='circular', finura=28, diametro=30,
                            alimentadores=90, elastano='Não',
                            empresa_id=company_ids[i],
                        ))

                    with engine.connect() as independent:
                        unseen = independent.execute(select(m.Empresa.id).where(
                            m.Empresa.id.in_(company_ids)
                        )).all()
                        assert unseen == [], 'FALHOU: fixtures visiveis fora da transacao'
                    print('PASSOU: fixtures temporarias isoladas de conexoes independentes.')

                    # Flask-SQLAlchemy possui Session.get_bind customizado: configurar
                    # o sessionmaker isoladamente NAO garante o uso desta conexao.
                    # Override restrito ao processo de teste e desfeito pelo patch.
                    m.db.session.remove()
                    m.db.session.configure(bind=conn, join_transaction_mode='create_savepoint')
                    session_cls = m.db.session.session_factory.class_

                    def bind_on_test_connection(self, mapper=None, clause=None, bind=None, **kwargs):
                        return conn

                    with patch.object(session_cls, 'get_bind', bind_on_test_connection):
                        assert m.db.session().get_bind() is conn
                        visible = m.Empresa.query.filter(
                            m.Empresa.id == company_ids[0], m.Empresa.assinatura_ativa
                        ).count()
                        assert visible == 1, (
                            'FALHOU: expressao SQL de vigencia ED01 nao reconhece a fixture ativa'
                        )
                        visible_tears = m.Tear.query.join(m.Empresa).filter(
                            m.Tear.empresa_id == company_ids[0], m.Empresa.assinatura_ativa
                        ).count()
                        assert visible_tears == 1, (
                            'FALHOU: SQL de busca nao encontra tear da empresa elegivel'
                        )
                        print('PASSOU: mesma conexao transacional; ORM confirma empresa e tear elegiveis.')

                        http_sql = []

                        def readonly_http(connection, cursor, statement, parameters, context, executemany):
                            # Event listener é instalado após INSERTs das fixtures.
                            cleaned = re.sub(r'^\s*(?:/\*.*?\*/\s*)*', '', statement,
                                             flags=re.S).upper()
                            if re.match(r'^(INSERT|UPDATE|DELETE|ALTER|DROP|CREATE|TRUNCATE|'
                                        r'MERGE|COPY|GRANT|REVOKE)\b', cleaned):
                                raise RuntimeError('BLOQUEADO: escrita SQL durante HTTP')
                            if (connection is not conn and
                                    re.search(r'\b(?:FROM|JOIN)\s+"?(?:empresa|tear)"?\b',
                                              statement, flags=re.I)):
                                raise RuntimeError('BLOQUEADO: consulta a empresa/tear fora da transacao')
                            if connection is conn:
                                http_sql.append(statement)

                        event.listen(engine, 'before_cursor_execute', readonly_http)
                        try:
                            client = m.app.test_client()

                            def get(path, expected=200):
                                response = client.get(path)
                                assert response.status_code == expected, (
                                    f'FALHOU: {path}: HTTP {response.status_code} != {expected}'
                                )
                                return response

                            active, pending, expired, no_date = names
                            blocked = (pending, expired, no_date)
                            home = get('/').get_data(as_text=True)
                            assert active in home, 'FALHOU: empresa ativa ausente na home'
                            assert all(name not in home for name in blocked), (
                                'FALHOU: home divulgou empresa bloqueada'
                            )
                            filtered = get(
                                '/?estado=SC&cidade=Jaragua%20do%20Sul&tipo=circular'
                            ).get_data(as_text=True)
                            assert active in filtered, 'FALHOU: empresa ativa ausente nos filtros'
                            assert all(name not in filtered for name in blocked), (
                                'FALHOU: filtros divulgaram empresa bloqueada'
                            )
                            get('/?estado=SC&pagina=2&pp=1')
                            print('PASSOU: home, filtros e paginacao reais.')

                            for path in ('/exportar', '/exportar?estado=SC&tipo=circular'):
                                body = get(path).get_data(as_text=True).lstrip('\ufeff')
                                rows = list(csv.reader(io.StringIO(body)))
                                raw = '\n'.join(','.join(row) for row in rows)
                                assert active in raw, 'FALHOU: CSV omitiu empresa elegivel'
                                assert all(name not in raw for name in blocked), (
                                    'FALHOU: CSV divulgou bloqueada'
                                )
                            print('PASSOU: CSV das rotas originais publica somente elegiveis.')

                            for path in ('/utils/empresas_json?escopo=pagantes',
                                         '/utils/empresas_json?escopo=pagantes&uf=SC'):
                                payload = get(path).get_json()
                                assert isinstance(payload, list), 'FALHOU: JSON nao e lista'
                                found = {row['id'] for row in payload}
                                assert company_ids[0] in found and all(
                                    x not in found for x in company_ids[1:]
                                ), 'FALHOU: JSON nao respeita elegibilidade'
                            print('PASSOU: JSON original respeita elegibilidade.')

                            assert active in get(f'/empresa/{company_ids[0]}').get_data(
                                as_text=True
                            ), 'FALHOU: perfil ativo nao contem empresa'
                            for bad_id in company_ids[1:]:
                                get(f'/empresa/{bad_id}', 404)
                            print('PASSOU: perfil ativo HTTP 200; bloqueadas HTTP 404.')
                            assert http_sql, 'FALHOU: nenhuma consulta SQL na conexao HTTP'
                        finally:
                            event.remove(engine, 'before_cursor_execute', readonly_http)
                finally:
                    # Remover sessão e liberar savepoints antes do rollback externo.
                    try:
                        m.db.session.remove()
                        m.db.session.configure(bind=None,
                                               join_transaction_mode='conditional_savepoint')
                    finally:
                        if outer_tx.is_active:
                            outer_tx.rollback()
        finally:
            # Auditoria roda tanto em SUCESSO quanto em FALHA do ensaio.
            with engine.connect() as independent:
                after = independent.execute(text(
                    'SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id'
                )).all()
                assert after == baseline, 'FALHOU: empresa original foi alterada'
                assert independent.execute(text('SELECT COUNT(*) FROM tear')).scalar_one() == baseline_teares, (
                    'FALHOU: quantidade de teares original mudou'
                )
                assert independent.execute(select(m.Empresa.id).where(
                    m.Empresa.id.in_(company_ids)
                )).all() == [], 'FALHOU: fixtures de empresa persistiram'
                assert independent.execute(select(m.Tear.id).where(
                    m.Tear.id.in_(tear_ids)
                )).all() == [], 'FALHOU: fixtures de tear persistiram'
            print('PASSOU: auditoria independente apos rollback; zero residuos e baseline intacto.')
    print('LIMITACAO: homologacao ED01; politica ED02 continua desligada.')


if __name__ == '__main__':
    run()
