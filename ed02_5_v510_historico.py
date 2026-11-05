"""ED02.5 V5.10: decisão DOCUMENTAL de conciliação, sem migração nem publicação.

Nunca cria período ED02 e não altera contratos ou visibilidade. A comprovação
financeira precisa vir de processo separado, com evidências reais e revisão.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Literal

Situacao = Literal["BLOQUEADA", "REVISAO_MANUAL", "CANDIDATA_A_CONCILIACAO"]


@dataclass(frozen=True)
class ParecerHistorico:
    situacao: Situacao
    permite_migrar: bool = False
    permite_publicar: bool = False
    motivo: str = ""


def avaliar_conciliacao(*, ed01_status: str | None, data_legada_presente: bool,
                         comprovante_verificado: bool, referencia_unica: bool,
                         estorno_ou_disputa: bool, conflito_de_periodos: bool) -> ParecerHistorico:
    """Parecer conservador. Até um candidato depende de auditoria externa."""
    if estorno_ou_disputa:
        return ParecerHistorico("BLOQUEADA", motivo="estorno_ou_disputa")
    if conflito_de_periodos:
        return ParecerHistorico("REVISAO_MANUAL", motivo="periodos_inconsistentes")
    if (ed01_status or "").strip().lower() != "ativo":
        return ParecerHistorico("BLOQUEADA", motivo="status_nao_ativo")
    if not data_legada_presente:
        return ParecerHistorico("REVISAO_MANUAL", motivo="ausencia_data_legada")
    if not comprovante_verificado or not referencia_unica:
        return ParecerHistorico("REVISAO_MANUAL", motivo="prova_financeira_insuficiente")
    return ParecerHistorico("CANDIDATA_A_CONCILIACAO", motivo="revisao_administrativa_pendente")
