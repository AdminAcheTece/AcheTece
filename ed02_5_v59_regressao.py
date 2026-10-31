"""ED02.5 V5.9: auditoria ESTATICA do reprocessamento legado.

Nao executa Flask, rede ou banco. Nao altera o fluxo de pagamentos.
Um achado nesta auditoria BLOQUEIA o gate; 'teste passou' nao equivale a
'problema corrigido'. A correcao em producao exige deduplicacao persistente,
identidade do pagamento e regra explicita para renovacoes diferentes.
"""
from __future__ import annotations
import ast
from pathlib import Path


def diagnosticar_replay_legado(main_py: str | Path) -> dict:
    caminho = Path(main_py)
    tree = ast.parse(caminho.read_text(encoding="utf-8"), filename=str(caminho))
    matches = [n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
               and n.name == "_processar_pagamento"]
    if len(matches) != 1:
        raise RuntimeError("Processador de pagamento ausente ou duplicado; revisao obrigatoria")
    func = matches[0]
    approved = [n for n in ast.walk(func) if isinstance(n, ast.If)
                and isinstance(n.test, ast.Compare)
                and isinstance(n.test.left, ast.Name) and n.test.left.id == "status"
                and len(n.test.ops) == 1 and isinstance(n.test.ops[0], ast.Eq)
                and len(n.test.comparators) == 1
                and isinstance(n.test.comparators[0], ast.Constant)
                and n.test.comparators[0].value == "approved"]
    if len(approved) != 1:
        raise RuntimeError("Contrato do branch approved mudou; revisar manualmente")
    writes = []
    for node in ast.walk(approved[0]):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                if isinstance(t, ast.Attribute) and isinstance(t.value, ast.Name) \
                        and t.value.id == "empresa" and t.attr == "data_pagamento":
                    writes.append(node.lineno)
    return {
        "funcao_encontrada": True,
        "linhas_update_data_pagamento_no_approved": tuple(sorted(writes)),
        # Nao inferimos segurança de um unico trecho AST: a correção deve passar
        # testes de replay E de renovação, com IDs de pagamento diferentes.
        "replay_legado_provado_seguro": False,
        "liberacao_permitida": False,
        "necessita_revisao": bool(writes),
    }
