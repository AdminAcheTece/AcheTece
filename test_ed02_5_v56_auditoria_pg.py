"""Auditoria agregada, somente leitura, de empresas ED01 e periodos ED02."""
import os
from sqlalchemy import create_engine, text
from ed02_5_v52_migration import validate_url, verify_schema, DB


def main():
    if os.getenv('ED02_V56_READONLY_AUTHORIZED') != 'true':
        raise RuntimeError('BLOQUEADO: auditoria V5.6 nao autorizada')
    url = os.getenv('ED02_HOMOLOG_DATABASE_URL','')
    validate_url(url)
    engine=create_engine(url, pool_pre_ping=True, hide_parameters=True, echo=False,
                         connect_args={'sslmode':'require','connect_timeout':10})
    try:
        with engine.connect() as c:
            tx=c.begin()
            try:
                c.execute(text('SET TRANSACTION READ ONLY'))
                c.execute(text("SET LOCAL statement_timeout = '15000ms'"))
                if c.execute(text('SELECT current_database()')).scalar_one()!=DB:
                    raise RuntimeError('BLOQUEADO: banco efetivo incorreto')
                verify_schema(c)
                results = c.execute(text("""
                    SELECT COALESCE(lower(e.status_pagamento),'(nulo)') AS estado,
                           COALESCE(lower(e.plano),'(nulo)') AS plano,
                           CASE WHEN e.data_pagamento IS NULL THEN 'sem_data' ELSE 'com_data' END AS legado,
                           CASE WHEN p.empresa_id IS NULL THEN 'sem_periodo'
                                WHEN p.vigente THEN 'ed02_vigente'
                                ELSE 'ed02_sem_vigencia_atual' END AS ed02,
                           COUNT(*) AS total
                    FROM public.empresa e
                    LEFT JOIN (
                      SELECT empresa_id, bool_or(inicio <= now() AND fim_exclusivo > now()) AS vigente
                      FROM public.ed02_assinatura_periodo GROUP BY empresa_id
                    ) p ON p.empresa_id=e.id
                    GROUP BY 1,2,3,4 ORDER BY 1,2,3,4
                """)).all()
                for estado, plano, legado, ed02, total in results:
                    print(f'AUDITORIA: status={estado} plano={plano} legado={legado} ed02={ed02} quantidade={total}')
                print('PASSOU: auditoria agrupada somente leitura; nenhuma empresa alterada.')
            finally:
                tx.rollback()
    finally:
        engine.dispose()

if __name__=='__main__': main()
