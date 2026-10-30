"""ED02.5 V5.5.1: dois commits reais em homologacao com limpeza auditada.
ATENCAO: usa somente empresa ficticia ID=1, com estado pendente. Nunca importar em app.
"""
import os
import uuid
import concurrent.futures
from datetime import datetime
from types import SimpleNamespace
from unittest.mock import MagicMock
from sqlalchemy import create_engine, text
from test_ed02_5_v54_flask_pg_integrado import check_url, load_flask_processor, DB_NAME

PREFIX = 'ED025-V551-'

def assert_destination(conn):
    if conn.execute(text('SELECT current_database()')).scalar_one() != DB_NAME:
        raise RuntimeError('BLOQUEADO: banco efetivo diferente')
    row = conn.execute(text('SELECT status_pagamento,data_pagamento FROM public.empresa WHERE id=1')).one_or_none()
    if not row or (row[0] or '').lower() != 'pendente' or row[1] is not None:
        raise RuntimeError('BLOQUEADO: empresa de ensaio nao esta pendente')
    for name in ('public.ed02_assinatura_periodo','public.pagamento_evento','public.contratacao_checkout'):
        if conn.execute(text('SELECT to_regclass(:t)'), {'t':name}).scalar_one() is None:
            raise RuntimeError('BLOQUEADO: estrutura ausente')

def main():
    if os.getenv('ED02_V551_COMMIT_AUTHORIZED') != 'true':
        raise RuntimeError('BLOQUEADO: execucao nao autorizada')
    url = os.getenv('ED02_HOMOLOG_DATABASE_URL','')
    check_url(url)
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={'sslmode':'require', 'connect_timeout':10})
    token = uuid.uuid4().hex
    reference = f'achetece:1:{token}'
    payment_id = str(920000000000000000 + (int(token[:12],16) % 10000000000))
    preference = PREFIX + token
    initialized = False
    old_env = os.environ.get('ED01_HOMOLOGACAO')
    os.environ['ED01_HOMOLOGACAO'] = 'true'
    try:
        with engine.begin() as conn:
            assert_destination(conn)
            conn.execute(text("SET LOCAL lock_timeout='4s'"))
            # Nao tocar em nenhum contrato preexistente.
            conn.execute(text('''INSERT INTO public.contratacao_checkout
               (empresa_id,external_reference,plano,valor_esperado,moeda,status,provedor,preference_id)
               VALUES (1,:ref,'mensal',2.00,'BRL','preferencia_criada','mercadopago',:pref)'''),
               {'ref':reference,'pref':preference})
        initialized = True
        payment = {'id':payment_id,'status':'approved','currency_id':'BRL',
                   'transaction_amount':2.0,'external_reference':reference}
        def run_one(_):
            # Cada thread tem conexao e sessao distintas: commit de verdade.
            with engine.connect() as connection:
                class Session:
                    def execute(self, *a, **kw): return connection.execute(*a, **kw)
                    def commit(self): connection.commit()
                    def rollback(self): connection.rollback()
                db = SimpleNamespace(engine=engine, session=Session())
                company = SimpleNamespace(id=1,status_pagamento='pendente',data_pagamento=None)
                processor, scope = load_flask_processor(db, {payment_id:payment}, company)
                try:
                    result = processor(payment_id)
                    assert result.get('ok') and result['ativou'] is False
                    assert scope['_send_email'].call_count == 0
                    assert company.status_pagamento == 'pendente' and company.data_pagamento is None
                    return bool(result['duplicado'])
                finally:
                    connection.rollback()
        # Uma falha em qualquer thread interrompe o ensaio, e o finally limpa.
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            outcomes = list(pool.map(run_one, (1,2)))
        if sorted(outcomes) != [False,True]:
            raise AssertionError('processamento concorrente nao idempotente')
        with engine.connect() as conn:
            assert_destination(conn)
            counts = [conn.execute(text(f'''SELECT count(*) FROM {t}
                WHERE payment_id=:pid'''), {'pid':payment_id}).scalar_one()
                for t in ('public.ed02_assinatura_periodo','public.pagamento_evento')]
            if counts != [1,1]:
                raise AssertionError('nao ha exatamente um periodo e um ledger confirmado')
            if conn.execute(text('''SELECT count(*) FROM public.contratacao_checkout
                  WHERE external_reference=:ref AND preference_id=:pref'''),
                  {'ref':reference,'pref':preference}).scalar_one() != 1:
                raise AssertionError('contrato nao confirmado')
            print('PASSOU: duas sessoes com commits reais; uma insercao e um replay.')
            print('PASSOU: segunda leitura confirma periodo=1 e ledger=1.')
    finally:
        try:
            if initialized:
                with engine.begin() as conn:
                    assert_destination(conn)
                    conn.execute(text("SET LOCAL lock_timeout='4s'"))
                    # Somente linhas cujos identificadores sao exclusivos deste ensaio.
                    conn.execute(text('''DELETE FROM public.ed02_assinatura_periodo
                        WHERE payment_id=:pid AND provedor='mercadopago' AND empresa_id=1
                        AND contrato_id IN (SELECT id FROM public.contratacao_checkout
                          WHERE external_reference=:ref AND preference_id=:pref AND empresa_id=1)'''),
                        {'pid':payment_id,'ref':reference,'pref':preference})
                    conn.execute(text('''DELETE FROM public.pagamento_evento
                        WHERE payment_id=:pid AND provedor='mercadopago' AND empresa_id=1
                        AND external_reference=:ref'''),{'pid':payment_id,'ref':reference})
                    conn.execute(text('''DELETE FROM public.contratacao_checkout
                        WHERE external_reference=:ref AND preference_id=:pref AND empresa_id=1'''),
                        {'ref':reference,'pref':preference})
                with engine.connect() as conn:
                    assert_destination(conn)
                    checks = (
                      conn.execute(text('SELECT count(*) FROM public.ed02_assinatura_periodo WHERE payment_id=:p'),{'p':payment_id}).scalar_one(),
                      conn.execute(text('SELECT count(*) FROM public.pagamento_evento WHERE payment_id=:p'),{'p':payment_id}).scalar_one(),
                      conn.execute(text('SELECT count(*) FROM public.contratacao_checkout WHERE external_reference=:r'),{'r':reference}).scalar_one())
                    if checks != (0,0,0): raise RuntimeError('CRITICO: residuos de ensaio')
                    print('PASSOU: limpeza auditada confirmada por segunda conexao.')
        finally:
            if old_env is None: os.environ.pop('ED01_HOMOLOGACAO',None)
            else: os.environ['ED01_HOMOLOGACAO'] = old_env
            engine.dispose()

if __name__ == '__main__':
    main()
