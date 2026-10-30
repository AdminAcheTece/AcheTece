"""Auditoria conservadora das rotas ED01; não importa main.py nem altera dados."""
import ast
from pathlib import Path

ROUTES = {'index': '/', 'exportar': '/exportar', 'utils_empresas_json': '/utils/empresas_json', 'empresa_perfil': '/empresa/<int:empresa_id>'}


def auditar(codigo: str):
    tree = ast.parse(codigo)
    funcs = {n.name: n for n in tree.body if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))}
    problemas = []
    for nome in ROUTES:
        if nome not in funcs:
            problemas.append(f'{nome}: ROTA_AUSENTE')
            continue
        func = funcs[nome]
        corpo = ast.get_source_segment(codigo, func) or ''
        if nome == 'index':
            if 'Empresa.assinatura_ativa' not in corpo:
                problemas.append('index: SEM_FILTRO_ASSINATURA')
            if 'segue sem o filtro' in corpo or 'fallback por status' in corpo.lower():
                problemas.append('index: FALLBACK_POTENCIALMENTE_ABERTO')
        elif nome == 'exportar':
            if 'Empresa.assinatura_ativa' not in corpo:
                problemas.append('exportar: SEM_FILTRO_ASSINATURA')
        elif nome == 'utils_empresas_json':
            if 'Empresa.assinatura_ativa' not in corpo:
                problemas.append('utils_empresas_json: SEM_FILTRO_ASSINATURA')
        elif nome == 'empresa_perfil':
            if 'assinatura_ativa' not in corpo:
                problemas.append('empresa_perfil: SEM_GUARDA_ASSINATURA')
    return sorted(problemas)


def main():
    src = Path('main.py').read_text(encoding='utf-8')
    problemas = auditar(src)
    print('ED02.5 V5.7.3 - AUDITORIA ESTATICA (SEM GRAVACOES)')
    for problema in problemas:
        print('ACHADO:', problema)
    if problemas:
        print(f'BLOQUEADO: {len(problemas)} riscos exigem correcao antes da homologacao HTTP+PostgreSQL.')
        return 2
    print('PASSOU: verificacao estatica; ainda requer teste HTTP+PG real.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
