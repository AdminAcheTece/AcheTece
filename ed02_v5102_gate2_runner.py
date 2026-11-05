"""ED02.5 V5.10.2 Gate 2: preflight + regressao em COPIA ISOLADA.

Esta ferramenta nao altera o main.py operacional. Nao faz deploy nem conexao real a
provedor, PostgreSQL ou SMTP. Requer candidatos integrais e hashes Gate 1.
"""
from __future__ import annotations

import argparse
import ast
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile

REPORT_NAME = "ED02_V5102_GATE2_RESULTADO.json"
# Testes offline: SEM os ensaios PostgreSQL reais que dependem de autorizacao.
# Cada modulo deve existir e concluir sem falha; sem skip automatico de modulos.
REGRESSION_MODULES = (
    "test_ed02_5_v510_replay_unit",
    "test_ed02_5_v510_shadow_historico",
    "test_ed02_5_v5101_integracao",
    "test_ed02_5_v5101_shadow",
    "test_ed02_5_v5101_pg_guard",
    "test_ed02_5_v59_replay",
    "test_ed02_5_v59_shadow",
    "test_ed02_5_v59_pg_safety",
    "test_ed02_5_v58_consolidacao",
    "test_ed02_5_v58_gate",
    "test_ed02_5_v58_pg_safety",
    "test_ed02_5_v510_pg_safety",
    "test_ed02_5_v56_elegibilidade",
    "test_ed02_5_v57_politica",
    "test_ed02_5_v572_http_visibilidade",
    "test_ed02_5_v55_http_concorrente",
    "test_ed02_3g6_http_integrado",
    "test_ed02_3g7c2_checkout_integrado",
)
# O teste historico do gerador V5.10.1 pressupoe main.py SEM o patch.
# Rodamos esse contrato sobre baseline reconstruida em outro clone isolado.
BASELINE_MODULES = ("test_ed02_5_v5101_integracao",)
CANDIDATE_MODULES = tuple(x for x in REGRESSION_MODULES if x not in BASELINE_MODULES)
ROUTES = ("index", "exportar", "utils_empresas_json", "empresa_perfil")
GUARDED_FLAGS = (
    "ED02_V510_REPLAY_HOMOLOG",
    "ED02_V510_HTTP_SHADOW_HOMOLOG",
    "ED02_EXACT_ONCE_HOMOLOG",
    "ED02_FINANCIAL_GUARD_HOMOLOG",
    "ED02_VIGENCIA_WRITE_HOMOLOG",
    "ED02_SHADOW_LEDGER",
)
SENSITIVE = (
    "DATABASE_URL", "ED02_TEST_DATABASE_URL", "MERCADOPAGO_ACCESS_TOKEN",
    "MP_ACCESS_TOKEN", "MERCADO_PAGO_ACCESS_TOKEN", "RESEND_API_KEY",
)


def digest(p: Path) -> str:
    if not p.is_file() or p.is_symlink():
        raise RuntimeError(f"BLOQUEADO: arquivo ausente ou link simbólico: {p.name}")
    return hashlib.sha256(p.read_bytes()).hexdigest()


def assert_safe_environment() -> None:
    branch = os.getenv("GITHUB_REF_NAME", "")
    if branch and branch != "ed02-v5102-gate2-auditoria":
        raise RuntimeError("BLOQUEADO: Gate 2 somente na branch isolada de auditoria")
    for key in GUARDED_FLAGS:
        if os.getenv(key, "").strip().lower() in ("1", "true", "yes", "sim", "on"):
            raise RuntimeError(f"BLOQUEADO: flag ativa {key}")
    for key in SENSITIVE:
        if os.getenv(key):
            raise RuntimeError(f"BLOQUEADO: credencial {key} presente no processo Gate 2")


def function_tree(source: str, name: str) -> str:
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise RuntimeError(f"BLOQUEADO: rota ausente ou duplicada: {name}")
    return ast.dump(nodes[0], include_attributes=False)


def checks(root: Path) -> dict:
    assert_safe_environment()
    report_file = root / "ED02_V5102_RELATORIO.json"
    if not report_file.is_file() or report_file.is_symlink():
        raise RuntimeError("BLOQUEADO: manifesto Gate 1 ausente")
    record = json.loads(report_file.read_text(encoding="utf-8"))
    required = {
        "main.py": None,
        "ed02_5_v510_http_shadow.py": "shadow_v5101_sha256",
        "main_V5101_BASE.py": "main_v5101_sha256",
        "main_V5102_CANDIDATO.py": "main_v5102_sha256",
        "ed02_5_v510_http_shadow_V5102_CANDIDATO.py": "shadow_v5102_sha256",
    }
    for name, key in required.items():
        p = root / name
        h = digest(p)
        if key and h != record.get(key):
            raise RuntimeError(f"BLOQUEADO: hash incompatível: {name}")
    original_main_hash = digest(root / "main.py")
    if original_main_hash not in (record.get("head_main_esperado_sha256"),
                                  record.get("main_v5101_sha256")):
        raise RuntimeError("BLOQUEADO: main.py mudou desde auditoria Gate 1")
    original_shadow_hash = digest(root / "ed02_5_v510_http_shadow.py")
    if original_shadow_hash != record.get("shadow_v5101_sha256"):
        raise RuntimeError("BLOQUEADO: shadow operacional alterado desde auditoria Gate 1")
    if record.get("aplicado_ao_main") is not False or record.get("producao_autorizada") is not False:
        raise RuntimeError("BLOQUEADO: manifesto não declara aplicação e produção proibidas")
    if any(record.get("flags", {}).values()):
        raise RuntimeError("BLOQUEADO: manifesto possui flag ativa")
    actual = (root / "main.py").read_text(encoding="utf-8")
    candidate = (root / "main_V5102_CANDIDATO.py").read_text(encoding="utf-8")
    for route in ROUTES:
        if function_tree(actual, route) != function_tree(candidate, route):
            raise RuntimeError(f"BLOQUEADO: lógica ED01 alterada na rota {route}")
    compile(candidate, "main_V5102_CANDIDATO.py", "exec")
    shadow = (root / "ed02_5_v510_http_shadow_V5102_CANDIDATO.py").read_text(encoding="utf-8")
    compile(shadow, "ed02_5_v510_http_shadow_V5102_CANDIDATO.py", "exec")
    if "def contar_divergencias_reais" not in shadow:
        raise RuntimeError("BLOQUEADO: correção shadow ausente")
    if "_env_bool(\"ED02_V510_REPLAY_HOMOLOG\", False)" not in candidate:
        raise RuntimeError("BLOQUEADO: flag homologação replay ausente")
    if not (root / "aplicar_ed02_v510_main.py").is_file():
        raise RuntimeError("BLOQUEADO: gerador historico de baseline ausente")
    for dependency in ("ed02_5_v56_elegibilidade.py", "ed02_5_v57_politica.py",
                       "ED02_5_V58_GATE_ATIVACAO.json"):
        if not (root / dependency).is_file():
            raise RuntimeError(f"BLOQUEADO: dependencia da regressao ausente: {dependency}")
    for module in REGRESSION_MODULES:
        if not (root / f"{module}.py").is_file():
            raise RuntimeError(f"BLOQUEADO: módulo de regressão obrigatório ausente: {module}")
    return {
        "main_sha256_anterior": original_main_hash,
        "shadow_sha256_anterior": original_shadow_hash,
        "main_sha256_candidato": digest(root / "main_V5102_CANDIDATO.py"),
        "shadow_sha256_candidato": digest(root / "ed02_5_v510_http_shadow_V5102_CANDIDATO.py"),
        "rotas_ed01_inalteradas": list(ROUTES),
        "modulos_regressao_exigidos": list(REGRESSION_MODULES),
    }


def prepare_snapshot(root: Path, workspace: Path) -> dict:
    ignored = shutil.ignore_patterns(
        ".git", ".github", ".env", ".env.*", ".venv", "venv",
        "__pycache__", ".pytest_cache", "*.db", "*.sqlite", "*.sqlite3",
        "ed02_gate2_outputs", "ED02_GATE2_OUTPUTS",
    )
    shutil.copytree(root, workspace, ignore=ignored, symlinks=False)
    old_main_hash = digest(workspace / "main.py")
    old_shadow_hash = digest(workspace / "ed02_5_v510_http_shadow.py")
    # Substituição APENAS no diretório temporário, nunca no checkout original.
    shutil.copyfile(workspace / "main_V5102_CANDIDATO.py", workspace / "main.py")
    shutil.copyfile(workspace / "ed02_5_v510_http_shadow_V5102_CANDIDATO.py",
                    workspace / "ed02_5_v510_http_shadow.py")
    return {"main_antes": old_main_hash, "shadow_antes": old_shadow_hash,
            "main_depois": digest(workspace / "main.py"),
            "shadow_depois": digest(workspace / "ed02_5_v510_http_shadow.py")}


def prepare_generator_baseline(root: Path, workspace: Path, expected_hash: str) -> str:
    """Recria main anterior a V5101 apenas para seus testes historicos de geracao."""
    ignored = shutil.ignore_patterns(
        ".git", ".github", ".env", ".env.*", ".venv", "venv",
        "__pycache__", ".pytest_cache", "*.db", "*.sqlite", "*.sqlite3",
        "ed02_gate2_outputs", "ED02_GATE2_OUTPUTS",
    )
    shutil.copytree(root, workspace, ignore=ignored, symlinks=False)
    from aplicar_ed02_v510_main import REPLAY, INSERT_SHADOW
    source = (root / "main_V5101_BASE.py").read_text(encoding="utf-8")
    if source.count(REPLAY) != 1 or source.count(INSERT_SHADOW) != 1:
        raise RuntimeError("BLOQUEADO: referencia V5101 nao permite reconstruir baseline")
    baseline = source.replace(REPLAY, "", 1).replace(INSERT_SHADOW, "", 1)
    h = hashlib.sha256(baseline.encode("utf-8")).hexdigest()
    if h != expected_hash:
        raise RuntimeError("BLOQUEADO: baseline reconstruida diverge do SHA esperado")
    (workspace / "main.py").write_text(baseline, encoding="utf-8")
    return h


def clean_environment() -> dict:
    # Preserva apenas variáveis essenciais para o Python local; nenhuma credencial.
    keys = ("PATH", "HOME", "LANG", "LC_ALL", "SYSTEMROOT", "WINDIR", "TMPDIR")
    env = {k: v for k in keys if (v := os.environ.get(k))}
    env.update({"PYTHONDONTWRITEBYTECODE": "1", "PYTHONUNBUFFERED": "1",
                "ED01_HOMOLOGACAO": "false", "DEMO_MODE": "sim",
                "ALLOW_SQLITE_FALLBACK": "sim"})
    for key in GUARDED_FLAGS:
        env[key] = "false"
    return env


def run_suite(workspace: Path, modules: tuple[str, ...]) -> tuple[bool, str, int]:
    cmd = [sys.executable, "-B", "-m", "unittest", "-v", *modules]
    done = subprocess.run(cmd, cwd=str(workspace), env=clean_environment(),
                          stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                          text=True, timeout=240, check=False)
    return (done.returncode == 0), done.stdout, done.returncode


def write_report(out: Path, data: dict) -> None:
    out.mkdir(parents=True, exist_ok=True)
    (out / REPORT_NAME).write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True), encoding="utf-8")


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("mode", choices=("preflight", "regression"))
    ap.add_argument("--root", type=Path, default=Path(__file__).resolve().parent)
    ap.add_argument("--out", type=Path, default=None)
    args = ap.parse_args()
    root = args.root.resolve(strict=True)
    out = args.out.resolve() if args.out else None
    if out and (out == root or root in out.parents):
        raise RuntimeError("BLOQUEADO: saída do relatório não pode ficar dentro do repositório")
    data = {"etapa": "ED02.5 V5.10.2 Gate 2",
            "timestamp_utc": datetime.now(timezone.utc).isoformat(),
            "modo": args.mode, "aplicado_ao_repositorio": False,
            "render_alterado": False, "producao_autorizada": False,
            "postgresql_real_executado": False,
            "pagamentos_reais_executados": False,
            "status": "INICIADO"}
    try:
        data.update(checks(root))
        print("PASSOU: integridade, HEAD, flags e quatro rotas originais ED01")
        if args.mode == "regression":
            with tempfile.TemporaryDirectory(prefix="ed02_v5102_gate2_") as tmp:
                old_dir = Path(tmp) / "baseline_legada_isolada"
                baseline_hash = prepare_generator_baseline(root, old_dir,
                    json.loads((root / "ED02_V5102_RELATORIO.json").read_text(encoding="utf-8"))["head_main_esperado_sha256"])
                data["baseline_generator_sha256"] = baseline_hash
                baseline_ok, baseline_log, baseline_code = run_suite(old_dir, BASELINE_MODULES)
                data["regressao_baseline"] = "PASS" if baseline_ok else "FAIL"
                if out:
                    out.mkdir(parents=True, exist_ok=True)
                    (out / "ED02_V5102_GATE2_BASELINE_V5101.log").write_text(baseline_log, encoding="utf-8")
                print("BASELINE V5101: ", "PASSOU" if baseline_ok else "FALHOU")
                if not baseline_ok:
                    print("\n".join(baseline_log.splitlines()[-18:]))
                    raise RuntimeError("BLOQUEADO: regressão historica do gerador V5101 falhou")
                workspace = Path(tmp) / "repositorio_candidato_isolado"
                status = prepare_snapshot(root, workspace)
                data["copia_isolada"] = status
                assert digest(root / "main.py") == data["main_sha256_anterior"]
                assert digest(root / "ed02_5_v510_http_shadow.py") == data["shadow_sha256_anterior"]
                ok, log, code = run_suite(workspace, CANDIDATE_MODULES)
                data["retorno_regressao"] = code
                data["regressao_candidato"] = "PASS" if ok else "FAIL"
                if out:
                    (out / "ED02_V5102_GATE2_REGRESSAO_CANDIDATO.log").write_text(log, encoding="utf-8")
                print("\n".join(log.splitlines()[-24:]))
                if not ok:
                    raise RuntimeError("BLOQUEADO: regressão da cópia candidata falhou")
                print("PASSOU: regressao da copia isolada; main.py original inalterado")
        data["status"] = "PASS"
    except Exception as exc:
        data["status"] = "FAIL"
        data["erro_tipo"] = type(exc).__name__
        data["motivo_generico"] = str(exc) if isinstance(exc, RuntimeError) else "erro inesperado na validacao"
        print(f"GATE 2 BLOQUEADO: {data['motivo_generico']}", file=sys.stderr)
    finally:
        if out:
            write_report(out, data)
    return 0 if data["status"] == "PASS" else 1


if __name__ == "__main__":
    sys.exit(main())
