"""ED02.3G.7D.3 - homologacao integrada, transacao externa reversivel.
Nao faz rede Mercado Pago, envio email nem toca no banco de producao.
O objeto `payment` e simulado como retorno de consulta autenticada.
"""
import os
import uuid
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import urlparse
from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session
from ed02_checkout_ledger import registrar_contratacao, registrar_resultado_preferencia
from ed02_payment_contract_guard import verify_payment_against_contract, ContractGuardError
from ed02_payment_idempotency import process_verified_payment_exact_once

DB = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
EMPRESA_ID = 1
EMPRESA_NOME = 'ED01 Malharia Teste'

def check_url(url):
    p = urlparse(url)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg2', 'postgresql+psycopg')
            and p.hostname == HOST and p.path.lstrip('/') == DB
            and p.username and p.password and p.port in (None, 5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: conexao fora da homologacao autorizada')

def main():
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    check_url(url)
    os.environ['ED01_HOMOLOGACAO'] = 'true'
    os.environ['ED02_FINANCIAL_GUARD_HOMOLOG'] = 'true'
    # Nao importa Flask nem aciona webhook, apenas rotinas transacionais locais.
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={'sslmode': 'require'}, echo=False)
    ref = f'achetece:{EMPRESA_ID}:{uuid.uuid4().hex}'
    pref = 'ED02-D3-' + uuid.uuid4().hex
    pid = str(uuid.uuid4().int)[:18]
    payment = dict(id=pid, status='approved', external_reference=ref,
                   currency_id='BRL', transaction_amount=2.0,
                   transaction_amount_refunded=0)
    try:
        with engine.connect() as conn:
            outer = conn.begin()
            try:
                if conn.execute(text('SELECT current_database()')).scalar_one() != DB:
                    raise RuntimeError('BLOQUEADO: banco real divergente')
                for table in ('empresa', 'contratacao_checkout', 'pagamento_evento'):
                    if conn.execute(text('SELECT to_regclass(:reg)'), {'reg':'public.'+table}).scalar_one() is None:
                        raise RuntimeError('BLOQUEADO: schema ED02 incompleto')
                before = conn.execute(text('SELECT nome,status_pagamento,data_pagamento FROM public.empresa WHERE id=:id FOR UPDATE'), {'id':EMPRESA_ID}).one_or_none()
                if before is None or before.nome != EMPRESA_NOME or before.status_pagamento != 'pendente' or before.data_pagamento is not None:
                    raise RuntimeError('BLOQUEADO: empresa ficticia fora do estado esperado')
                if conn.execute(text('SELECT COUNT(*) FROM public.pagamento_evento WHERE payment_id=:pid'), {'pid':pid}).scalar_one():
                    raise RuntimeError('BLOQUEADO: payment_id de teste ja existe')
                with Session(bind=conn, join_transaction_mode='create_savepoint') as sess:
                    db = SimpleNamespace(session=sess, engine=engine)
                    registrar_contratacao(db, EMPRESA_ID, ref, 'mensal', '2.00')
                    registrar_resultado_preferencia(db, ref, pref)
                    valid = verify_payment_against_contract(db, payment, pid)
                    assert valid['empresa_id'] == EMPRESA_ID and valid['valor'] == Decimal('2.00')
                    print('PASSOU: guarda validou contrato/preferencia/empresa/plano/valor/moeda.')
                    for key, val in (('transaction_amount', 3.0), ('currency_id','USD'), ('external_reference','achetece:999:'+uuid.uuid4().hex)):
                        altered = dict(payment, **{key:val})
                        try:
                            verify_payment_against_contract(db, altered, pid)
                        except (ContractGuardError, ValueError):
                            pass
                        else:
                            raise AssertionError('Pagamento adulterado aceito: '+key)
                    assert conn.execute(text('SELECT COUNT(*) FROM public.pagamento_evento WHERE payment_id=:pid'), {'pid':pid}).scalar_one() == 0
                    print('PASSOU: valor, moeda e referencia adulterados bloqueados antes da gravacao.')
                    result = process_verified_payment_exact_once(db, payment, EMPRESA_ID)
                    assert result['aplicado'] is True and result['duplicado'] is False
                    row = conn.execute(text('SELECT status_pagamento,data_pagamento FROM public.empresa WHERE id=:id'), {'id':EMPRESA_ID}).one()
                    assert row.status_pagamento == 'ativo' and row.data_pagamento is not None
                    again = process_verified_payment_exact_once(db, payment, EMPRESA_ID)
                    assert again['aplicado'] is False and again['duplicado'] is True
                    row2 = conn.execute(text('SELECT status_pagamento,data_pagamento FROM public.empresa WHERE id=:id'), {'id':EMPRESA_ID}).one()
                    assert row2 == row, 'Replay alterou a data da vigencia'
                    print('PASSOU: ativacao provisoria unica; replay preservou a vigencia.')
            finally:
                # Esta e a UNICA finalizacao da transacao externa. Proibido commit.
                outer.rollback()
        with engine.connect() as verify:
            if verify.execute(text('SELECT current_database()')).scalar_one() != DB:
                raise RuntimeError('BLOQUEADO: banco divergente ao conferir rollback')
            residual_contract = verify.execute(text('SELECT COUNT(*) FROM public.contratacao_checkout WHERE external_reference=:r'), {'r':ref}).scalar_one()
            residual_payment = verify.execute(text('SELECT COUNT(*) FROM public.pagamento_evento WHERE payment_id=:pid'), {'pid':pid}).scalar_one()
            after = verify.execute(text('SELECT nome,status_pagamento,data_pagamento FROM public.empresa WHERE id=:id'), {'id':EMPRESA_ID}).one_or_none()
            assert residual_contract == 0 and residual_payment == 0 and after == before, 'FALHA CRITICA: rollback incompleto'
            print('PASSOU: segunda conexao confirma zero residuos e empresa original intacta.')
    finally:
        engine.dispose()

if __name__ == '__main__':
    main()
