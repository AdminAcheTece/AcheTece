"""ED02.5 V5.7.1: auditoria ESTATICA e somente leitura das superfícies ED01.

Não importa main.py, não conecta ao banco nem modifica rotas.
"""
from __future__ import annotations
import ast
import json
from pathlib import Path

ROUTES = {
    'home': ('index', '/', 'assinatura_ativa'),
    'csv': ('exportar', '/exportar', 'assinatura_ativa'),
    'json': ('utils_empresas_json', '/utils/empresas_json', 'assinatura_ativa'),
    'perfil': ('empresa_perfil', '/empresa/<int:empresa_id>', 'assinatura_ativa'),
}


def _functions(tree):
    return {node.name: node for node in tree.body if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef))}


def _route_paths(fn):
    values = []
    for d in fn.decorator_list:
        if isinstance(d, ast.Call) and isinstance(d.func, ast.Attribute) and d.func.attr in {'route', 'get', 'post'}:
            if d.args and isinstance(d.args[0], ast.Constant) and isinstance(d.args[0].value, str):
                values.append(d.args[0].value)
    return values


def _expr_uses_filter(node, token):
    """Verifica aplicação de filtro em query, não apenas texto em comentário/docstring."""
    for n in ast.walk(node):
        if not isinstance(n, ast.Call) or not isinstance(n.func, ast.Attribute) or n.func.attr != 'filter':
            continue
        if any(isinstance(part, ast.Attribute) and part.attr == token for arg in n.args for part in ast.walk(arg)):
            return True
    return False


def audit(source: str):
    tree = ast.parse(source)
    funcs = _functions(tree)
    results = {}
    for label, (name, route, token) in ROUTES.items():
        fn = funcs.get(name)
        has_route = fn is not None and route in _route_paths(fn)
        filter_found = fn is not None and _expr_uses_filter(fn, token)
        results[label] = {'route': route, 'function': name, 'route_present': bool(has_route), 'eligibility_filter_in_query': bool(filter_found), 'pass': bool(has_route and filter_found)}

    cls = next((n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'Empresa'), None)
    prop = next((n for n in cls.body if isinstance(n, ast.FunctionDef) and n.name == 'assinatura_ativa'), None) if cls else None
    source_property = ast.get_source_segment(source, prop) if prop else ''
    results['ed01_contract'] = {
        'status_ativo_required': 'status_pagamento' in source_property and 'ativo' in source_property,
        'payment_date_required': 'data_pagamento' in source_property,
        'legacy_expiration_present': '_dias_por_plano' in source_property,
    }
    admin = funcs.get('admin_seed_teares_all')
    results['review_nonpublic_admin_seed'] = {
        'present': admin is not None,
        'uses_raw_status_filter': bool(admin and any(isinstance(n, ast.Attribute) and n.attr == 'status_pagamento' for n in ast.walk(admin))),
        'note': 'Rota administrativa: revisar autorização e filtro antes de futura integração ED02; não é exposição pública comprovada.',
    }
    results['scope'] = 'Auditoria AST: não executa HTTP, SQL, templates, endpoints adicionais nem comprova comportamento em runtime.'
    return results


def main():
    result = audit(Path(__file__).with_name('main.py').read_text(encoding='utf-8'))
    print(json.dumps(result, ensure_ascii=False, indent=2))
    if not all(result[name]['pass'] for name in ROUTES):
        raise SystemExit('BLOQUEADO: filtro ED01 ausente em uma superfície essencial')
    if not all(result['ed01_contract'].values()):
        raise SystemExit('BLOQUEADO: contrato ED01 alterado ou não reconhecido')
    print('PASSOU: auditoria estática ED01; nenhuma alteração realizada.')

if __name__ == '__main__':
    main()
