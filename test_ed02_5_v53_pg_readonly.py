"""ED02.5 V5.3: consulta somente leitura na estrutura PERMANENTE de homologacao.
Nao cria transacoes financeiras, empresa, contrato, pagamento, periodo ou email.
"""
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine, text
from ed02_5_v52_migration import verify_schema

HOST='dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DB='achetece_ed01_test'

def main():
    url=os.environ.get('ED02_HOMOLOG_DATABASE_URL','')
    p=urlparse(url)
    if not (p.scheme in ('postgresql','postgresql+psycopg') and p.hostname==HOST
            and p.path=='/'+DB and p.username and p.password
            and p.port in (None,5432) and not p.fragment):
        raise RuntimeError('BLOQUEADO: banco nao autorizado')
    engine=create_engine(url,hide_parameters=True,pool_pre_ping=True,
                         connect_args={'sslmode':'require','connect_timeout':10})
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            conn.exec_driver_sql("SET LOCAL statement_timeout='10000ms'")
            if conn.execute(text('SELECT current_database()')).scalar_one()!=DB:
                raise RuntimeError('BLOQUEADO: banco efetivo incorreto')
            verify_schema(conn)
            print('PASSOU: tabela permanente e restricoes da vigencia verificadas.')
            # Confere a semantica do gate de leitura com empresa ficticia inexistente.
            row=conn.execute(text("""SELECT id FROM public.ed02_assinatura_periodo
                WHERE empresa_id=-1 AND inicio <= now() AND fim_exclusivo > now()
                LIMIT 1""")).first()
            if row is not None:
                raise RuntimeError('BLOQUEADO: leitura inesperada')
            print('PASSOU: consulta de periodo para empresa inexistente retorna vazio.')
            conn.rollback()
            print('PASSOU: operacao somente leitura; nenhuma gravacao ou migracao.')
    finally:
        engine.dispose()

if __name__=='__main__': main()
