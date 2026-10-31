"""ED02.5 V5.8: diagnostico agregado ED01 x ED02 em PostgreSQL READ ONLY.

Executar SOMENTE pelo job protegido; nenhum SQL de escrita, nenhuma informacao
individual. Nao eh um teste de endpoints HTTP nem autorizacao de ativacao.
"""
from __future__ import annotations

import json
import os
from urllib.parse import urlparse

BANCO = "achetece_ed01_test"
HOST = "dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com"
GATILHO = "ED02_RUN_CONSOLIDACAO_ED025_V58_READONLY"


def guarda_execucao(environ=None) -> str:
    e = os.environ if environ is None else environ
    if e.get("ED02_V58_READONLY_AUTORIZADA") != "true":
        raise RuntimeError("BLOQUEADO: auditoria nao autorizada")
    if e.get("ED02_V58_COMMIT_MESSAGE") != GATILHO:
        raise RuntimeError("BLOQUEADO: gatilho inesperado")
    url = e.get("ED02_HOMOLOG_DATABASE_URL", "")
    p = urlparse(url)
    if not (
        p.scheme in ("postgresql", "postgresql+psycopg", "postgresql+psycopg2")
        and p.hostname == HOST
        and p.path.lstrip("/") == BANCO
        and p.username and p.password
        and p.port in (None, 5432)
        and not p.fragment
    ):
        raise RuntimeError("BLOQUEADO: destino PostgreSQL nao autorizado")
    return url


# Todas as medidas sao agregadas; as tabelas individuais nao sao exportadas.
SQL_RESUMO = """
    SELECT
      COUNT(*) AS empresas_total,
      COUNT(*) FILTER (WHERE LOWER(COALESCE(e.status_pagamento, '')) = 'ativo') AS status_ativo,
      COUNT(*) FILTER (WHERE e.data_pagamento IS NOT NULL) AS com_data_legada,
      COUNT(*) FILTER (WHERE e.data_pagamento IS NULL) AS sem_data_legada,
      COUNT(*) FILTER (
        WHERE LOWER(COALESCE(e.status_pagamento, '')) = 'ativo'
          AND e.data_pagamento IS NOT NULL
          AND (e.data_pagamento + CASE
            WHEN LOWER(COALESCE(e.plano, '')) = 'anual' THEN INTERVAL '370 days'
            ELSE INTERVAL '35 days' END) > NOW()
      ) AS potencial_visivel_ed01,
      COUNT(*) FILTER (
        WHERE EXISTS (
          SELECT 1 FROM public.ed02_assinatura_periodo p
          WHERE p.empresa_id=e.id AND p.inicio <= NOW() AND p.fim_exclusivo > NOW()
        )
      ) AS com_periodo_vigente_ed02,
      COUNT(*) FILTER (
        WHERE LOWER(COALESCE(e.status_pagamento, '')) = 'ativo'
          AND e.data_pagamento IS NOT NULL
          AND (e.data_pagamento + CASE
            WHEN LOWER(COALESCE(e.plano, '')) = 'anual' THEN INTERVAL '370 days'
            ELSE INTERVAL '35 days' END) > NOW()
          AND NOT EXISTS (
            SELECT 1 FROM public.ed02_assinatura_periodo p
            WHERE p.empresa_id=e.id AND p.inicio <= NOW() AND p.fim_exclusivo > NOW()
          )
      ) AS ed01_potencial_visivel_sem_periodo_ed02,
      COUNT(*) FILTER (
        WHERE NOT (
          LOWER(COALESCE(e.status_pagamento, '')) = 'ativo'
          AND e.data_pagamento IS NOT NULL
          AND (e.data_pagamento + CASE
            WHEN LOWER(COALESCE(e.plano, '')) = 'anual' THEN INTERVAL '370 days'
            ELSE INTERVAL '35 days' END) > NOW()
        )
        AND EXISTS (
          SELECT 1 FROM public.ed02_assinatura_periodo p
          WHERE p.empresa_id=e.id AND p.inicio <= NOW() AND p.fim_exclusivo > NOW()
        )
      ) AS ed02_periodo_vigente_ed01_nao_visivel
    FROM public.empresa e
"""


def executar():
    url = guarda_execucao()
    from sqlalchemy import create_engine, text

    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 8})
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                nome = conn.execute(text("SELECT current_database()" )).scalar_one()
                if nome != BANCO:
                    raise RuntimeError("BLOQUEADO: banco conectado nao autorizado")
                # A tabela deve existir; nenhum fallback que esconda falha de migracao.
                resumo = dict(conn.execute(text(SQL_RESUMO)).mappings().one())
                periodos = conn.execute(text("SELECT COUNT(*) FROM public.ed02_assinatura_periodo")).scalar_one()
        resumo["periodos_ed02_total"] = int(periodos)
        resumo["leitura_apenas"] = True
        resumo["ativacao_autorizada"] = False
        print("PASSOU: banco homologacao validado; transacao SOMENTE LEITURA.")
        print("AUDITORIA_AGREGADA=" + json.dumps(resumo, ensure_ascii=False, sort_keys=True))
        print("LIMITACAO: indicativos agregados; nao valida pagamentos do provedor, nem executa rotas HTTP.")
    finally:
        engine.dispose()


if __name__ == "__main__":
    executar()
