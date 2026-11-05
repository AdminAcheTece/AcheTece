"""AcheTece ED02.5 V5.10.3: gate de PR isolada. SEM deploy, banco ou pagamentos.

Faz auditoria git da branch candidata e regressao offline somente em copias temporarias.
Requer branch de origem ED02 imutavel no momento de executar. Nao realiza commits.
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

CANDIDATE_BRANCH = 'ed02-v5103-integracao-candidata'
BASE_BRANCH = 'ed02-assinaturas-vigencia-ledger'
BASE_REF = f'refs/remotes/origin/{BASE_BRANCH}'
ORIGINAL_MAIN_HASHES = frozenset({
    # main.py sem V5101, preservado na branch operacional no Gate 1
    '9b90b67b6195bd63d28c7f1c3e8011240b5ab366c680e8d1acd27a99f6157d84',
    # main.py V5101, possivel variante verificada pelo Gate 2
    '2e262117443ceef4e44e35d964675b2bb4b2f435108c9e449ea5b1cbb65b4ae6',
})
ORIGINAL_SHADOW_HASH = '95d2f0ff4e4959ca5e4ca18a95ad189e3944802bd313a4f3eb6b7124727ca5cd'
TARGET_MAIN_HASH = '7e5725b80b0f5c50f3b78b4a6f8ca930c74de0b4d97949ebf1b1794f79275200'
TARGET_SHADOW_HASH = '45fe37566c9efedcd109eda56499e5ef0c48b978ef3a596003dcc30ec5973e44'
FOUR_ROUTES = ('index', 'exportar', 'utils_empresas_json', 'empresa_perfil')
FLAGS = (
    'ED02_V510_REPLAY_HOMOLOG', 'ED02_V510_HTTP_SHADOW_HOMOLOG',
    'ED02_EXACT_ONCE_HOMOLOG', 'ED02_FINANCIAL_GUARD_HOMOLOG',
    'ED02_VIGENCIA_WRITE_HOMOLOG', 'ED02_SHADOW_LEDGER',
)
SENSITIVE_ENV = ('DATABASE_URL', 'ED02_TEST_DATABASE_URL',
                 'MERCADOPAGO_ACCESS_TOKEN', 'MP_ACCESS_TOKEN',
                 'MERCADO_PAGO_ACCESS_TOKEN', 'RESEND_API_KEY')
ALLOWED_CHANGES = frozenset({
    'main.py', 'ed02_5_v510_http_shadow.py',
    'ed02_v5103_gate.py', 'test_ed02_v5103_gate.py',
    'ED02_V5103_MANIFESTO.json', 'LEIA_ME_ED02_V5103.md',
    '.github/workflows/ed02-v5103-revisao-pr.yml',
})
# Mesma regressao de candidato aprovada no Gate 2, mais a integracao V5102.
# test_ed02_5_v5101_integracao exige main.py ANTIGO, por isso tem gate separado
# no workflow legado V5101, nao pertence a regressao pos-integracao.
REGRESSION_MODULES = (
    'test_ed02_5_v510_replay_unit',
    'test_ed02_5_v510_shadow_historico',
    'test_ed02_5_v5101_shadow',
    'test_ed02_5_v5101_pg_guard',
    'test_ed02_5_v5102_integracao',
    'test_ed02_5_v59_replay',
    'test_ed02_5_v59_shadow',
    'test_ed02_5_v59_pg_safety',
    'test_ed02_5_v58_consolidacao',
    'test_ed02_5_v58_gate',
    'test_ed02_5_v58_pg_safety',
    'test_ed02_5_v510_pg_safety',
    'test_ed02_5_v56_elegibilidade',
    'test_ed02_5_v57_politica',
    'test_ed02_5_v572_http_visibilidade',
    'test_ed02_5_v55_http_concorrente',
    'test_ed02_3g6_http_integrado',
    'test_ed02_3g7c2_checkout_integrado',
)


def sha_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha_file(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError(f'BLOQUEADO: arquivo ausente ou symlink: {path.name}')
    return sha_bytes(path.read_bytes())


def run_git(root: Path, *args: str) -> bytes:
    done = subprocess.run(['git', '-C', str(root), *args],
                          stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                          check=False, timeout=20)
    if done.returncode:
        raise RuntimeError('BLOQUEADO: verificacao git sem sucesso (' + args[0] + ')')
    return done.stdout


def truthy(value: str | None) -> bool:
    return (value or '').strip().lower() in ('true', '1', 'on', 'yes', 'sim')


def assert_safe_environment() -> None:
    branch = os.getenv('GITHUB_REF_NAME') or ''
    if branch and branch != CANDIDATE_BRANCH:
        raise RuntimeError('BLOQUEADO: somente branch de integracao candidata')
    for flag in FLAGS:
        if truthy(os.getenv(flag)):
            raise RuntimeError(f'BLOQUEADO: flag financeira ativa: {flag}')
    for secret in SENSITIVE_ENV:
        if os.getenv(secret):
            raise RuntimeError(f'BLOQUEADO: credencial inesperada no processo: {secret}')


def route_ast(source: str, name: str) -> str:
    nodes = [n for n in ast.parse(source).body
             if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef)) and n.name == name]
    if len(nodes) != 1:
        raise RuntimeError('BLOQUEADO: rota ausente ou duplicada: ' + name)
    return ast.dump(nodes[0], include_attributes=False)


def preflight(root: Path) -> dict:
    assert_safe_environment()
    manifest_path = root / 'ED02_V5103_MANIFESTO.json'
    if not manifest_path.is_file():
        raise RuntimeError('BLOQUEADO: manifesto de release ausente')
    m = json.loads(manifest_path.read_text(encoding='utf-8'))
    if m.get('producao_autorizada') is not False or m.get('render_alterado') is not False:
        raise RuntimeError('BLOQUEADO: manifesto nao garante isolamento')
    if m.get('origem') != BASE_BRANCH or m.get('destino') != CANDIDATE_BRANCH:
        raise RuntimeError('BLOQUEADO: branch de origem/destino invalida')
    for name, expected in (('main.py', TARGET_MAIN_HASH),
                           ('ed02_5_v510_http_shadow.py', TARGET_SHADOW_HASH)):
        if sha_file(root / name) != expected or m['sha256'][name] != expected:
            raise RuntimeError('BLOQUEADO: candidato diferente do Gate 2: ' + name)
    run_git(root, 'rev-parse', '--verify', BASE_REF)
    if not subprocess.run(['git','-C',str(root),'merge-base','--is-ancestor',BASE_REF,'HEAD'],
                          stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          check=False, timeout=20).returncode == 0:
        raise RuntimeError('BLOQUEADO: branch ED02 mudou apos criar a candidata; refazer revisao')
    before_main = run_git(root, 'show', BASE_REF + ':main.py')
    before_shadow = run_git(root, 'show', BASE_REF + ':ed02_5_v510_http_shadow.py')
    if sha_bytes(before_main) not in ORIGINAL_MAIN_HASHES:
        raise RuntimeError('BLOQUEADO: main.py da ED02 diferente dos hashes auditados')
    if sha_bytes(before_shadow) != ORIGINAL_SHADOW_HASH:
        raise RuntimeError('BLOQUEADO: shadow da ED02 diferente do hash auditado')
    changed = set(run_git(root, 'diff', '--name-only', BASE_REF + '...HEAD').decode().splitlines())
    if not {'main.py', 'ed02_5_v510_http_shadow.py'}.issubset(changed):
        raise RuntimeError('BLOQUEADO: dois arquivos operacionais nao incluidos')
    unexpected = changed - ALLOWED_CHANGES
    if unexpected:
        raise RuntimeError('BLOQUEADO: mudancas fora do escopo: ' + ','.join(sorted(unexpected)))
    old = before_main.decode('utf-8')
    new = (root / 'main.py').read_text(encoding='utf-8')
    for name in FOUR_ROUTES:
        if route_ast(old, name) != route_ast(new, name):
            raise RuntimeError('BLOQUEADO: rota ED01 modificada: ' + name)
    compile(new, 'main.py', 'exec')
    compile((root / 'ed02_5_v510_http_shadow.py').read_text(encoding='utf-8'),
            'ed02_5_v510_http_shadow.py','exec')
    if new.count('def _processar_pagamento(payment_id: str):') != 1:
        raise RuntimeError('BLOQUEADO: processador financeiro nao e unico')
    shadow = (root / 'ed02_5_v510_http_shadow.py').read_text(encoding='utf-8')
    if 'def contar_divergencias_reais' not in shadow:
        raise RuntimeError('BLOQUEADO: ajuste shadow ausente')
    for module in REGRESSION_MODULES:
        if not (root / (module + '.py')).is_file():
            raise RuntimeError('BLOQUEADO: teste obrigatorio ausente: ' + module)
    return {'etapa':'ED02.5 V5.10.3',
            'timestamp_utc':datetime.now(timezone.utc).isoformat(),
            'branch_origem':BASE_BRANCH,'branch_candidata':CANDIDATE_BRANCH,
            'sha256_main_origem':sha_bytes(before_main),
            'sha256_shadow_origem':sha_bytes(before_shadow),
            'sha256_main_candidato':sha_file(root/'main.py'),
            'sha256_shadow_candidato':sha_file(root/'ed02_5_v510_http_shadow.py'),
            'arquivos_alterados':sorted(changed),
            'rotas_ed01_preservadas':list(FOUR_ROUTES),
            'render_alterado':False,'ativacao_financeira':False,
            'status':'PREFLIGHT_APROVADO'}


def sanitized_environment() -> dict[str, str]:
    env = {k: v for k in ('PATH','HOME','LANG','LC_ALL','TMPDIR','SYSTEMROOT','WINDIR')
           if (v := os.environ.get(k))}
    env.update({'PYTHONDONTWRITEBYTECODE':'1','PYTHONUNBUFFERED':'1',
                'ED01_HOMOLOGACAO':'false','DEMO_MODE':'sim',
                'ALLOW_SQLITE_FALLBACK':'sim'})
    for flag in FLAGS:
        env[flag] = 'false'
    return env


def regress(root: Path, out: Path, preflight_data: dict) -> dict:
    with tempfile.TemporaryDirectory(prefix='achetece_v5103_') as dirname:
        cloned = Path(dirname) / 'repo'
        shutil.copytree(root, cloned,
                        ignore=shutil.ignore_patterns('.git','.github','.env','.env.*',
                            'venv','.venv','__pycache__','*.db','*.sqlite','*.sqlite3',
                            '.pytest_cache'), symlinks=False)
        command = [sys.executable, '-B', '-m', 'unittest', '-v', *REGRESSION_MODULES]
        proc = subprocess.run(command, cwd=cloned, env=sanitized_environment(),
                              stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                              text=True, check=False, timeout=240)
        out.mkdir(parents=True, exist_ok=True)
        (out / 'ED02_V5103_REGRESSAO.log').write_text(proc.stdout, encoding='utf-8')
        original_expected = preflight_data['sha256_main_candidato']
        if sha_file(root / 'main.py') != original_expected:
            raise RuntimeError('BLOQUEADO: codigo do checkout foi modificado pelo teste')
        if proc.returncode:
            print('\n'.join(proc.stdout.splitlines()[-25:]))
            raise RuntimeError('BLOQUEADO: regressao ampliada do Gate 3 falhou')
        print('\n'.join(proc.stdout.splitlines()[-9:]))
        return {'status':'REGRESSAO_APROVADA','modulos_executados':list(REGRESSION_MODULES),
                'codigo_saida':proc.returncode,'checkout_preservado':True}


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('modo', choices=('preflight','regressao'))
    parser.add_argument('--root',type=Path,default=Path(__file__).resolve().parent)
    parser.add_argument('--out',type=Path,required=True)
    args=parser.parse_args()
    root=args.root.resolve(strict=True)
    out=args.out.resolve()
    if out == root or root in out.parents:
        raise RuntimeError('BLOQUEADO: relatorio dentro do checkout')
    result=preflight(root)
    print('PASSOU: branch isolada, SHA256, HEAD, escopo e quatro rotas ED01')
    if args.modo=='regressao':
        result.update(regress(root,out,result))
    out.mkdir(parents=True,exist_ok=True)
    (out / 'ED02_V5103_RESULTADO.json').write_text(
        json.dumps(result,ensure_ascii=False,indent=2,sort_keys=True),encoding='utf-8')
    print('PASSOU: integracao candidata validada; nenhum deploy ou pagamento real')


if __name__=='__main__':
    main()
