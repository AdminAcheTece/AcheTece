"""AcheTece ED02.5 V5.10.1 — gerador FAIL-CLOSED do main.py completo.

Não altera main.py. Gera um arquivo candidato com base no main.py PRESENTE
no checkout atual (HEAD) e relatório de SHA256/diff revisável.
Nunca executa pagamento nem conecta banco.
"""
from __future__ import annotations
import argparse
import ast
import difflib
import hashlib
import json
from pathlib import Path
from aplicar_ed02_v510_main import construir as construir_v510, _verify_tree, REPLAY, INSERT_SHADOW

ALVOS = {"index", "exportar", "utils_empresas_json", "empresa_perfil"}
MARCADORES = ("ED02_V510_REPLAY_HOMOLOG", "ED02_V510_HTTP_SHADOW_HOMOLOG")


def digest(s: str) -> str:
    return hashlib.sha256(s.encode("utf-8")).hexdigest()


def _validar_estrutura(src: str):
    ast.parse(src)
    _verify_tree(src)
    tree = ast.parse(src)
    functions = {n.name for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    if not ALVOS.issubset(functions):
        raise RuntimeError("V5101 BLOQUEADO: rotas originais alteradas ou ausentes")
    # Garantia ED01: identificar proteção nas funções originais.
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in ALVOS:
            code = ast.get_source_segment(src, node) or ""
            if "Empresa.assinatura_ativa" not in code:
                raise RuntimeError(f"V5101 BLOQUEADO: filtro ED01 ausente em {node.name}")
    return tree


def _integrado(src: str) -> bool:
    has = [x in src for x in MARCADORES]
    if any(has) and not all(has):
        raise RuntimeError("V5101 BLOQUEADO: patch V510 parcial detectado")
    return all(has)


def gerar_candidato(original: str) -> tuple[str, str]:
    """Gera main completo e modo, sem escrever main original."""
    _validar_estrutura(original)
    if _integrado(original):
        candidate = original
        modo = "ja_integrado"
    else:
        candidate = construir_v510(original)
        modo = "inserido_sem_alterar_main"
        # Exige somente as duas inserções conhecidas; nenhuma outra edição.
        if candidate.count(REPLAY) != 1 or candidate.count(INSERT_SHADOW) != 1:
            raise RuntimeError("V5101 BLOQUEADO: insercoes inesperadas")
        if candidate.replace(REPLAY, "", 1).replace(INSERT_SHADOW, "", 1) != original:
            raise RuntimeError("V5101 BLOQUEADO: alteracao fora dos dois pontos autorizados")
    _validar_estrutura(candidate)
    f = next(n for n in ast.parse(candidate).body if isinstance(n, ast.FunctionDef) and n.name == "_processar_pagamento")
    fc = ast.get_source_segment(candidate, f) or ""
    for marker in ("ED02_V510_REPLAY_HOMOLOG", "processar_legado_uma_vez_homolog", "ED02_EXACT_ONCE_HOMOLOG"):
        if marker not in fc:
            raise RuntimeError("V5101 BLOQUEADO: ramo financeiro incompleto")
    if "instalar_shadow_http(app, db)" not in candidate:
        raise RuntimeError("V5101 BLOQUEADO: observacao HTTP ausente")
    if "_env_bool(\"ED02_V510_REPLAY_HOMOLOG\", False)" not in candidate or \
       "_env_bool(\"ED02_V510_HTTP_SHADOW_HOMOLOG\", False)" not in candidate:
        raise RuntimeError("V5101 BLOQUEADO: flags precisam estar desligadas por padrao")
    return candidate, modo


def salvar(src: Path, dest: Path, relatorio: Path, diff_path: Path):
    if src.name != "main.py" or src.is_symlink():
        raise RuntimeError("V5101 BLOQUEADO: origem deve ser main.py regular")
    if not src.exists() or src.resolve() == dest.resolve() or dest.name == "main.py" or dest.is_symlink():
        raise RuntimeError("V5101 BLOQUEADO: nao sobrescrever main.py")
    original = src.read_text(encoding="utf-8")
    candidate, modo = gerar_candidato(original)
    for d in (dest, relatorio, diff_path):
        if d.resolve() == src.resolve():
            raise RuntimeError("V5101 BLOQUEADO: nenhum artefato pode substituir a origem")
    patch = "".join(difflib.unified_diff(
        original.splitlines(keepends=True), candidate.splitlines(keepends=True),
        fromfile="a/main.py", tofile="b/main.py"))
    rel = {
        "etapa": "ED02.5 V5.10.1", "modo": modo, "sha256_origem": digest(original),
        "sha256_candidato": digest(candidate), "main_original_inalterado": True,
        "mudancas": "dois pontos de insercao auditados" if patch else "ja existente no HEAD",
        "flags_defaults": {"ED02_V510_REPLAY_HOMOLOG": False,
                           "ED02_V510_HTTP_SHADOW_HOMOLOG": False},
        "rotas_publicas_ed01": sorted(ALVOS),
        "implantacao_automatica": False, "aprovacao_producao": False,
        "observacoes": "Revisar diff e executar homologacao real antes de substituir main.py",
    }
    dest.write_text(candidate, encoding="utf-8")
    relatorio.write_text(json.dumps(rel, indent=2, ensure_ascii=False)+"\n", encoding="utf-8")
    diff_path.write_text(patch or "# Nenhuma diferenca: V510 ja presente no HEAD.\n", encoding="utf-8")
    if src.read_text(encoding="utf-8") != original:
        raise RuntimeError("V5101 BLOQUEADO: main original alterado durante a operacao")
    print("PASSOU: candidato completo gerado do HEAD; main.py original preservado")
    print("MODO:", modo, "SHA256 ORIGEM:", rel["sha256_origem"])
    print("GATE: BLOQUEADO; flags OFF; nenhum deploy, nenhum pagamento")
    return rel


def main():
    a = argparse.ArgumentParser()
    a.add_argument("--source", default="main.py")
    a.add_argument("--output", default="main_V5101_CANDIDATO.py")
    a.add_argument("--report", default="ED02_V5101_RELATORIO.json")
    a.add_argument("--diff", default="ED02_V5101_MAIN.diff")
    p=a.parse_args()
    salvar(Path(p.source), Path(p.output), Path(p.report), Path(p.diff))

if __name__ == "__main__": main()
