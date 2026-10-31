"""AcheTece ED02.5 V5.8: comparador offline ED01 x ED02 (AUDITORIA).

NUNCA utilizar este modulo diretamente como filtro de visibilidade.
Sem banco, Flask, rede, pagamentos, gravacao ou ativacao automatica.
As observacoes ED01 sao entradas obtidas em ensaio/controladoria, nao uma
reimplementacao das regras SQL de publicacao.
"""
from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Optional, Tuple


PUBLICACAO_AUTOMATICA_AUTORIZADA = False
PERIODOS = Tuple[Tuple[datetime, datetime], ...]


@dataclass(frozen=True)
class LinhaConsolidacao:
    empresa_id: int
    ed01_visivel_observado: bool
    status_pagamento: str
    plano: Optional[str]
    data_pagamento_legado: Optional[datetime]
    periodos_ed02: PERIODOS = ()
    financeiro_ed02_conferido: bool = False
    legado_conciliado: bool = False
    estorno_confirmado: bool = False
    disputa_financeira: bool = False


@dataclass(frozen=True)
class ParecerAuditoria:
    empresa_id: int
    ed01_visivel_observado: bool
    classificacao: str
    divergencia: str
    ed02_candidato_diagnostico: bool
    autoriza_publicar: bool = False


def _aware(dt: datetime) -> bool:
    return isinstance(dt, datetime) and dt.tzinfo is not None and dt.utcoffset() is not None


def classificar(linha: LinhaConsolidacao, agora: datetime) -> str:
    """Classificacao conservadora, SEM produzir uma decisao de acesso publico."""
    if not _aware(agora):
        raise ValueError("agora deve ser um datetime com timezone")
    if not isinstance(linha.empresa_id, int) or isinstance(linha.empresa_id, bool) or linha.empresa_id <= 0:
        raise ValueError("empresa_id invalido")
    if type(linha.ed01_visivel_observado) is not bool:
        raise ValueError("observacao ED01 deve ser booleana")
    for campo in ("financeiro_ed02_conferido", "legado_conciliado", "estorno_confirmado", "disputa_financeira"):
        if type(getattr(linha, campo)) is not bool:
            raise ValueError("campo de validacao invalido")
    status = (linha.status_pagamento or "").strip().lower()
    if status != "ativo":
        return "BLOQUEADO_STATUS"
    if linha.estorno_confirmado or linha.disputa_financeira:
        return "REVISAO_FINANCEIRA_URGENTE"
    if (linha.plano or "").strip().lower() not in ("mensal", "anual"):
        return "REVISAO_PLANO"
    if linha.data_pagamento_legado is not None and not _aware(linha.data_pagamento_legado):
        return "REVISAO_DATA_LEGADA"
    ordenados = []
    for periodo in linha.periodos_ed02:
        if not isinstance(periodo, (tuple, list)) or len(periodo) != 2:
            return "REVISAO_PERIODO_INVALIDO"
        inicio, fim = periodo
        if not _aware(inicio) or not _aware(fim) or fim <= inicio:
            return "REVISAO_PERIODO_INVALIDO"
        ordenados.append((inicio, fim))
    ordenados.sort(key=lambda p: p[0])
    if any(seguinte[0] < anterior[1] for anterior, seguinte in zip(ordenados, ordenados[1:])):
        return "REVISAO_SOBREPOSICAO"
    if ordenados:
        vigente = any(inicio <= agora < fim for inicio, fim in ordenados)
        if vigente:
            return "ED02_VIGENTE_CONFERIDO_AUDITORIA" if linha.financeiro_ed02_conferido else "ED02_VIGENTE_SEM_CONFERENCIA"
        return "ED02_SEM_VIGENCIA_ATUAL"
    if linha.data_pagamento_legado is None:
        return "SEM_EVIDENCIA_FINANCEIRA"
    if linha.legado_conciliado:
        return "LEGADO_CONCILIADO_AGUARDA_TRANSICAO"
    return "LEGADO_A_CONCILIAR"


def comparar(linha: LinhaConsolidacao, agora: datetime) -> ParecerAuditoria:
    classificacao = classificar(linha, agora)
    ed02_candidato = classificacao == "ED02_VIGENTE_CONFERIDO_AUDITORIA"
    if linha.ed01_visivel_observado and classificacao == "REVISAO_FINANCEIRA_URGENTE":
        divergencia = "ALERTA_FINANCEIRO_ED01_VISIVEL"
    elif linha.ed01_visivel_observado and not ed02_candidato:
        divergencia = "ED01_VISIVEL_REQUER_CONCILIACAO"
    elif not linha.ed01_visivel_observado and ed02_candidato:
        divergencia = "ED02_CANDIDATO_MAS_ED01_OCULTO"
    elif linha.ed01_visivel_observado:
        divergencia = "CONVERGENTE_VISIVEL"
    else:
        divergencia = "CONVERGENTE_OCULTO_ED01"
    return ParecerAuditoria(
        empresa_id=linha.empresa_id,
        ed01_visivel_observado=linha.ed01_visivel_observado,
        classificacao=classificacao,
        divergencia=divergencia,
        ed02_candidato_diagnostico=ed02_candidato,
        autoriza_publicar=False,
    )


def resumo_agrupado(pareceres: Iterable[ParecerAuditoria]) -> dict:
    """Somente totais. Nao inclui identificadores ou dados pessoais."""
    resultados = list(pareceres)
    return {
        "quantidade": len(resultados),
        "classificacoes": dict(sorted(Counter(p.classificacao for p in resultados).items())),
        "divergencias": dict(sorted(Counter(p.divergencia for p in resultados).items())),
        "autorizacoes_publicas": sum(bool(p.autoriza_publicar) for p in resultados),
    }
