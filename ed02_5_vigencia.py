"""ED02.5 - motor puro de vigencia; sem banco, Mercado Pago ou efeitos colaterais.

Politica PROPOSTA para homologacao. Nao integrar a ativacao em producao
sem aprovar explicitamente renovacao antecipada, timezone e estornos.
"""
from __future__ import annotations
from calendar import monthrange
from datetime import datetime, timezone
from dataclasses import dataclass

PLANOS_MESES = {'mensal': 1, 'anual': 12}


def _utc(value: datetime) -> datetime:
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError('datetime UTC com timezone obrigatorio')
    return value.astimezone(timezone.utc)


def add_months_clamped(value: datetime, months: int) -> datetime:
    """Soma meses preservando horario e limitando dia ao ultimo do mes.

    Ex.: 31/jan + 1 mes = 28/fev (ou 29 em ano bissexto).
    """
    value = _utc(value)
    if not isinstance(months, int) or isinstance(months, bool) or months < 1:
        raise ValueError('meses invalidos')
    index = value.year * 12 + value.month - 1 + months
    year, month0 = divmod(index, 12)
    if not (1 <= year <= 9999):
        raise ValueError('data fora do intervalo')
    month = month0 + 1
    return value.replace(year=year, month=month, day=min(value.day, monthrange(year, month)[1]))


@dataclass(frozen=True)
class VigenciaCalculada:
    inicio: datetime
    fim_exclusivo: datetime
    renovacao_antecipada: bool


def calcular_vigencia(plano: str, aprovado_em: datetime,
                       fim_vigente: datetime | None = None) -> VigenciaCalculada:
    """PROPOSTA: renovar antes do vencimento soma período ao fim atual.

    Intervalos semiabertos [inicio, fim); uma aprovação não pode ser aplicada
    duas vezes: idempotencia e prova de contrato continuam responsabilidades
    do ledger transacional, NUNCA desta funcao.
    """
    if plano not in PLANOS_MESES:
        raise ValueError('plano invalido')
    aprovado = _utc(aprovado_em)
    fim_atual = _utc(fim_vigente) if fim_vigente is not None else None
    antecipada = fim_atual is not None and fim_atual > aprovado
    inicio = fim_atual if antecipada else aprovado
    fim = add_months_clamped(inicio, PLANOS_MESES[plano])
    return VigenciaCalculada(inicio, fim, antecipada)


def esta_vigente(inicio: datetime, fim_exclusivo: datetime, agora: datetime) -> bool:
    inicio, fim, agora = _utc(inicio), _utc(fim_exclusivo), _utc(agora)
    if fim <= inicio:
        raise ValueError('intervalo invalido')
    return inicio <= agora < fim
