"""ED02.5 V5.2: migração persistente, EXCLUSIVA do PostgreSQL de homologação.
Não importar pela aplicação. Execução manual protegida via GitHub Actions.
"""
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine, text

DB = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
TABLE = 'public.ed02_assinatura_periodo'
REQUIRED = {'id','empresa_id','contrato_id','provedor','payment_id','plano','inicio','fim_exclusivo','criado_em'}

DDL = """
CREATE TABLE public.ed02_assinatura_periodo (
 id BIGSERIAL PRIMARY KEY,
 empresa_id BIGINT NOT NULL REFERENCES public.empresa(id),
 contrato_id BIGINT NOT NULL REFERENCES public.contratacao_checkout(id),
 provedor VARCHAR(40) NOT NULL DEFAULT 'mercadopago',
 payment_id VARCHAR(160) NOT NULL,
 plano VARCHAR(16) NOT NULL CHECK (plano IN ('mensal', 'anual')),
 inicio TIMESTAMPTZ NOT NULL,
 fim_exclusivo TIMESTAMPTZ NOT NULL,
 criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ed02_vigencia_intervalo_chk CHECK (fim_exclusivo > inicio),
 CONSTRAINT ed02_vigencia_pagamento_uniq UNIQUE (provedor, payment_id),
 CONSTRAINT ed02_vigencia_contrato_uniq UNIQUE (contrato_id)
)
"""
INDEX = 'CREATE INDEX ed02_vigencia_empresa_fim_idx ON public.ed02_assinatura_periodo (empresa_id, fim_exclusivo DESC)'


def validate_url(url):
    p = urlparse(url)
    if not (p.scheme in ('postgresql','postgresql+psycopg') and p.hostname == HOST
            and p.path == '/' + DB and p.username and p.password
            and p.port in (None,5432) and not p.fragment):
        raise RuntimeError('BLOQUEADO: destino PostgreSQL nao autorizado')


def verify_schema(conn):
    cols = {r[0]: (r[1],r[2]) for r in conn.execute(text("""
      SELECT column_name, data_type, is_nullable FROM information_schema.columns
      WHERE table_schema='public' AND table_name='ed02_assinatura_periodo'
    """))}
    if set(cols) != REQUIRED:
        raise RuntimeError('BLOQUEADO: colunas da vigencia divergentes')
    for field in ('empresa_id','contrato_id','payment_id','plano','inicio','fim_exclusivo'):
        if cols[field][1] != 'NO':
            raise RuntimeError('BLOQUEADO: coluna permite nulos')
    constraints = {r[0]: r[1] for r in conn.execute(text("""
      SELECT conname, contype FROM pg_constraint
      WHERE conrelid = 'public.ed02_assinatura_periodo'::regclass
    """))}
    expected = {'ed02_assinatura_periodo_pkey':'p', 'ed02_vigencia_intervalo_chk':'c',
                'ed02_vigencia_pagamento_uniq':'u','ed02_vigencia_contrato_uniq':'u'}
    if not all(constraints.get(k)==v for k,v in expected.items()) or list(constraints.values()).count('f') < 2:
        raise RuntimeError('BLOQUEADO: restricoes ou chaves estrangeiras divergentes')
    index = conn.execute(text("""
      SELECT 1 FROM pg_indexes WHERE schemaname='public' AND tablename='ed02_assinatura_periodo'
      AND indexname='ed02_vigencia_empresa_fim_idx'
    """)).scalar_one_or_none()
    if index != 1:
        raise RuntimeError('BLOQUEADO: indice ausente')


def main():
    if os.environ.get('ED02_V52_MIGRATION_AUTHORIZED') != 'true':
        raise RuntimeError('BLOQUEADO: sem autorizacao explicita da V5.2')
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL','')
    validate_url(url)
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True, echo=False,
                           connect_args={'sslmode':'require', 'connect_timeout':10})
    created = False
    try:
        with engine.begin() as conn:
            conn.execute(text("SET LOCAL statement_timeout='15000ms'"))
            conn.execute(text("SET LOCAL lock_timeout='5000ms'"))
            if conn.execute(text('SELECT current_database()')).scalar_one() != DB:
                raise RuntimeError('BLOQUEADO: banco efetivo incorreto')
            conn.execute(text('SELECT pg_advisory_xact_lock(208025, 52)'))
            for t in ('public.empresa','public.contratacao_checkout','public.pagamento_evento'):
                if conn.execute(text('SELECT to_regclass(:t)'), {'t':t}).scalar_one() is None:
                    raise RuntimeError('BLOQUEADO: prerequisito financeiro ausente')
            present = conn.execute(text('SELECT to_regclass(:t)'), {'t':TABLE}).scalar_one() is not None
            if not present:
                conn.execute(text(DDL))
                conn.execute(text(INDEX))
                created = True
            verify_schema(conn)
            # Preservar qualquer dado preexistente; nunca truncate, delete ou drop.
            print('PASSOU: destino e prerequisitos conferidos; estrutura validada.')
        # O commit ocorreu. Confirmar em outra conexão, sem dados de acesso nos logs.
        with engine.connect() as conn:
            if conn.execute(text('SELECT current_database()')).scalar_one() != DB:
                raise RuntimeError('BLOQUEADO: verificacao pos-commit em outro banco')
            verify_schema(conn)
            print('PASSOU: segunda conexao confirma estrutura persistida.')
            print('PASSOU: empresa e pagamentos nao foram alterados pela migracao.')
            print('STATUS: ' + ('CRIADA' if created else 'JA_EXISTENTE_VALIDADA'))
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
