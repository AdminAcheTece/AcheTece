"""ED02.3F: concorrência real em banco EXCLUSIVO de homologação.

Não usa rede do Mercado Pago, Flask, e-mails ou chaves de pagamento reais.
Duas conexões concorrentes gravam/confirmam; finally restaura o estado da
empresa fictícia e apaga somente identificadores específicos do teste.
Um erro fatal no runner pode interromper o finally: executar consulta de
verificação e, se necessário, limpeza supervisionada antes de repetir.
"""
import os
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ed02_payment_idempotency import process_verified_payment_exact_once

DB = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
IDS = ('990028102026101', '990028102026102')
REF = 'achetece:1:ed02-3f-concorrencia'


def run():
    url = os.getenv('ED02_HOMOLOG_DATABASE_URL', '')
    p = urlparse(url)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg', 'postgresql+psycopg2')
            and p.hostname == HOST and p.path.lstrip('/') == DB
            and p.username and p.password):
        raise RuntimeError('BLOQUEADO: conexao nao e a URL externa esperada da homologacao')
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={'sslmode': 'require'}, pool_size=3, max_overflow=2)
    touched = False

    def counts(conn):
        return conn.execute(text('''
            SELECT payment_id, contador_recebimentos, estado_processamento
              FROM public.pagamento_evento
             WHERE provedor='mercadopago' AND payment_id IN (:a, :b)
             ORDER BY payment_id
        '''), {'a': IDS[0], 'b': IDS[1]}).all()

    def call(payment_id, barrier=None):
        class Db:
            def __init__(self, session): self.session = session
        payload = dict(id=payment_id, status='approved', external_reference=REF,
                       currency_id='BRL', transaction_amount='2.00')
        with Session(engine) as session:
            if barrier is not None:
                barrier.wait(timeout=20)
            return process_verified_payment_exact_once(Db(session), payload, 1)

    try:
        with engine.connect() as c:
            if c.execute(text('SELECT current_database()')).scalar_one() != DB:
                raise RuntimeError('BLOQUEADO: banco PostgreSQL incorreto')
            company = c.execute(text('''
                SELECT nome, status_pagamento, data_pagamento FROM public.empresa WHERE id=1
            ''')).one_or_none()
            if (company is None or company.nome != 'ED01 Malharia Teste'
                or company.status_pagamento != 'pendente' or company.data_pagamento is not None):
                raise RuntimeError('BLOQUEADO: empresa ficticia nao esta pendente/sem pagamento')
            if counts(c):
                raise RuntimeError('BLOQUEADO: identificadores ED02.3F ja existem')
        touched = True
        barrier = Barrier(2)
        with ThreadPoolExecutor(max_workers=2) as pool:
            a = pool.submit(call, IDS[0], barrier)
            b = pool.submit(call, IDS[0], barrier)
            first, second = a.result(timeout=40), b.result(timeout=40)
        assert sum(int(x['aplicado']) for x in (first, second)) == 1, (first, second)
        assert sum(int(x['duplicado']) for x in (first, second)) == 1, (first, second)
        with engine.connect() as c:
            event = counts(c)
            assert len(event) == 1 and event[0].contador_recebimentos == 2
            assert event[0].estado_processamento == 'aplicado_ed02'
            first_date = c.execute(text('SELECT data_pagamento FROM public.empresa WHERE id=1')).scalar_one()
            assert first_date is not None
        replay = call(IDS[0])
        assert replay['duplicado'] and not replay['aplicado']
        with engine.connect() as c:
            assert c.execute(text('SELECT data_pagamento FROM public.empresa WHERE id=1')).scalar_one() == first_date
        renewal = call(IDS[1])
        assert renewal['aplicado'] and not renewal['duplicado']
        with engine.connect() as c:
            rows = counts(c)
            assert len(rows) == 2 and [r.contador_recebimentos for r in rows] == [3, 1]
            assert all(r.estado_processamento == 'aplicado_ed02' for r in rows)
            n = c.execute(text('''
                SELECT COUNT(*) FROM public.assinatura_ciclo
                 WHERE empresa_id=1 AND pagamento_confirmado_id IN
                       (SELECT id FROM public.pagamento_evento WHERE provedor='mercadopago'
                         AND payment_id IN (:a,:b))
            '''), {'a':IDS[0], 'b':IDS[1]}).scalar_one()
            assert n == 0
        print('PASSOU: duas conexoes simultaneas, um unico processamento do pagamento repetido.')
        print('PASSOU: repeticao posterior manteve data; nova transacao legitima foi aplicada.')
        print('PASSOU: nenhum ciclo de assinatura foi criado.')
    finally:
        if touched:
            with engine.begin() as c:
                actual = c.execute(text('SELECT current_database()')).scalar_one()
                if actual != DB:
                    raise RuntimeError('BLOQUEADO: limpeza em banco incorreto')
                owner = c.execute(text("SELECT nome FROM public.empresa WHERE id=1 FOR UPDATE")).scalar_one()
                if owner != 'ED01 Malharia Teste':
                    raise RuntimeError('BLOQUEADO: empresa de limpeza nao reconhecida')
                # Não remove ciclos arbitrários; teste exige zero ciclos.
                c.execute(text('''
                    DELETE FROM public.pagamento_evento
                     WHERE provedor='mercadopago' AND empresa_id=1
                       AND payment_id IN (:a,:b)
                '''), {'a':IDS[0],'b':IDS[1]})
                c.execute(text('''
                    UPDATE public.empresa SET status_pagamento='pendente',data_pagamento=NULL
                     WHERE id=1 AND nome='ED01 Malharia Teste'
                '''))
        with engine.connect() as c:
            assert not counts(c), 'FALHA: registro ficticio residual'
            state = c.execute(text('''
                SELECT status_pagamento,data_pagamento FROM public.empresa WHERE id=1
            ''')).one()
            assert state.status_pagamento == 'pendente' and state.data_pagamento is None
        engine.dispose()
        print('PASSOU: limpeza confirmada em nova conexao; empresa pendente e sem data.')

if __name__ == '__main__':
    run()
