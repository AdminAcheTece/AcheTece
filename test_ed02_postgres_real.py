"""ED02.3C — teste REAL PostgreSQL, restrito à homologação e totalmente reversível.

Uso: somente GitHub Actions workflow_dispatch, segredo ED02_HOMOLOG_DATABASE_URL.
A transação EXTERNA nunca recebe COMMIT; a Session usa SAVEPOINT, permitindo
que commits internos do módulo não publiquem as gravações fora da transação.
"""
import os
from urllib.parse import urlparse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from ed02_payment_shadow import shadow_record_payment

EXPECTED_DB = 'achetece_ed01_test'
EXPECTED_HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
PROVIDER = 'mercadopago'
PAYMENT_ID = '990028102026003'  # identificador fictício, somente no teste


def run():
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    parsed = urlparse(url)
    if (parsed.scheme not in ('postgresql', 'postgresql+psycopg2') or
        parsed.hostname != EXPECTED_HOST or
        parsed.path.lstrip('/') != EXPECTED_DB):
        raise RuntimeError('BLOQUEADO: URL não aponta EXATAMENTE para homologação')
    if not parsed.username or not parsed.password:
        raise RuntimeError('BLOQUEADO: credenciais ausentes')

    engine = create_engine(url, pool_pre_ping=True, connect_args={'sslmode': 'require'},
                           echo=False, hide_parameters=True)
    try:
        with engine.connect() as connection:
            outer = connection.begin()  # jamais commitamos essa transação externa
            try:
                db_name = connection.execute(text('SELECT current_database()')).scalar_one()
                if db_name != EXPECTED_DB:
                    raise RuntimeError('BLOQUEADO: banco real diverge da homologação')
                if connection.execute(text('SELECT to_regclass(\'public.pagamento_evento\')')).scalar_one() is None:
                    raise RuntimeError('BLOQUEADO: tabela de pagamentos não encontrada')
                empresa = connection.execute(text('''
                    SELECT nome, status_pagamento, data_pagamento
                    FROM public.empresa WHERE id=1 FOR UPDATE
                ''')).one_or_none()
                if empresa is None or empresa.nome != 'ED01 Malharia Teste' or empresa.status_pagamento != 'pendente' or empresa.data_pagamento is not None:
                    raise RuntimeError('BLOQUEADO: empresa fictícia não está pendente e sem pagamento')
                initial = connection.execute(text('''
                    SELECT count(*) FROM public.pagamento_evento
                    WHERE provedor=:p AND payment_id=:id
                '''), {'p':PROVIDER, 'id':PAYMENT_ID}).scalar_one()
                if initial != 0:
                    raise RuntimeError('BLOQUEADO: ID fictício de teste já registrado')

                class Db:
                    def __init__(self, session): self.session = session

                # A Session usa SAVEPOINT; os commits do módulo liberam apenas SAVEPOINTS.
                # O ROLLBACK da transação externa sempre elimina as alterações.
                with Session(bind=connection, join_transaction_mode='create_savepoint') as session:
                    db = Db(session)
                    payment = {'id':PAYMENT_ID,
                               'external_reference':'achetece:1:ed02-3c-transacao-isolada',
                               'status':'approved', 'currency_id':'BRL',
                               'transaction_amount':'2.00'}
                    first = shadow_record_payment(db, payment, 1)
                    second = shadow_record_payment(db, payment, 1)
                    assert (first['recebimentos'], second['recebimentos']) == (1,2), 'Contador de recebimentos incorreto'
                    result = connection.execute(text('''
                        SELECT count(*) registros, max(contador_recebimentos) recebimentos
                        FROM public.pagamento_evento
                        WHERE provedor=:p AND payment_id=:id
                    '''), {'p':PROVIDER,'id':PAYMENT_ID}).one()
                    assert result.registros == 1 and result.recebimentos == 2, 'Duplicidade não impedida'
                    ciclos = connection.execute(text('''
                        SELECT count(*) FROM public.assinatura_ciclo c
                        JOIN public.pagamento_evento p ON p.id=c.pagamento_confirmado_id
                        WHERE p.provedor=:p AND p.payment_id=:id
                    '''), {'p':PROVIDER,'id':PAYMENT_ID}).scalar_one()
                    assert ciclos == 0, 'Ciclo criado indevidamente'
                    unchanged = connection.execute(text('''
                        SELECT nome, status_pagamento, data_pagamento FROM public.empresa WHERE id=1
                    ''')).one()
                    assert unchanged == empresa, 'Cadastro financeiro da empresa foi alterado'
                    print('PASSOU: 2 recebimentos, 1 pagamento observacional, 0 ciclos, empresa intacta.')
            finally:
                outer.rollback()  # mesmo que qualquer assert/consulta falhe

        with engine.connect() as verify:
            remaining = verify.execute(text('''
                SELECT count(*) FROM public.pagamento_evento
                WHERE provedor=:p AND payment_id=:id
            '''), {'p':PROVIDER,'id':PAYMENT_ID}).scalar_one()
            assert remaining == 0, 'FALHA GRAVE: dados fictícios não foram revertidos'
            print('PASSOU: rollback confirmado em nova conexão; 0 registros residuais.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    run()
