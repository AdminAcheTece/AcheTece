"""ED02.5 V5.9: comparador observacional ED01/ED02 (SEM publicacao).

Recebe snapshots OBSERVADOS; NUNCA aplica filtros de acesso nem modifica
Empresa, Tear, assinaturas ou quaisquer registros. Nenhum dado pessoal em logs.
"""
from __future__ import annotations
from collections import Counter
from datetime import datetime
from typing import Iterable
from ed02_5_v58_consolidacao import LinhaConsolidacao, comparar


ATIVA_RESULTADO_PUBLICO = False


def comparar_observacoes(linhas: Iterable[LinhaConsolidacao], agora: datetime) -> dict:
    linhas = tuple(linhas)
    if len({i.empresa_id for i in linhas}) != len(linhas):
        raise ValueError("empresa duplicada em snapshot; auditoria interrompida")
    pareceres = [comparar(linha, agora) for linha in linhas]
    if any(p.autoriza_publicar for p in pareceres):
        raise RuntimeError("violacao do contrato: observacao autorizou publicacao")
    return {
        "empresas_auditadas": len(pareceres),
        "ed01_visiveis_observadas": sum(p.ed01_visivel_observado for p in pareceres),
        "ed02_candidatos_diagnosticos": sum(p.ed02_candidato_diagnostico for p in pareceres),
        "classificacoes": dict(sorted(Counter(p.classificacao for p in pareceres).items())),
        "divergencias": dict(sorted(Counter(p.divergencia for p in pareceres).items())),
        "publicacoes_autorizadas": 0,
        "modifica_consultas_publicas": False,
    }
