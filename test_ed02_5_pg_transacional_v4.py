"""ED02.5 V4 - ensaio do adaptador real com rollback EXTERNO obrigatório.

Somente GitHub Actions protegido e banco achetece_ed01_test. Sem API MP,
sem email, sem Flask, sem COMMIT persistente. Usa uma empresa de teste existente.
"""
import os
import secrets
import uuid
from datetime import datetime, timezone
from decimal import Decimal
from pathlib import Path
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from ed02_5_transacao import registrar_periodo_homolog

DB = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
TABLE = 'public.ed02_assinatura_periodo'


def guard_url(url):
    p = urlparse(url)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg2', 'postgresql+psycopg')
            and p.hostname == HOST and p.path == '/' + DB
            and p.username and p.password and p.port in (None, 5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: URL fora do PostgreSQL de homologacao')


class UncommittableSession:
    """Usa a conexão externa e neutraliza COMMIT interno do adaptador.

    A transação externa é controlada apenas pelo teste; nenhuma chamada ao
    adaptador pode persistir a alteração. Falhas são propagadas.
    """
    def __init__(self, conn):
        self.conn = conn

    def execute(self, *args, **kwargs):
        return self.conn.execute(*args, **kwargs)

    def commit(self):
        if not self.conn.in_transaction():
            raise RuntimeError('BLOQUEADO: transacao externa ausente')
        # Proibido persistir; o teste verifica a escrita dentro da transacao.

    def rollback(self):
        self.conn.rollback()


class DBFacade:
    def __init__(self, engine, conn):
        self.engine = engine
        self.session = UncommittableSession(conn)


def main():
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    guard_url(url)
    if os.environ.get('ED02_V4_TRANSACAO_AUTORIZADA') != 'true':
        raise RuntimeError('BLOQUEADO: gatilho V4 ausente')
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={'sslmode':'require'}, echo=False)
    old_flag = os.environ.get('ED01_HOMOLOGACAO')
    os.environ['ED01_HOMOLOGACAO'] = 'true'
    test_ids = []
    tx = None
    try:
        with engine.connect() as conn:
            tx = conn.begin()
            try:
                if conn.execute(text('SELECT current_database()')).scalar_one() != DB:
                    raise RuntimeError('BLOQUEADO: banco conectado incorreto')
                conn.exec_driver_sql("SET LOCAL lock_timeout='3s'")
                conn.exec_driver_sql("SET LOCAL statement_timeout='20s'")
                for required in ('public.empresa','public.contratacao_checkout','public.pagamento_evento'):
                    if conn.execute(text('SELECT to_regclass(:t)'), {'t':required}).scalar_one() is None:
                        raise RuntimeError('BLOQUEADO: schema pre-requisito ausente')
                if conn.execute(text('SELECT to_regclass(:t)'), {'t':TABLE}).scalar_one() is not None:
                    raise RuntimeError('BLOQUEADO: tabela de vigencia preexistente')
                # Ensaio DDL inteiramente dentro da mesma transacao que sera revertida.
                sql = Path(__file__).with_name('ed02_5_schema_homolog.sql').read_text(encoding='utf-8')
                ddl = '\n'.join(line for line in sql.splitlines()
                                if line.strip().upper() not in ('BEGIN;', 'COMMIT;'))
                conn.exec_driver_sql(ddl)
                empresa = conn.execute(text('''SELECT id, status_pagamento, data_pagamento
                         FROM public.empresa WHERE id=1 FOR UPDATE''')).mappings().first()
                if empresa is None or (empresa['status_pagamento'] or '').strip().lower() != 'pendente' or empresa['data_pagamento'] is not None:
                    raise RuntimeError('BLOQUEADO: empresa 1 nao esta no estado seguro esperado')
                snapshot = (empresa['status_pagamento'], empresa['data_pagamento'])
                # Segunda conexao prova bloqueio NOWAIT no banco real.
                with engine.connect() as competitor:
                    try:
                        competitor.execute(text('SELECT id FROM public.empresa WHERE id=1 FOR UPDATE NOWAIT')).first()
                    except Exception as e:
                        if getattr(getattr(e, 'orig', None), 'sqlstate', None) != '55P03':
                            raise RuntimeError('falha concorrente inesperada') from None
                        print('PASSOU: segunda conexao bloqueada com SQLSTATE 55P03.')
                    else:
                        raise AssertionError('CRITICO: segunda conexao adquiriu lock da empresa')
                    finally:
                        competitor.rollback()

                db = DBFacade(engine, conn)
                fixed_date = datetime(2026, 10, 30, 12, 0, tzinfo=timezone.utc)
                scenarios = [('mensal', '100'), ('anual', '101')]
                results = []
                for plano, pid in scenarios:
                    reference = f'achetece:1:{uuid.uuid4().hex}'
                    pref_id = f'ED025-V4-TEST-{uuid.uuid4().hex}'
                    conn.execute(text('''INSERT INTO public.contratacao_checkout
                        (empresa_id, external_reference, plano, valor_esperado, moeda,
                         status, provedor, preference_id)
                        VALUES (1,:ref,:plano,2.00,'BRL','preferencia_criada',
                                'mercadopago',:pref)'''),
                        {'ref':reference,'plano':plano,'pref':pref_id})
                    payment_id = str(900000000000000000 + secrets.randbelow(9999999999))
                    test_ids.append(payment_id)
                    payment = {'id':payment_id, 'status':'approved',
                               'currency_id':'BRL', 'transaction_amount':2.0,
                               'external_reference':reference}
                    result = registrar_periodo_homolog(db,payment,1,fixed_date)
                    assert result['duplicado'] is False, 'primeira cobranca duplicada'
                    repeat = registrar_periodo_homolog(db,payment,1,fixed_date)
                    assert repeat['duplicado'] is True and repeat['periodo_id'] == result['periodo_id'], 'replay invalido'
                    assert repeat['fim_exclusivo'] == result['fim_exclusivo'], 'replay alterou vigencia'
                    results.append(result)
                # Segundo contrato renovou ANTES do fim mensal: inicia no fim do anterior.
                assert results[1]['inicio'] == results[0]['fim_exclusivo'], 'renovacao antecipada nao preservada'
                assert results[1]['fim_exclusivo'].year == 2027, 'anual nao ampliou 12 meses'
                n_period = conn.execute(text('SELECT count(*) FROM public.ed02_assinatura_periodo WHERE empresa_id=1 AND payment_id = ANY(:ids)'), {'ids':test_ids}).scalar_one()
                assert n_period == 2, 'quantidade incorreta de periodos'
                n_ledger = conn.execute(text("SELECT count(*) FROM public.pagamento_evento WHERE provedor='mercadopago' AND payment_id = ANY(:ids)"), {'ids':test_ids}).scalar_one()
                assert n_ledger == 2, 'quantidade incorreta no ledger'
                now_company = conn.execute(text('SELECT status_pagamento,data_pagamento FROM public.empresa WHERE id=1')).one()
                assert tuple(now_company) == snapshot, 'adaptador mudou status de empresa'
                print('PASSOU: mensal e anual com periodo e ledger na mesma transacao.')
                print('PASSOU: replay nao duplica periodos; renovacao preserva vigencia.')
                print('PASSOU: empresa permanece pendente durante o ensaio.')
            finally:
                if tx.is_active:
                    tx.rollback()
                    print('PASSOU: rollback externo executado.')
                else:
                    print('ATENCAO: transacao ja revertida; verificar erro precedente.')
        with engine.connect() as check:
            assert check.execute(text('SELECT current_database()')).scalar_one() == DB
            assert check.execute(text('SELECT to_regclass(:t)'), {'t':TABLE}).scalar_one() is None, 'tabela residual'
            if test_ids:
                assert check.execute(text("SELECT count(*) FROM public.pagamento_evento WHERE provedor='mercadopago' AND payment_id = ANY(:ids)"), {'ids':test_ids}).scalar_one() == 0, 'ledger residual'
            company = check.execute(text('SELECT status_pagamento, data_pagamento FROM public.empresa WHERE id=1')).one()
            assert (company[0] or '').strip().lower() == 'pendente' and company[1] is None, 'empresa alterada'
            print('PASSOU: segunda conexao confirma zero residuos e empresa intacta.')
    finally:
        if old_flag is None:
            os.environ.pop('ED01_HOMOLOGACAO', None)
        else:
            os.environ['ED01_HOMOLOGACAO'] = old_flag
        engine.dispose()

if __name__ == '__main__':
    main()
