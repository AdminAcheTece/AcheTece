"""V5.1: preflight SOMENTE LEITURA no PostgreSQL exclusivo de homologacao."""
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine, text

DB = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'


def main():
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    p = urlparse(url)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg2', 'postgresql+psycopg')
            and p.hostname == HOST and p.path == '/' + DB
            and p.username and p.password and p.port in (None, 5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: destino nao autorizado')
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={'sslmode': 'require'}, echo=False)
    try:
        with engine.connect() as conn:
            conn.exec_driver_sql("SET statement_timeout='10000ms'")
            conn.exec_driver_sql('SET TRANSACTION READ ONLY')
            if conn.execute(text('SELECT current_database()')).scalar_one() != DB:
                raise RuntimeError('BLOQUEADO: banco efetivo incorreto')
            print('PASSOU: banco de homologacao confirmado.')
            for table in ('public.empresa', 'public.contratacao_checkout', 'public.pagamento_evento'):
                if conn.execute(text('SELECT to_regclass(:t)'), {'t': table}).scalar_one() is None:
                    raise RuntimeError('BLOQUEADO: pre-requisito nao encontrado')
            print('PASSOU: tabelas financeiras anteriores presentes.')
            has_period = conn.execute(text('SELECT to_regclass(:t)'),
                                      {'t': 'public.ed02_assinatura_periodo'}).scalar_one() is not None
            print('OBSERVADO: tabela de vigencia ' + ('presente' if has_period else 'ausente') + '.')
            if has_period:
                cols = {r[0] for r in conn.execute(text("""
                    SELECT column_name FROM information_schema.columns
                    WHERE table_schema='public' AND table_name='ed02_assinatura_periodo'
                """)).all()}
                required = {'id', 'empresa_id', 'contrato_id', 'provedor', 'payment_id',
                            'plano', 'inicio', 'fim_exclusivo', 'criado_em'}
                if not required.issubset(cols):
                    raise RuntimeError('BLOQUEADO: tabela existente com colunas divergentes')
                print('PASSOU: colunas da tabela existente conferidas.')
            # Importante: o preflight apenas observa; nao cria nem migra tabelas.
            conn.rollback()
            print('PASSOU: somente leitura; nenhuma migracao executada.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
