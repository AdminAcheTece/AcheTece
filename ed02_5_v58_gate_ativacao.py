"""AcheTece V5.8: registro de prerequisitos de liberacao, SEM executar deploy.

Nenhum estado de gate habilita recursos nem altera ambiente. Requer avaliacao
humana e uma etapa futura, separada e explicitamente autorizada.
"""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping


PREREQUISITOS = (
    "historico_conciliado_e_auditado",
    "politica_estorno_disputa_aprovada",
    "comparacao_ed01_ed02_em_shadow_homologada",
    "consultas_sql_ed02_e_integridade_homologadas",
    "webhooks_idempotencia_e_reversoes_homologados",
    "replay_legado_nao_prorroga_vigencia_sem_novo_pagamento",
    "checkout_mensal_recorrente_e_anual_homologados",
    "renovacao_vencimento_e_avisos_homologados",
    "fluxo_usuario_completo_homologado",
    "backup_monitoramento_e_rollback_documentados",
    "autorizacao_explicita_de_release",
)
ATIVACAO_AUTOMATICA_PERMITIDA = False


@dataclass(frozen=True)
class GateConsolidado:
    prontidao_documental: bool
    pendencias: tuple[str, ...]
    pode_ativar_producao: bool = False


def verificar_gate(marcadores: Mapping[str, bool]) -> GateConsolidado:
    if not isinstance(marcadores, Mapping):
        raise TypeError("marcadores devem formar um mapeamento")
    desconhecidos = set(marcadores) - set(PREREQUISITOS)
    if desconhecidos:
        raise ValueError("prerequisitos desconhecidos")
    if any(type(v) is not bool for v in marcadores.values()):
        raise ValueError("marcadores devem ser booleanos exatos")
    pendencias = tuple(chave for chave in PREREQUISITOS if marcadores.get(chave) is not True)
    return GateConsolidado(prontidao_documental=not pendencias, pendencias=pendencias,
                           pode_ativar_producao=False)


def ler_gate_arquivo(caminho: str | Path) -> GateConsolidado:
    dados = json.loads(Path(caminho).read_text(encoding="utf-8"))
    if not isinstance(dados, dict) or dados.get("etapa") != "ED02.5 V5.8":
        raise ValueError("manifesto inesperado")
    return verificar_gate(dados.get("prerequisitos", {}))
