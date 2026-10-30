"""ED02.5: ensaio DDL transacional, sem qualquer alteracao persistente.
Exige segredo apenas no job GitHub protegido; nao imprime URL/credenciais.
"""
import os
from urllib.parse import urlparse
from pathlib import Path
from sqlalchemy import create_engine, text

HOST='dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DB='achetece_ed01_test'
TABLE='public.ed02_assinatura_periodo'

def guard(url):
    p=urlparse(url)
    if not (p.scheme in ('postgresql','postgresql+psycopg2','postgresql+psycopg')
            and p.hostname == HOST and p.path == '/' + DB
            and p.username and p.password and p.port in (None,5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: destino PostgreSQL nao autorizado')

def main():
    url=os.environ.get('ED02_HOMOLOG_DATABASE_URL','')
    guard(url)
    sql=Path(__file__).with_name('ed02_5_schema_homolog.sql').read_text(encoding='utf-8')
    # Retirar delimitadores, preservando um unico controle de transacao externo.
    lines=[line for line in sql.splitlines() if line.strip().upper() not in ('BEGIN;','COMMIT;')]
    ddl='\n'.join(lines)
    engine=create_engine(url, pool_pre_ping=True, hide_parameters=True,
                         connect_args={'sslmode':'require'}, echo=False)
    try:
        with engine.connect() as conn:
            tx=conn.begin()
            try:
                actual=conn.execute(text('select current_database()')).scalar_one()
                if actual != DB:
                    raise RuntimeError('BLOQUEADO: banco efetivo incorreto')
                for tbl in ('public.empresa','public.contratacao_checkout','public.pagamento_evento'):
                    if conn.execute(text('select to_regclass(:t)'),{'t':tbl}).scalar_one() is None:
                        raise RuntimeError('BLOQUEADO: pre-requisito ausente')
                # Evitar efeitos em tabela de vigencia preexistente.
                existed=conn.execute(text('select to_regclass(:t)'),{'t':TABLE}).scalar_one() is not None
                if existed:
                    raise RuntimeError('BLOQUEADO: tabela de vigencia ja existe; revisar antes de nova migracao')
                conn.exec_driver_sql("SET LOCAL lock_timeout='3s'")
                conn.exec_driver_sql("SET LOCAL statement_timeout='15s'")
                conn.exec_driver_sql(ddl)
                if conn.execute(text('select to_regclass(:t)'),{'t':TABLE}).scalar_one() is None:
                    raise AssertionError('DDL nao criou a tabela no ensaio')
                cols={r[0] for r in conn.execute(text("""select column_name from information_schema.columns
                         where table_schema='public' and table_name='ed02_assinatura_periodo'""")).all()}
                expected={'id','empresa_id','contrato_id','provedor','payment_id','plano','inicio','fim_exclusivo','criado_em'}
                if not expected.issubset(cols):
                    raise AssertionError('DDL com colunas inesperadas')
                print('PASSOU: destino e pre-requisitos confirmados.')
                print('PASSOU: DDL executado e colunas verificadas na transacao de ensaio.')
            finally:
                tx.rollback()
                print('PASSOU: rollback da transacao de ensaio executado.')
        with engine.connect() as verify:
            if verify.execute(text('select current_database()')).scalar_one()!=DB:
                raise RuntimeError('BLOQUEADO: banco da segunda conexao incorreto')
            if verify.execute(text('select to_regclass(:t)'),{'t':TABLE}).scalar_one() is not None:
                raise AssertionError('CRITICO: DDL deixou tabela persistente')
            print('PASSOU: segunda conexao confirma tabela ausente e nenhuma migracao persistida.')
    finally:
        engine.dispose()

if __name__=='__main__': main()
