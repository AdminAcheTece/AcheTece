"""ED02.5 V5.6 — classificador de auditoria, NUNCA gate de produção."""
from datetime import datetime, timezone


def classificar(status, data_pagamento, periodos, agora):
    """Retorna classificação conservadora sem atribuir visibilidade automaticamente.

    periodos: sequência de pares (inicio, fim_exclusivo) provenientes do banco.
    """
    if agora.tzinfo is None or agora.utcoffset() is None:
        raise ValueError("agora precisa de timezone")
    s = str(status or "").strip().lower()
    if s != "ativo":
        return "BLOQUEADO_STATUS"
    validos = []
    for inicio, fim in periodos:
        if not isinstance(inicio, datetime) or not isinstance(fim, datetime):
            return "REVISAR_PERIODO_INVALIDO"
        if inicio.tzinfo is None or fim.tzinfo is None or fim <= inicio:
            return "REVISAR_PERIODO_INVALIDO"
        validos.append(inicio <= agora < fim)
    if any(validos):
        return "ED02_VIGENTE_AUDITORIA"
    if periodos:
        return "ED02_SEM_VIGENCIA_ATUAL"
    if data_pagamento is not None:
        return "HISTORICO_REQUER_CONCILIACAO"
    return "SEM_COMPROVACAO_FINANCEIRA"
