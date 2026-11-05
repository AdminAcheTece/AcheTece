"""ED02.5 V5.10 — afericao shadow nas quatro superfícies HTTP ORIGINAIS.

Sem alteração da resposta, sem decisão de publicação, sem informações pessoais.
O hook só pode ser registrado sob flags de HOMOLOGAÇÃO. Os grupos por rota
são diagnósticos, NÃO substituem testes de paginação ou contrato histórico.
"""
from __future__ import annotations

from collections import defaultdict
from datetime import timezone
import logging
import os

from ed02_5_v58_consolidacao import LinhaConsolidacao
from ed02_5_v59_shadow import comparar_observacoes
from ed02_5_v59_pg_shadow_readonly import SQL_EMPRESAS, SQL_PERIODOS, _dt_legado_utc

ALVOS = frozenset(("index", "exportar", "utils_empresas_json", "empresa_perfil"))
BANCO = "achetece_ed01_test"
MAX_EMPRESAS = 2000  # evita custo inesperado, sem permitir fallback permissivo


def escopo_filtros(endpoint: str, args: dict, empresa_id=None):
    """Converte apenas parâmetros conhecidos em predicados parametrizados.

    A observação é por coorte, não pela página individual de resultados.
    """
    if endpoint not in ALVOS:
        raise ValueError("endpoint nao permitido")
    if endpoint == "empresa_perfil":
        if type(empresa_id) is not int or empresa_id < 1:
            raise ValueError("empresa invalida")
        return "e.id=:empresa_id", {"empresa_id": empresa_id}, False
    clausulas = []
    parametros = {}
    filtros = dict(args)
    if endpoint in ("index", "exportar"):
        for chave, coluna in (("tipo", "t.tipo"), ("estado", "e.estado"), ("cidade", "e.cidade")):
            valor = str(filtros.get(chave) or "").strip()
            if valor:
                parametros[chave] = valor
                clausulas.append(f"lower({coluna})=lower(:{chave})")
        for chave, coluna in (("diâmetro", "t.diametro"), ("galga", "t.finura")):
            valor = str(filtros.get(chave) or filtros.get("diametro" if chave == "diâmetro" else chave) or "").strip()
            if valor:
                try:
                    valor_num = int(valor)
                except (ValueError, TypeError):
                    raise ValueError("filtro numerico invalido") from None
                if not 0 <= valor_num <= 100000:
                    raise ValueError("filtro fora de limite")
                parametros["filtro_" + coluna.split(".")[1]] = valor_num
                clausulas.append(f"{coluna}=:filtro_{coluna.split('.')[1]}")
        # A coorte HTTP tem pelo menos um tear. Os filtros ED01 não são alterados.
        return " AND ".join(clausulas) or "TRUE", parametros, True
    uf = str(filtros.get("uf") or "").strip()
    if uf:
        if len(uf) != 2 or not uf.isalpha():
            raise ValueError("UF invalida")
        clausulas.append("upper(e.estado)=upper(:uf)")
        parametros["uf"] = uf
    # escopo=demo depende de DEMO_FILTER e é documentado como comparação de
    # coorte geográfica ampliada; não afirmar equivalência ao JSON exibido.
    return " AND ".join(clausulas) or "TRUE", parametros, False


def observar_http(db, endpoint: str, args: dict, empresa_id=None) -> dict:
    """Executa SELECTs em conexão independente e transação READ ONLY."""
    from sqlalchemy import text
    condicao, parametros, usa_tear = escopo_filtros(endpoint, args, empresa_id)
    engine = db.engine
    if engine.dialect.name != "postgresql":
        raise RuntimeError("dialeto nao autorizado")
    with engine.connect() as conn:
        with conn.begin():
            conn.exec_driver_sql("SET TRANSACTION READ ONLY")
            if conn.execute(text("SELECT current_database()")).scalar_one() != BANCO:
                raise RuntimeError("banco nao autorizado")
            total = conn.execute(text("SELECT count(*) FROM public.empresa")).scalar_one()
            if total > MAX_EMPRESAS:
                raise RuntimeError("coorte acima de limite observacional")
            if usa_tear:
                sql = "SELECT DISTINCT e.id FROM public.empresa e JOIN public.tear t ON t.empresa_id=e.id WHERE " + condicao
            else:
                sql = "SELECT e.id FROM public.empresa e WHERE " + condicao
            ids = {int(x) for x in conn.execute(text(sql), parametros).scalars().all()}
            empresas = conn.execute(text(SQL_EMPRESAS)).mappings().all()
            periodos = conn.execute(text(SQL_PERIODOS)).mappings().all()
            agora = conn.execute(text("SELECT now()")).scalar_one()
            if agora.tzinfo is None:
                raise RuntimeError("NOW sem fuso")
            mapa = defaultdict(list)
            for p in periodos:
                mapa[int(p["empresa_id"])].append((p["inicio"], p["fim_exclusivo"]))
            linhas = [LinhaConsolidacao(
                empresa_id=int(e["id"]),
                ed01_visivel_observado=bool(e["ed01_visivel"]),
                status_pagamento=e["status_pagamento"] or "",
                plano=e["plano"],
                data_pagamento_legado=_dt_legado_utc(e["data_pagamento"]),
                periodos_ed02=tuple(mapa.get(int(e["id"]), ())),
                financeiro_ed02_conferido=False,
                legado_conciliado=False,
            ) for e in empresas if int(e["id"]) in ids]
            resultado = comparar_observacoes(linhas, agora)
    if resultado["publicacoes_autorizadas"] != 0 or resultado["modifica_consultas_publicas"]:
        raise RuntimeError("comparador violou contrato observacional")
    return resultado



def contar_divergencias_reais(resumo: dict) -> int:
    """Soma alertas financeiros, nao classificacoes convergentes.

    A camada shadow continua exclusivamente observacional: nunca publica.
    Chaves futuras desconhecidas contam como alertas para evitar subcontagem.
    """
    categorias = resumo.get("divergencias")
    if not isinstance(categorias, dict):
        raise ValueError("diagnostico malformado")
    total = 0
    for nome, valor in categorias.items():
        if type(nome) is not str or type(valor) is not int or valor < 0:
            raise ValueError("contagem de diagnostico invalida")
        if nome not in ("CONVERGENTE_VISIVEL", "CONVERGENTE_OCULTO_ED01"):
            total += valor
    return total

def instalar_shadow_http(app, db) -> bool:
    """Somente registrar hook sob aprovação explícita da HOMOLOGAÇÃO."""
    if not (os.getenv("ED02_V510_HTTP_SHADOW_HOMOLOG", "").strip().lower() == "true"
            and os.getenv("ED01_HOMOLOGACAO", "").strip().lower() == "true"):
        return False

    @app.after_request
    def _v510_shadow_diagnostico(response):
        from flask import request
        # Não tocar em payload, dados pessoais ou resposta do visitante.
        if request.endpoint not in ALVOS or response.status_code >= 500:
            return response
        try:
            resumo = observar_http(db, request.endpoint, request.args.to_dict(flat=True),
                                   (request.view_args or {}).get("empresa_id"))
            # Somente contagens agregadas; não logar query string, IDs ou payload.
            app.logger.info("[V510 SHADOW] rota=%s escopo=coorte contagem=%d divergencias=%d publicacoes=0",
                            request.endpoint, resumo["empresas_auditadas"],
                            contar_divergencias_reais(resumo))
        except Exception:
            app.logger.warning("[V510 SHADOW] diagnostico indisponivel; detalhes omitidos")
        return response

    return True
