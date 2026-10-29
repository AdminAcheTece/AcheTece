"""ED02.3G.7C.3: gravação real, reversível, apenas no PostgreSQL de homologação.

Executar SOMENTE pelo workflow manual com ED02_HOMOLOG_DATABASE_URL.
Sem Mercado Pago, sem e-mails, sem processamento ou ativação financeira.
"""
import os
import uuid
from types import SimpleNamespace
from urllib.parse import urlparse

from sqlalchemy import create_engine, text
from sqlalchemy.orm import Session

from ed02_checkout_ledger import registrar_contratacao, registrar_resultado_preferencia

DB = 'achetece_ed01_test'
HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
EMPRESA_ID = 1
EMPRESA_NOME = 'ED01 Malharia Teste'


def guard_url(url):
    p = urlparse(url)
    if not (p.scheme in ('postgresql', 'postgresql+psycopg2')
            and p.hostname == HOST and p.path.lstrip('/') == DB
            and p.username and p.password and p.port in (None, 5432)
            and not p.fragment):
        raise RuntimeError('BLOQUEADO: URL nao corresponde ao PostgreSQL externo de homologacao')


def main():
    url = os.environ.get('ED02_HOMOLOG_DATABASE_URL', '')
    guard_url(url)
    # O guard do modulo requer homologacao. Forcamos somente neste processo isolado.
    os.environ['ED01_HOMOLOGACAO'] = 'true'
    engine = create_engine(url, pool_pre_ping=True, hide_parameters=True,
                           connect_args={'sslmode': 'require'}, echo=False)
    reference = f'achetece:{EMPRESA_ID}:{uuid.uuid4().hex}'
    preference = f'ED02-3G7C3-{uuid.uuid4().hex}'
    try:
        with engine.connect() as conn:
            outer = conn.begin()
            try:
                if conn.execute(text('SELECT current_database()')).scalar_one() != DB:
                    raise RuntimeError('BLOQUEADO: nome real do banco divergente')
                if conn.execute(text("SELECT to_regclass('public.contratacao_checkout')")).scalar_one() is None:
                    raise RuntimeError('BLOQUEADO: tabela contratacao_checkout ausente')
                company = conn.execute(text('''
                    SELECT nome, status_pagamento, data_pagamento
                    FROM public.empresa WHERE id=:id FOR UPDATE
                '''), {'id': EMPRESA_ID}).one_or_none()
                if (company is None or company.nome != EMPRESA_NOME
                        or company.status_pagamento != 'pendente' or company.data_pagamento is not None):
                    raise RuntimeError('BLOQUEADO: empresa ficticia nao corresponde ao estado esperado')
                initial = conn.execute(text('''
                    SELECT COUNT(*) FROM public.contratacao_checkout WHERE empresa_id=:id
                '''), {'id': EMPRESA_ID}).scalar_one()

                # Commits internos liberam SAVEPOINTS, mas NAO a transacao externa.
                with Session(bind=conn, join_transaction_mode='create_savepoint') as session:
                    db = SimpleNamespace(session=session, engine=engine)
                    registrar_contratacao(db, EMPRESA_ID, reference, 'mensal', '2.00')
                    row = conn.execute(text('''
                        SELECT empresa_id, plano, valor_esperado, moeda, status, preference_id
                        FROM public.contratacao_checkout WHERE external_reference=:ref
                    '''), {'ref': reference}).one()
                    assert (row.empresa_id, row.plano, str(row.valor_esperado), row.moeda.strip(),
                            row.status, row.preference_id) == (EMPRESA_ID, 'mensal', '2.00', 'BRL', 'criada', None), 'Contratacao nao foi registrada corretamente'

                    registrar_resultado_preferencia(db, reference, preference)
                    updated = conn.execute(text('''
                        SELECT empresa_id, plano, valor_esperado, moeda, status, preference_id
                        FROM public.contratacao_checkout WHERE external_reference=:ref
                    '''), {'ref': reference}).one()
                    assert (updated.empresa_id, updated.plano, str(updated.valor_esperado), updated.moeda.strip(),
                            updated.status, updated.preference_id) == (
                                EMPRESA_ID, 'mensal', '2.00', 'BRL', 'preferencia_criada', preference
                            ), 'Preferencia nao vinculada ou dados financeiros alterados'
                    try:
                        registrar_resultado_preferencia(db, reference, 'REPETIDA')
                    except RuntimeError:
                        pass
                    else:
                        raise AssertionError('Atualizacao repetida deveria ser bloqueada')
                    repeated = conn.execute(text('''
                        SELECT status, preference_id FROM public.contratacao_checkout
                        WHERE external_reference=:ref
                    '''), {'ref': reference}).one()
                    assert (repeated.status, repeated.preference_id) == ('preferencia_criada', preference), 'Repeticao alterou contratacao'
                final_count = conn.execute(text('''
                    SELECT COUNT(*) FROM public.contratacao_checkout WHERE empresa_id=:id
                '''), {'id': EMPRESA_ID}).scalar_one()
                assert final_count == initial + 1, 'Contagem interna inesperada'
                unchanged = conn.execute(text('''
                    SELECT nome, status_pagamento, data_pagamento FROM public.empresa WHERE id=:id
                '''), {'id': EMPRESA_ID}).one()
                assert unchanged == company, 'Dados financeiros da empresa foram alterados'
                print('PASSOU: contratacao real criada com referencia, empresa, plano e valor corretos.')
                print('PASSOU: preferencia associada sem alterar dados financeiros; repeticao bloqueada.')
                print('PASSOU: empresa ficticia permaneceu pendente, sem ativacao.')
            finally:
                # NUNCA chamar outer.commit() neste teste.
                outer.rollback()

        # Uma segunda conexao comprova que os commits do modulo nao persistiram.
        with engine.connect() as verify:
            if verify.execute(text('SELECT current_database()')).scalar_one() != DB:
                raise RuntimeError('BLOQUEADO: banco divergente na verificacao')
            residual = verify.execute(text('''
                SELECT COUNT(*) FROM public.contratacao_checkout
                WHERE external_reference=:ref OR preference_id=:pref
            '''), {'ref': reference, 'pref': preference}).scalar_one()
            assert residual == 0, 'FALHA CRITICA: registro ficticio persistiu'
            print('PASSOU: ROLLBACK confirmado em nova conexao; ZERO registros residuais.')
    finally:
        engine.dispose()


if __name__ == '__main__':
    main()
