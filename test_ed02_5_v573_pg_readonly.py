"""Inventário PostgreSQL somente leitura. Não executa rotas HTTP nem altera dados."""
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine, text

DB = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'

def main():
    if os.environ.get('ED02_V573_READONLY_AUTHORIZED') != 'true':
        raise SystemExit('BLOQUEADO: gatilho ausente')
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    p = urlparse(url)
    if (p.scheme not in ('postgresql', 'postgresql+psycopg') or p.hostname != HOST
        or p.path.lstrip('/') != DB or not p.username or not p.password
        or p.port not in (None, 5432) or p.fragment):
        raise SystemExit('BLOQUEADO: destino nao autorizado')
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    with engine.connect() as c:
        with c.begin():
            c.execute(text('SET TRANSACTION READ ONLY'))
            actual = c.execute(text('SELECT current_database()')).scalar_one()
            if actual != DB:
                raise SystemExit('BLOQUEADO: banco conectado divergente')
            # Agregados nao identificaveis, sem dados pessoais
            total = c.execute(text('SELECT count(*) FROM empresa')).scalar_one()
            print('PASSOU: banco autorizado, transacao READ ONLY. Empresas:', total)
            print('INFO: nao executa endpoint HTTP nem aplica ajustes na visibilidade.')
    engine.dispose()

if __name__ == '__main__':
    main()
