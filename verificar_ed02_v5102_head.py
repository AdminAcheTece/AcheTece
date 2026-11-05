"""ED02.5 V5.10.2 — preflight fail-closed; APENAS LEITURA.

Verifica que o checkout do GitHub ainda corresponde à revisão auditada.
Não altera arquivos, banco, Render, flags ou a produção.
"""
from __future__ import annotations
import hashlib
import json
import sys
from pathlib import Path

R = Path(__file__).resolve().parent


def sha(path: Path) -> str:
    if not path.is_file() or path.is_symlink():
        raise RuntimeError('V5102 BLOQUEADO: arquivo ausente ou link simbólico')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verificar():
    registro = json.loads((R / 'ED02_V5102_RELATORIO.json').read_text('utf8'))
    esperado_origem = registro['head_main_esperado_sha256']
    esperado_v5101 = registro['main_v5101_sha256']
    atual = sha(R / 'main.py')
    if atual not in {esperado_origem, esperado_v5101}:
        raise RuntimeError('V5102 BLOQUEADO: main.py divergiu do HEAD auditado; revisar antes de atualizar')
    atual_shadow = sha(R / 'ed02_5_v510_http_shadow.py')
    if atual_shadow != registro['shadow_v5101_sha256']:
        raise RuntimeError('V5102 BLOQUEADO: shadow operacional divergiu; revisar antes de atualizar')
    if sha(R / 'main_V5102_CANDIDATO.py') != registro['main_v5102_sha256']:
        raise RuntimeError('V5102 BLOQUEADO: candidato com integridade divergente')
    if sha(R / 'ed02_5_v510_http_shadow_V5102_CANDIDATO.py') != registro['shadow_v5102_sha256']:
        raise RuntimeError('V5102 BLOQUEADO: shadow candidato com integridade divergente')
    print('PASSOU: HEAD auditado; candidatos íntegros; sem substituição de arquivos')
    print('STATUS: SOMENTE REVISAO; flags não alteradas; produção bloqueada')


if __name__ == '__main__':
    try:
        verificar()
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        raise SystemExit(1)
