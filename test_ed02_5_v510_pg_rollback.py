"""V5.10: ensaio PostgreSQL REAL — commits de savepoint com rollback externo.

Não chama Mercado Pago, webhook, email, templates ou Render. Todos os dados são
fictícios, isolados da segunda conexão. CI EXIGE commit gatilho + secret.
"""
from __future__ import annotations
import os
import uuid
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from urllib.parse import urlparse

HOST = "dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com"
BANCO = "achetece_ed01_test"
GATILHO = "ED02_RUN_REPLAY_ED025_V510_ROLLBACK"


def guarda(environ=None):
    e = os.environ if environ is None else environ
    if (e.get("ED02_V510_PG_RUN_AUTHORIZED") != "true" or
        e.get("ED02_V510_COMMIT_MESSAGE") != GATILHO or
        e.get("ED01_HOMOLOGACAO") != "true" or
        e.get("ED02_V510_REPLAY_HOMOLOG") != "true"):
        raise RuntimeError("V510: gatilho/ambiente nao autorizado")
    url=e.get("ED02_HOMOLOG_DATABASE_URL", "")
    p=urlparse(url)
    if not (p.scheme in ("postgresql", "postgresql+psycopg", "postgresql+psycopg2")
            and p.hostname == HOST and p.path.lstrip("/") == BANCO and p.username
            and p.password and p.port in (None,5432) and not p.fragment):
        raise RuntimeError("V510: destino PostgreSQL nao autorizado")
    return url


def executar():
    url=guarda()
    from sqlalchemy import create_engine, text
    from sqlalchemy.orm import Session
    from ed02_5_v510_legacy_once import processar_legado_uma_vez_homolog
    engine=create_engine(url,pool_pre_ping=True,connect_args={"connect_timeout":8})
    company_id=None
    pids=[]
    refs=[]
    baseline=None
    outer=None
    conn=None
    try:
        conn=engine.connect()
        outer=conn.begin()
        if conn.execute(text("SELECT current_database()")).scalar_one()!=BANCO:
            raise RuntimeError("V510: conexao divergente")
        # Baseline da única malharia de ensaio anterior. Nenhuma alteracao nela.
        baseline=conn.execute(text("""
            SELECT status_pagamento, data_pagamento FROM public.empresa WHERE id=1
        """)).mappings().one()
        assert baseline["status_pagamento"] == "pendente" and baseline["data_pagamento"] is None

        stamp=uuid.uuid4().hex
        company_id=conn.execute(text("""
            INSERT INTO public.empresa(nome,email,senha,plano,status_pagamento,data_pagamento)
            VALUES (:nome,:email,'senha_sintetica','mensal','pendente',NULL)
            RETURNING id
        """), {"nome":"V510_FIXTURE_"+stamp,
                "email":"v510_"+stamp+"@example.invalid"}).scalar_one()
        now=datetime.now(timezone.utc)
        for i in (0,1):
            ref=f"achetece:{company_id}:{uuid.uuid4().hex}"
            refs.append(ref)
            conn.execute(text("""
                INSERT INTO public.contratacao_checkout
                  (empresa_id,external_reference,plano,valor_esperado,moeda,
                   status,provedor,preference_id)
                VALUES (:id,:ref,'mensal',2.00,'BRL','preferencia_criada',
                        'mercadopago',:pref)
            """), {"id":company_id,"ref":ref,"pref":"v510_"+uuid.uuid4().hex})
            pids.append(str(uuid.uuid4().int % (10**24)))
        assert pids[0]!=pids[1]
        # Fixa data no passado para permitir renovação real por outro payment_id.
        t0=now-timedelta(days=2)
        t1=now-timedelta(days=1)
        pagamento=lambda k,inst: {"id":pids[k], "status":"approved",
                         "external_reference":refs[k], "currency_id":"BRL",
                         "transaction_amount":"2.00", "date_approved":inst.isoformat()}
        # Sessions montadas por savepoint: Session.commit() não confirma outer.
        session=Session(bind=conn,join_transaction_mode="create_savepoint",future=True)
        db=SimpleNamespace(engine=engine,session=session)
        try:
            r1=processar_legado_uma_vez_homolog(db,pagamento(0,t0),company_id)
            assert r1["aplicado"] and r1["primeira_ativacao"]
            data1=session.execute(text("SELECT data_pagamento FROM public.empresa WHERE id=:id"),{"id":company_id}).scalar_one()
            replay=processar_legado_uma_vez_homolog(db,pagamento(0,t0),company_id)
            data2=session.execute(text("SELECT data_pagamento FROM public.empresa WHERE id=:id"),{"id":company_id}).scalar_one()
            assert replay["duplicado"] and data1 == data2
            renewal=processar_legado_uma_vez_homolog(db,pagamento(1,t1),company_id)
            data3=session.execute(text("SELECT data_pagamento FROM public.empresa WHERE id=:id"),{"id":company_id}).scalar_one()
            assert renewal["aplicado"] and data3 > data1
            count=session.execute(text("SELECT count(*) FROM public.pagamento_evento WHERE empresa_id=:id"),{"id":company_id}).scalar_one()
            assert count == 2
            # Segunda conexão não enxerga nem empresa nem ledger (outer sem commit).
            with engine.connect() as probe:
                probe.exec_driver_sql("SET TRANSACTION READ ONLY")
                assert probe.execute(text("SELECT count(*) FROM public.empresa WHERE id=:id"),{"id":company_id}).scalar_one()==0
                assert probe.execute(text("SELECT count(*) FROM public.pagamento_evento WHERE empresa_id=:id"),{"id":company_id}).scalar_one()==0
            print("PASSOU: primeiro pagamento aplicado; replay sem prorrogacao; novo checkout renovou.")
            print("PASSOU: segunda conexao nao enxerga fixtures antes do rollback.")
        finally:
            session.close()
    finally:
        if outer is not None and outer.is_active:
            outer.rollback()
        if conn is not None: conn.close()
        if company_id is not None:
            with engine.connect() as probe:
                probe.exec_driver_sql("SET TRANSACTION READ ONLY")
                from sqlalchemy import text
                assert probe.execute(text("SELECT count(*) FROM public.empresa WHERE id=:id"),{"id":company_id}).scalar_one()==0
                assert probe.execute(text("SELECT count(*) FROM public.pagamento_evento WHERE empresa_id=:id"),{"id":company_id}).scalar_one()==0
                assert probe.execute(text("SELECT count(*) FROM public.contratacao_checkout WHERE empresa_id=:id"),{"id":company_id}).scalar_one()==0
                original=probe.execute(text("SELECT status_pagamento,data_pagamento FROM public.empresa WHERE id=1")).mappings().one()
                assert original["status_pagamento"]==baseline["status_pagamento"]
                assert original["data_pagamento"]==baseline["data_pagamento"]
            print("PASSOU: rollback externo; zero residuos; empresa original intacta.")
        engine.dispose()


if __name__ == "__main__": executar()
