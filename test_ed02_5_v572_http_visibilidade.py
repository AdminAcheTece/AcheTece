"""ED02.5 V5.7.2 — testes HTTP das rotas reais extraídas do main.py.

Isolamento: Flask test_client + consultas fictícias em memória.
Sem banco externo, Mercado Pago, gravações ou mudanças de código produtivo.
A lógica SQL real em PostgreSQL será testada em estágio separado.
"""
import ast
import csv
import io
import re
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from types import SimpleNamespace

from flask import Flask, abort, g, jsonify, request, send_file

SOURCE = Path(__file__).with_name('main.py')
NOW = datetime.utcnow()


class Expression:
    def __init__(self, fn):
        self.fn = fn
    def __call__(self, item):
        return self.fn(item)
    def __eq__(self, other):
        return Expression(lambda item: self(item) == other)
    def is_(self, other):
        return Expression(lambda item: self(item) is other)
    def asc(self):
        return self
    def desc(self):
        return self


class Field(Expression):
    def __init__(self, cls, attr):
        self.cls, self.attr = cls, attr
        super().__init__(self._get)
    def _get(self, item):
        obj = item.empresa if self.cls == 'Empresa' and hasattr(item, 'empresa') else item
        return getattr(obj, self.attr)


class ModelMeta(type):
    @property
    def query(cls):
        return FakeQuery(DATA['teares'] if cls.__name__ == 'Tear' else DATA['empresas'])


class Empresa(metaclass=ModelMeta):
    id = Field('Empresa', 'id')
    estado = Field('Empresa', 'estado')
    cidade = Field('Empresa', 'cidade')
    assinatura_ativa = Expression(lambda row: eligible(row.empresa if hasattr(row, 'empresa') else row))


class Tear(metaclass=ModelMeta):
    id = Field('Tear', 'id')
    empresa_id = Field('Tear', 'empresa_id')
    ativo = Field('Tear', 'ativo')
    tipo = Field('Tear', 'tipo')
    diametro = Field('Tear', 'diametro')
    finura = Field('Tear', 'finura')
    alimentadores = Field('Tear', 'alimentadores')
    elastano = Field('Tear', 'elastano')


def eligible(emp):
    if (emp.status_pagamento or '').strip().lower() != 'ativo' or emp.data_pagamento is None:
        return False
    dias = 370 if (emp.plano or '').lower() == 'anual' else 35
    return emp.data_pagamento + timedelta(days=dias) > NOW


class FakeQuery:
    def __init__(self, rows, selected=None):
        self.rows = list(rows)
        self.selected = selected
    def join(self, *args, **kwargs):
        return self
    def filter(self, *exprs):
        for expr in exprs:
            if not isinstance(expr, Expression):
                raise AssertionError('Expressão não implementada no fixture HTTP')
            self.rows = [row for row in self.rows if expr(row)]
        return self
    def filter_by(self, **kwargs):
        self.rows = [row for row in self.rows if all(getattr(row, key) == val for key, val in kwargs.items())]
        return self
    def with_entities(self, *fields):
        return FakeQuery(self.rows, fields)
    def all(self):
        if self.selected:
            return [tuple(field(row) for field in self.selected) for row in self.rows]
        return list(self.rows)
    def count(self):
        return len(self.rows)
    def order_by(self, field):
        return self
    def offset(self, n):
        self.rows = self.rows[n:]
        return self
    def limit(self, n):
        self.rows = self.rows[:n]
        return self
    def first_or_404(self):
        if not self.rows:
            abort(404)
        return self.rows[0]


class Func:
    def lower(self, expr):
        return Expression(lambda item: str(expr(item) or '').lower())
    def upper(self, expr):
        return Expression(lambda item: str(expr(item) or '').upper())


def make_empresa(i, status, days_ago, plano='mensal', estado='SC', cidade='Jaraguá do Sul'):
    return SimpleNamespace(id=i, nome=f'EMP_TEST_{i}', apelido=f'EMP_TEST_{i}', email=f'teste{i}@example.invalid',
        telefone=None, estado=estado, cidade=cidade, status_pagamento=status, plano=plano,
        data_pagamento=None if days_ago is None else NOW-timedelta(days=days_ago))


def make_tear(i, empresa, tipo='circular'):
    return SimpleNamespace(id=i, empresa_id=empresa.id, empresa=empresa, ativo=True,
        marca='MARCA_TESTE', modelo='MODELO_TESTE', tipo=tipo, diametro=30,
        finura=28, alimentadores=90, elastano=False)


def load_routes(namespace):
    text = SOURCE.read_text(encoding='utf-8')
    tree = ast.parse(text, filename=str(SOURCE))
    names = {'index', 'exportar', 'utils_empresas_json', 'empresa_perfil'}
    found = []
    for node in tree.body:
        if isinstance(node, ast.FunctionDef) and node.name in names:
            node.decorator_list = []
            found.append(node)
    if {n.name for n in found} != names:
        raise AssertionError('Rotas ED01 não encontradas no main.py atual')
    module = ast.fix_missing_locations(ast.Module(body=found, type_ignores=[]))
    exec(compile(module, str(SOURCE), 'exec'), namespace)
    return namespace


class HTTPVisibilidadeED01(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.app = Flask('ed02_v572_http_isolado')
        cls.app.config.update(TESTING=True, SECRET_KEY='teste-local-sem-externos')
        ns = {
            'app': cls.app, 'request': request, 'g': g, 'jsonify': jsonify, 'send_file': send_file,
            'Empresa': Empresa, 'Tear': Tear, 'db': SimpleNamespace(func=Func()), 'func': Func(),
            'DEMO_FILTER': Expression(lambda row: True), 're': re, 'csv': csv, 'io': io,
            'render_template': lambda template, **data: jsonify({
                'total': data.get('total', 0), 'pagina': data.get('pagina'),
                'resultados': [x['empresa'] for x in data.get('resultados', [])],
                'opcoes': data.get('opcoes', {})}),
            '_render_offline': lambda: ('indisponivel', 503),
            '_num_key': lambda x: (float(x) if str(x).replace('.', '', 1).isdigit() else x),
            '_to_int': lambda x: int(x) if str(x).isdigit() else None,
            'track_event': lambda *args, **kwargs: None,
            'abort': abort,
        }
        load_routes(ns)
        cls.app.add_url_rule('/', 'index_test', ns['index'])
        cls.app.add_url_rule('/exportar', 'exportar_test', ns['exportar'])
        cls.app.add_url_rule('/utils/empresas_json', 'json_test', ns['utils_empresas_json'])
        cls.app.add_url_rule('/empresa/<int:empresa_id>', 'empresa_test', ns['empresa_perfil'])

    def setUp(self):
        es = [
            make_empresa(1, 'ativo', 1),
            make_empresa(2, 'pendente', 1),
            make_empresa(3, 'ativo', 80),
            make_empresa(4, 'ativo', None),
            make_empresa(5, 'aprovado', 1),
            make_empresa(6, 'ativo', 200, plano='anual', estado='PR', cidade='Curitiba'),
        ]
        DATA['empresas'] = es
        DATA['teares'] = [make_tear(i, e) for i, e in enumerate(es, 1)]
        self.client = self.app.test_client()

    def test_home_exibe_apenas_duas_vigentes(self):
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(set(resp.json['resultados']), {'EMP_TEST_1', 'EMP_TEST_6'})
        self.assertEqual(resp.json['total'], 2)

    def test_home_filtros_tecnicos_e_localizacao(self):
        resp = self.client.get('/?tipo=circular&diametro=30&galga=28&estado=SC&cidade=Jaragu%C3%A1%20do%20Sul')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.json['resultados'], ['EMP_TEST_1'])
        self.assertNotIn('EMP_TEST_2', str(resp.json))

    def test_home_paginacao_nao_recupera_bloqueadas(self):
        p1 = self.client.get('/?pp=1&pagina=1').json
        p2 = self.client.get('/?pp=1&pagina=2').json
        p3 = self.client.get('/?pp=1&pagina=3').json
        self.assertEqual(p1['total'], 2)
        self.assertEqual(len(p1['resultados']) + len(p2['resultados']), 2)
        self.assertFalse(p3['resultados'])

    def test_csv_nao_expoe_bloqueadas_com_ou_sem_filtros(self):
        for url in ('/exportar', '/exportar?estado=SC&tipo=circular&galga=28'):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            s = resp.get_data(as_text=True)
            self.assertIn('EMP_TEST_1', s)
            for i in (2,3,4,5):
                self.assertNotIn(f'EMP_TEST_{i}', s)

    def test_json_pagantes_demo_uf(self):
        for url, expected in (('/utils/empresas_json?escopo=pagantes', {1,6}),
                              ('/utils/empresas_json?escopo=demo', {1,6}),
                              ('/utils/empresas_json?escopo=pagantes&uf=SC', {1})):
            resp = self.client.get(url)
            self.assertEqual(resp.status_code, 200)
            self.assertEqual({e['id'] for e in resp.json}, expected)

    def test_perfil_direto_bloqueia_pendente_vencido_sem_data(self):
        for i in (2,3,4,5,999999):
            self.assertEqual(self.client.get(f'/empresa/{i}').status_code, 404)

    def test_perfil_de_empresa_vigente(self):
        # Substitui somente o renderizador durante este teste; a consulta vem da rota real.
        fn = self.app.view_functions['empresa_test']
        original = fn.__globals__['render_template']
        fn.__globals__['render_template'] = lambda template, **k: jsonify({'id': k['empresa'].id})
        try:
            self.assertEqual(self.client.get('/empresa/1').json, {'id': 1})
        finally:
            fn.__globals__['render_template'] = original

    def test_empresa_pendente_permanece_oculta_ao_mudar_filtros(self):
        for url in ('/', '/?estado=SC', '/?cidade=Jaragu%C3%A1%20do%20Sul', '/?tipo=circular', '/?pp=100'):
            self.assertNotIn('EMP_TEST_2', self.client.get(url).get_data(as_text=True))


DATA = {'empresas': [], 'teares': []}

if __name__ == '__main__':
    unittest.main()
