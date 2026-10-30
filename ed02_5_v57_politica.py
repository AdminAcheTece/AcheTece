"""ED02.5 V5.7: política de conciliação em modo auditoria, nunca gate público.

Não acessa banco, não concede status ativo, não altera visibilidade.
'LEGADO_A_VALIDAR' não é autorização de publicação.
"""
from dataclasses import dataclass
from datetime import datetime
from typing import Optional, Tuple


@dataclass(frozen=True)
class CasoHistorico:
    status: str
    plano: Optional[str]
    data_pagamento: Optional[datetime]
    periodos: Tuple[Tuple[datetime, datetime], ...] = ()
    estorno_confirmado: bool = False
    disputa_financeira: bool = False
    direito_herdado_verificado: bool = False


def _aware(dt):
    return isinstance(dt, datetime) and dt.tzinfo is not None and dt.utcoffset() is not None


def avaliar(caso: CasoHistorico, agora: datetime) -> str:
    """Classifica para revisão interna; **nunca** use como filtro da busca."""
    if not _aware(agora):
        raise ValueError('agora precisa de fuso horário')
    status = str(caso.status or '').strip().lower()
    if status != 'ativo':
        return 'BLOQUEADO_STATUS'
    if caso.estorno_confirmado or caso.disputa_financeira:
        return 'REVISAO_FINANCEIRA_OBRIGATORIA'
    if caso.plano is not None and str(caso.plano).strip().lower() not in ('mensal', 'anual'):
        return 'REVISAO_PLANO'
    if caso.data_pagamento is not None and not _aware(caso.data_pagamento):
        return 'REVISAO_DATA_LEGADA'
    for inicio, fim in caso.periodos:
        if not _aware(inicio) or not _aware(fim) or fim <= inicio:
            return 'REVISAO_PERIODO_INVALIDO'
    if any(inicio <= agora < fim for inicio, fim in caso.periodos):
        return 'ED02_VIGENTE_AUDITORIA'
    if caso.periodos:
        return 'ED02_SEM_VIGENCIA_ATUAL'
    if caso.data_pagamento is None:
        return 'SEM_PROVA_FINANCEIRA'
    if caso.direito_herdado_verificado:
        return 'LEGADO_VALIDADO_AGUARDA_MIGRACAO'
    return 'LEGADO_A_VALIDAR'


# Todas as classificações são diagnósticas. Nenhuma delas constitui liberação pública.
PERMITE_PUBLICACAO_AUTOMATICA = frozenset()
