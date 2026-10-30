"""ED02.5 V5.7.5 - HTTP original + PostgreSQL real, SOMENTE LEITURA.

Não insere fixtures: valida a empresa pendente já existente. Qualquer cenário
ativo/vencido requer etapa posterior e transação rollback explicitamente isolada.
"""
import os
from urllib.parse import urlparse

HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DATABASE = 'achetece_ed01_test'
TRIGGER = 'ED02_RUN_ORIGINAL_HTTP_PG_ED025_V575'


def configure():
    if os.getenv('ED02_V575_READONLY_AUTHORIZED') != 'true':
        raise RuntimeError('BLOQUEADO: autorizacao ausente')
    if os.getenv('GITHUB_REF') != 'refs/heads/ed02-assinaturas-vigencia-ledger':
        raise RuntimeError('BLOQUEADO: branch nao autorizada')
    if os.getenv('GITHUB_EVENT_NAME') != 'push':
        raise RuntimeError('BLOQUEADO: evento nao autorizado')
    if os.getenv('ED02_V575_COMMIT_MESSAGE') != TRIGGER:
        raise RuntimeError('BLOQUEADO: commit nao autorizado')
    raw = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    p = urlparse(raw)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg', 'postgresql+psycopg2')
            and p.hostname == HOST and p.path == '/' + DATABASE
            and p.username and p.password and p.port in (None, 5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: URL de homologacao invalida')
    from sqlalchemy.engine import make_url
    u = make_url(raw)
    # Server-side enforcement: PostgreSQL rejeita DML e DDL em toda conexao.
    u = u.set(drivername='postgresql+psycopg', query={**dict(u.query), 'options': '-c default_transaction_read_only=on'})
    os.environ.pop('INTERNAL_DATABASE_URL', None)
    os.environ.pop('DATABASE_URL_INTERNAL', None)
    os.environ['SQLALCHEMY_DATABASE_URI'] = u.render_as_string(hide_password=False)
    os.environ['DATABASE_URL'] = os.environ['SQLALCHEMY_DATABASE_URI']
    os.environ['ALLOW_SQLITE_FALLBACK'] = 'false'
    os.environ['MAIL_SUPPRESS_SEND'] = 'true'
    os.environ['ED01_HOMOLOGACAO'] = 'true'
    os.environ['ED02_VIGENCIA_WRITE_HOMOLOG'] = 'false'
    os.environ['ED02_FINANCIAL_GUARD_HOMOLOG'] = 'false'


def run():
    configure()
    import main as m
    from sqlalchemy import text

    # Desliga antes de request todas as rotinas auxiliares com possivel escrita,
    # sem substituir as quatro funcoes de rota originais.
    m.app.before_request_funcs[None] = [
        cb for cb in m.app.before_request_funcs.get(None, [])
        if cb.__name__ not in {'_bootstrap_and_analytics_lazy'}
    ]
    m.track_event = lambda *a, **kw: None
    m.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)
    with m.app.app_context():
        with m.db.engine.connect() as conn:
            assert conn.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
            assert conn.execute(text('SELECT current_database()')).scalar_one() == DATABASE
            before = conn.execute(text('SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id')).all()
            assert len(before) == 1 and before[0][0] == 1, 'BLOQUEADO: inventario inesperado'
            assert (before[0][1] or '').lower() == 'pendente' and before[0][2] is None
        print('PASSOU: PostgreSQL autorizado, sessao somente leitura e empresa pendente confirmados.')
        client = m.app.test_client()
        checks = [
            ('/', 200),
            ('/?estado=SC&cidade=Jaragua%20do%20Sul&pagina=2', 200),
            ('/exportar', 200),
            ('/exportar?estado=SC&tipo=circular', 200),
            ('/utils/empresas_json?escopo=pagantes', 200),
            ('/utils/empresas_json?escopo=pagantes&uf=SC', 200),
            ('/empresa/1', 404),
            ('/empresa/999999', 404),
        ]
        for path, expected in checks:
            r = client.get(path)
            assert r.status_code == expected, f'FALHOU rota {path}: HTTP {r.status_code}'
            if path.startswith('/exportar'):
                assert len(r.get_data().decode('utf-8-sig').splitlines()) == 1, 'CSV nao esta vazio'
            if path.startswith('/utils/empresas_json'):
                assert r.get_json() == [], 'JSON expoe empresa pendente'
            if path == '/':
                # Busca retorna HTML, verificacao de ausencia via texto sensivel isolado.
                assert b'ED01 Malharia Teste' not in r.data, 'Home revela empresa pendente'
            print('PASSOU: rota original', path, 'HTTP', r.status_code)
        with m.db.engine.connect() as conn:
            assert conn.execute(text('SHOW transaction_read_only')).scalar_one() == 'on'
            after = conn.execute(text('SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id')).all()
            assert after == before, 'FALHOU: empresa alterada'
        print('PASSOU: rotas originais protegidas; sem alteracoes no banco.')
        print('LIMITACAO: nenhum registro ativo/vencido criado; nao comprova visibilidade positiva nem rollback de fixtures.')


if __name__ == '__main__':
    run()
