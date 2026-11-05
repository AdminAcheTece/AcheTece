"""Gerador conservador do main.py INTEGRAL V5.10 a partir do main.py atual.

Por padrão escreve somente main_V510_CANDIDATO.py, não substitui main.py.
FALHA se os âncoras não coincidirem: nunca faz substituição parcial.
Use apenas sobre a BRANCH ED02 e revise git diff antes de aplicar.
"""
from __future__ import annotations
import argparse
import ast
import hashlib
from pathlib import Path

REPLAY = '''    # ED02.5 V5.10: replay com ledger atômico SOMENTE na homologacao.
    # Flag OFF por padrão; não habilitar sem gate financeiro e banco autorizado.
    if _env_bool("ED02_V510_REPLAY_HOMOLOG", False):
        if _env_bool("ED02_EXACT_ONCE_HOMOLOG", False) or _env_bool("ED02_SHADOW_LEDGER", False):
            raise RuntimeError("V510: modos de pagamento mutuamente exclusivos")
        from ed02_5_v510_legacy_once import processar_legado_uma_vez_homolog
        result = processar_legado_uma_vez_homolog(db, payment, empresa.id)
        if result["primeira_ativacao"]:
            try:
                link = _make_magic_link(empresa.id)
                html = _email_ativacao_html(empresa, link)
                _send_email(empresa.email, "Pagamento aprovado - AcheTece", html)
            except Exception:
                app.logger.error("[ED02 V510] notificacao nao enviada; detalhes omitidos")
        return {"ok": True, "empresa_id": empresa.id,
                "ativou": result["aplicado"], "duplicado": result["duplicado"],
                "revisao_historica": result["revisao_historica"],
                "modo": "v510_homologacao", "status": result["status"]}

'''
INSERT_SHADOW = '''# ED02.5 V5.10: diagnostico HTTP em HOMOLOGACAO, nao afeta respostas.
# A flag nao deve ser ligada em producao.
if _env_bool("ED02_V510_HTTP_SHADOW_HOMOLOG", False):
    from ed02_5_v510_http_shadow import instalar_shadow_http
    instalar_shadow_http(app, db)

'''
ANCHOR_REPLAY = '    # ED02.3E: exclusivamente homologacao, flag DESLIGADA por padrao.\n'
ANCHOR_SHADOW = '# --------------------------------------------------------------------\n# Entry point local\n# --------------------------------------------------------------------\n'


def _verify_tree(source):
    tree = ast.parse(source)
    found = [n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == "_processar_pagamento"]
    if len(found) != 1 or not any(isinstance(n, ast.FunctionDef) and n.name == "empresa_perfil" for n in tree.body):
        raise RuntimeError("V510: funcoes de pagamento/perfil divergentes — sem patch")
    f = found[0]
    code = ast.get_source_segment(source, f) or ""
    if 'empresa.data_pagamento = datetime.utcnow()' not in code or 'process_verified_payment_exact_once' not in code:
        raise RuntimeError("V510: contrato financeiro mudou — revisao manual obrigatoria")
    return tree


def construir(source):
    _verify_tree(source)
    if any(marker in source for marker in ("ED02_V510_REPLAY_HOMOLOG", "ED02_V510_HTTP_SHADOW_HOMOLOG")):
        raise RuntimeError("V510 aparentemente ja instalada; sem duplicar")
    if source.count(ANCHOR_REPLAY) != 1 or source.count(ANCHOR_SHADOW) != 1:
        raise RuntimeError("V510: ancoras alteradas ou duplicadas; sem patch")
    result = source.replace(ANCHOR_REPLAY, REPLAY + ANCHOR_REPLAY, 1)
    result = result.replace(ANCHOR_SHADOW, INSERT_SHADOW + ANCHOR_SHADOW, 1)
    _verify_tree(result)
    return result


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="main.py")
    ap.add_argument("--output", default="main_V510_CANDIDATO.py")
    ap.add_argument("--apply", action="store_true", help="gravar main.py, somente com confirmação de revisão externa")
    opts = ap.parse_args()
    source_path = Path(opts.source)
    original = source_path.read_text(encoding="utf-8")
    candidate = construir(original)
    h = hashlib.sha256(original.encode("utf-8")).hexdigest()
    print("SHA256 main.py origem:", h)
    if opts.apply:
        if source_path.name != "main.py":
            raise RuntimeError("--apply aceita somente arquivo main.py")
        source_path.write_text(candidate, encoding="utf-8")
        print("main.py atualizado; REVISE git diff e NAO faça merge para main")
    else:
        path = Path(opts.output)
        if path.name == "main.py" or path.resolve() == source_path.resolve():
            raise RuntimeError("sem sobrescrita em modo candidato")
        path.write_text(candidate, encoding="utf-8")
        print("Candidato completo:", path)
    print("V510 patch aplicado logicamente: flag de replay e hook shadow. Ambas OFF por padrao")


if __name__ == "__main__":
    main()
