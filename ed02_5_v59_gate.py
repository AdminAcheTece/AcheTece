"""ED02.5 V5.9: gate nao operacional e bloqueado por padrao.

Mesmo um manifesto indicando requisitos preenchidos NAO autoriza producao.
"""
from __future__ import annotations
from dataclasses import dataclass
from typing import Mapping
from ed02_5_v58_gate_ativacao import PREREQUISITOS

ATIVACAO_ED02_PERMITIDA = False
BLOQUEADORES_V59 = (
    "idempotencia_do_legado_comprovada_sem_perder_renovacoes",
    "integracao_shadow_nas_rotas_reais_auditada",
    "periodos_ed02_vinculados_a_financeiro_conferido",
    "conciliação_historica_de_clientes_reais_aprovada",
    "reversoes_e_contestacoes_homologadas",
    "autorizacao_explicita_de_publicacao",
)


@dataclass(frozen=True)
class GateV59:
    pendencias: tuple[str, ...]
    modo: str = "BLOQUEADO"
    permite_producao: bool = False
    permite_visibilidade_ed02: bool = False


def avaliar_gate(marcadores: Mapping[str, bool] | None = None) -> GateV59:
    marcadores = {} if marcadores is None else marcadores
    if not isinstance(marcadores, Mapping):
        raise TypeError("marcadores devem ser um dicionario")
    permitidos = set(PREREQUISITOS) | set(BLOQUEADORES_V59)
    if set(marcadores) - permitidos:
        raise ValueError("requisito desconhecido")
    if any(type(x) is not bool for x in marcadores.values()):
        raise ValueError("valores devem ser booleanos")
    pendencias = tuple(k for k in (*PREREQUISITOS, *BLOQUEADORES_V59)
                       if marcadores.get(k) is not True)
    # Mesmo com todas as evidencias: gate documental nao e comando de deploy.
    return GateV59(pendencias=pendencias)
