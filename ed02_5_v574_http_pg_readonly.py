"""V5.7.4: smoke-test HTTP/SQLAlchemy PostgreSQL read-only; NAO e teste das rotas originais.
Nenhuma gravacao, nenhum fixture persistente. Falha fechada para banco fora da whitelist.
"""
import os
from urllib.parse import urlparse

HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DB = 'achetece_ed01_test'

def validate_url(url):
    p = urlparse(url)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg') and p.hostname == HOST
            and p.path.lstrip('/') == DB and p.username and p.password
            and p.port in (None, 5432) and not p.fragment):
        raise RuntimeError('BLOQUEADO: destino PostgreSQL fora da homologacao')
    return url

def main():
    from flask import Flask, jsonify, abort
    from sqlalchemy import create_engine, text
    if os.environ.get('ED02_V574_READONLY_AUTHORIZED') != 'true':
        raise RuntimeError('BLOQUEADO: autorizacao ausente')
    url = validate_url(os.environ.get('ED02_HOMOLOG_DATABASE_URL', ''))
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True)
    app = Flask(__name__)
    app.config['TESTING'] = True
    # Rota diagnostica MINIMA, nunca exposta no site. Usa SQLAlchemy de verdade.
    # Nao substitui os endpoints de producao nem inventa dados ativos.
    @app.get('/__ed02_v574/empresa/<int:identificador>')
    def diagnostico_empresa(identificador):
        with engine.connect() as conn:
            trans = conn.begin()
            try:
                conn.execute(text('SET TRANSACTION READ ONLY'))
                if conn.execute(text('SELECT current_database()')).scalar_one() != DB:
                    abort(503)
                registro = conn.execute(text('''
                    SELECT id, status_pagamento, data_pagamento
                    FROM empresa WHERE id=:id
                '''), {'id': identificador}).mappings().first()
                # Apenas prova bloqueio de pendente/sem data; casos ativos requerem
                # executar a EXPRESSAO REAL do modelo e as rotas originais.
                if not registro or str(registro['status_pagamento'] or '').lower() != 'ativo' or registro['data_pagamento'] is None:
                    abort(404)
                return jsonify({'elegibilidade': 'revisao_pendente'}), 409
            finally:
                trans.rollback()
    try:
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text('SET TRANSACTION READ ONLY'))
                assert conn.execute(text('SELECT current_database()')).scalar_one() == DB
                row = conn.execute(text('SELECT status_pagamento, data_pagamento FROM empresa WHERE id=1')).first()
                if row is None or str(row[0] or '').lower() != 'pendente' or row[1] is not None:
                    raise RuntimeError('BLOQUEADO: fixture id=1 nao atende ao inventario esperado')
        with app.test_client() as client:
            assert client.get('/__ed02_v574/empresa/1').status_code == 404
            assert client.get('/__ed02_v574/empresa/999999999').status_code == 404
        with engine.connect() as conn:
            with conn.begin():
                conn.execute(text('SET TRANSACTION READ ONLY'))
                row = conn.execute(text('SELECT status_pagamento, data_pagamento FROM empresa WHERE id=1')).first()
                assert row is not None and str(row[0] or '').lower() == 'pendente' and row[1] is None
        print('PASSOU: HTTP diagnostico + SQLAlchemy PostgreSQL real; pendente e inexistente -> 404.')
        print('PASSOU: empresa de homologacao intacta; transacoes READ ONLY encerradas.')
        print('LIMITACAO: NAO executou /, /exportar, /utils/empresas_json, /empresa/<id> originais.')
        print('LIMITACAO: sem cenarios de ativo/vencido ou rollback de fixtures (nao houve insercoes).')
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
