"""ED02.4: lock NOWAIT entre duas conexoes, sem DML e com rollback.
Nao prova duas ativacoes reais simultaneas; esta e outra etapa de validacao.
"""
import os
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from test_ed02_3g7d3_postgres_integrado import check_url, DB, EMPRESA_ID, EMPRESA_NOME


def main():
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    check_url(url)
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={'sslmode': 'require'}, echo=False)
    try:
        with engine.connect() as first, engine.connect() as second:
            outer1 = first.begin()
            outer2 = second.begin()
            row = None
            try:
                for connection in (first, second):
                    if connection.execute(text('SELECT current_database()')).scalar_one() != DB:
                        raise RuntimeError('BLOQUEADO: banco conectado divergente')
                row = first.execute(text('SELECT nome,status_pagamento,data_pagamento FROM public.empresa WHERE id=:id FOR UPDATE'), {'id':EMPRESA_ID}).one_or_none()
                if row is None or row.nome != EMPRESA_NOME or row.status_pagamento != 'pendente' or row.data_pagamento is not None:
                    raise RuntimeError('BLOQUEADO: empresa ficticia fora do estado esperado')
                second.execute(text('SAVEPOINT ed024_lock_probe'))
                try:
                    second.execute(text('SELECT id FROM public.empresa WHERE id=:id FOR UPDATE NOWAIT'), {'id':EMPRESA_ID}).one()
                except OperationalError as ex:
                    sqlstate = getattr(ex.orig, 'pgcode', None) or getattr(ex.orig, 'sqlstate', None)
                    if sqlstate != '55P03':
                        raise RuntimeError('BLOQUEADO: erro inesperado no teste de concorrencia') from None
                    second.execute(text('ROLLBACK TO SAVEPOINT ed024_lock_probe'))
                    print('PASSOU: segunda conexao impedida de reservar a mesma empresa.')
                else:
                    raise AssertionError('FALHA: segunda conexao obteve lock indevido')
            finally:
                outer2.rollback()
                outer1.rollback()
        with engine.connect() as verify:
            after = verify.execute(text('SELECT nome,status_pagamento,data_pagamento FROM public.empresa WHERE id=:id'), {'id':EMPRESA_ID}).one_or_none()
            if after != row:
                raise AssertionError('FALHA: estado original foi alterado')
            print('PASSOU: rollback realizado; empresa permanece intacta.')
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
