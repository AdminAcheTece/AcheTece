"""ED02.5 V5.9: comparacao ED01 x ED02 usando SQL real no PostgreSQL de teste.

NAO executa endpoints Flask, NAO altera registros, NAO confirma pagamentos.
Resultados somente agregados; sem ids, nomes, emails ou payload do provedor.
"""
from __future__ import annotations
import json
import os
from datetime import datetime, timezone
from urllib.parse import urlparse
from ed02_5_v58_consolidacao import LinhaConsolidacao
from ed02_5_v59_shadow import comparar_observacoes

HOST = "dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com"
BANCO = "achetece_ed01_test"
GATILHO = "ED02_RUN_SHADOW_ED025_V59_READONLY"

# ED01: espelha o contrato de Empresa.assinatura_ativa SQL da snapshot
# disponivel (ativo + data obrigatoria + 35/370 dias + fronteira exclusiva).
# Este SQL deve ser conferido se o main.py da branch mudar.
SQL_EMPRESAS = """
SELECT e.id, e.status_pagamento, e.plano, e.data_pagamento,
       (lower(coalesce(e.status_pagamento, '')) = 'ativo'
        AND e.data_pagamento IS NOT NULL
        AND (e.data_pagamento + CASE
             WHEN lower(coalesce(e.plano,''))='anual' THEN INTERVAL '370 days'
             ELSE INTERVAL '35 days' END) > NOW()) AS ed01_visivel
  FROM public.empresa e ORDER BY e.id
"""
SQL_PERIODOS = """
SELECT empresa_id, inicio, fim_exclusivo
  FROM public.ed02_assinatura_periodo
 ORDER BY empresa_id, inicio
"""


def guarda_execucao(environ=None):
    env = os.environ if environ is None else environ
    if env.get("ED02_V59_READONLY_AUTORIZADA") != "true" or \
       env.get("ED02_V59_COMMIT_MESSAGE") != GATILHO:
        raise RuntimeError("BLOQUEADO: gatilho nao autorizado")
    url = env.get("ED02_HOMOLOG_DATABASE_URL", "")
    parsed = urlparse(url)
    if not (parsed.scheme in ("postgresql", "postgresql+psycopg", "postgresql+psycopg2")
            and parsed.hostname == HOST and parsed.path.lstrip("/") == BANCO
            and parsed.username and parsed.password
            and parsed.port in (None, 5432) and not parsed.fragment):
        raise RuntimeError("BLOQUEADO: destino nao eh a homologacao autorizada")
    return url


def _dt_legado_utc(utc_naive):
    if utc_naive is None:
        return None
    if utc_naive.tzinfo is None:
        return utc_naive.replace(tzinfo=timezone.utc)
    return utc_naive.astimezone(timezone.utc)


def executar():
    url = guarda_execucao()
    from sqlalchemy import create_engine, text
    engine = create_engine(url, pool_pre_ping=True, connect_args={"connect_timeout": 8})
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.exec_driver_sql("SET TRANSACTION READ ONLY")
                if conn.execute(text("SELECT current_database()")).scalar_one() != BANCO:
                    raise RuntimeError("BLOQUEADO: banco conectado divergente")
                empresas = list(conn.execute(text(SQL_EMPRESAS)).mappings().all())
                periodos = conn.execute(text(SQL_PERIODOS)).mappings().all()
                map_periodos = {}
                for p in periodos:
                    map_periodos.setdefault(p["empresa_id"], []).append((p["inicio"], p["fim_exclusivo"]))
                agora = conn.execute(text("SELECT NOW()")).scalar_one()
                if agora.tzinfo is None:
                    raise RuntimeError("banco nao retornou NOW() timezone aware")
                linhas = [LinhaConsolidacao(
                    empresa_id=int(e["id"]),
                    ed01_visivel_observado=bool(e["ed01_visivel"]),
                    status_pagamento=e["status_pagamento"] or "",
                    plano=e["plano"],
                    data_pagamento_legado=_dt_legado_utc(e["data_pagamento"]),
                    periodos_ed02=tuple(map_periodos.get(e["id"], ())),
                    # Fica FALSO por padrao: o script nao consulta/confirma MP.
                    financeiro_ed02_conferido=False,
                    legado_conciliado=False,
                ) for e in empresas]
                resultado = comparar_observacoes(linhas, agora)
        resultado["periodos_ed02_total"] = len(periodos)
        resultado["pagamentos_provedor_verificados"] = False
        resultado["ativacao_ed02_autorizada"] = False
        print("PASSOU: PostgreSQL autorizado; transacao SOMENTE LEITURA; nenhuma gravacao.")
        print("SHADOW_AGREGADO=" + json.dumps(resultado, ensure_ascii=False, sort_keys=True))
        print("GATE: BLOQUEADO; consultas publicas ED01 permanecem inalteradas.")
    finally:
        engine.dispose()


if __name__ == '__main__':
    executar()
