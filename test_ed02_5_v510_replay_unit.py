"""V5.10 — testes de fluxo legado sem rede/Flask/MP/PostgreSQL externo."""
from __future__ import annotations
import os
import unittest
from datetime import datetime, timezone, timedelta
from types import SimpleNamespace
from unittest.mock import patch
from sqlalchemy.dialects import postgresql

from ed02_5_v510_legacy_once import processar_legado_uma_vez_homolog, BloqueioV510


class Res:
    def __init__(self, rows=()):
        self.rows = list(rows)
    def mappings(self): return self
    def one_or_none(self): return self.rows[0] if self.rows else None
    def first(self): return self.rows[0] if self.rows else None
    def all(self): return self.rows
    def scalar_one(self): return self.rows[0]


class SessionFake:
    def __init__(self):
        self.company = {"id": 1, "status_pagamento": "pendente", "data_pagamento": None}
        self.ledger = []
        self.periods = []
        self.commits = 0
        self.rollbacks = 0
    def execute(self, stmt, params=None):
        q = " ".join(str(stmt).lower().split()); p = params or {}
        if "select current_database()" in q: return Res(["achetece_ed01_test"])
        if "from public.empresa" in q and "for update" in q:
            return Res([{"id": 1, "status": self.company["status_pagamento"],
                         "data_pagamento": self.company["data_pagamento"]}])
        if "from public.pagamento_evento" in q and "payment_id=:payment_id" in q:
            return Res([r.copy() for r in self.ledger if r["payment_id"] == p["payment_id"]])
        if "from public.pagamento_evento" in q and "external_reference=:external_reference" in q:
            return Res([r.copy() for r in self.ledger if r["external_reference"] == p["external_reference"]
                        and r["estado_processamento"] in ("aplicado_legado_v510", "aplicado_ed02", "periodo_ed025")])
        if "from public.pagamento_evento" in q and "estado_processamento in" in q:
            return Res([{"id": r["id"]} for r in self.ledger if r["estado_processamento"] in
                        ("aplicado_legado_v510", "aplicado_ed02", "periodo_ed025")])
        if "from public.ed02_assinatura_periodo" in q: return Res(self.periods)
        if q.startswith("insert into public.pagamento_evento"):
            self.ledger.append({"id": len(self.ledger)+1, **p, "estado_processamento": "aplicado_legado_v510",
                                "status_provedor": "approved"}); return Res()
        if q.startswith("update public.pagamento_evento"):
            for r in self.ledger:
                if r["id"] == p["id"]:
                    r.update(estado_processamento="aplicado_legado_v510", status_provedor="approved")
            return Res()
        if q.startswith("update public.empresa"):
            self.company.update(status_pagamento="ativo", data_pagamento=p["aprovacao_utc_naive"])
            return Res()
        raise AssertionError("SQL nao mapeado no teste: "+q)
    def commit(self): self.commits += 1
    def rollback(self): self.rollbacks += 1


class ReplayV510(unittest.TestCase):
    def setUp(self):
        self.s = SessionFake()
        self.db = SimpleNamespace(session=self.s,
                                  engine=SimpleNamespace(dialect=SimpleNamespace(name="postgresql")))
        self.env = patch.dict(os.environ, {"ED01_HOMOLOGACAO":"true", "ED02_V510_REPLAY_HOMOLOG":"true"})
        self.env.start(); self.addCleanup(self.env.stop)
        self.check = patch("ed02_5_v510_legacy_once.verify_payment_against_contract",
                           side_effect=lambda db,p,id: {"empresa_id":1})
        self.check.start(); self.addCleanup(self.check.stop)
        self.now = datetime.now(timezone.utc) - timedelta(days=3)
    def payment(self, ident="10001", ref=None, approved=None, status="approved"):
        return {"id":ident, "status":status,
                "external_reference":ref or "achetece:1:"+("a"*32),
                "currency_id":"BRL", "transaction_amount":"2.00",
                "date_approved":(approved or self.now).isoformat()}
    def call(self, *a, **kw):
        return processar_legado_uma_vez_homolog(self.db, self.payment(*a, **kw), 1)
    def test_primeiro_aprovado_aplica_uma_vez(self):
        r=self.call(); self.assertTrue(r["aplicado"]); self.assertTrue(r["primeira_ativacao"])
        self.assertEqual(len(self.s.ledger),1); self.assertEqual(self.s.commits,1)
    def test_replay_nao_altera_data_nem_comita(self):
        self.call(); original=self.s.company["data_pagamento"]
        r=self.call(); self.assertTrue(r["duplicado"])
        self.assertEqual(original,self.s.company["data_pagamento"]);self.assertEqual(self.s.commits,1)
    def test_novo_pagamento_distinto_renova(self):
        self.call(); a=self.s.company["data_pagamento"]
        r=self.call("10002", "achetece:1:"+("b"*32), self.now+timedelta(days=1))
        self.assertTrue(r["aplicado"]);self.assertFalse(r["primeira_ativacao"])
        self.assertGreater(self.s.company["data_pagamento"],a);self.assertEqual(self.s.commits,2)
    def test_checkout_consumido_por_outro_pagamento_bloqueia(self):
        self.call()
        with self.assertRaises(BloqueioV510): self.call("10002", approved=self.now+timedelta(days=1))
        self.assertEqual(self.s.commits,1)
    def test_empresa_historica_ativa_sem_ledger_exige_revisao(self):
        self.s.company.update(status_pagamento="ativo",data_pagamento=self.now.replace(tzinfo=None))
        r=self.call("10002", "achetece:1:"+("b"*32), self.now+timedelta(days=1))
        self.assertTrue(r["revisao_historica"]);self.assertEqual(self.s.commits,0)
    def test_data_anterior_nao_prorroga(self):
        self.call()
        r=self.call("10002", "achetece:1:"+("b"*32), self.now-timedelta(hours=1))
        self.assertTrue(r["revisao_historica"]);self.assertEqual(self.s.commits,1)
    def test_pagamento_pendente_nao_atualiza(self):
        r=self.call(status="pending");self.assertTrue(r["revisao_historica"])
        self.assertEqual(self.s.commits,0)
    def test_estorno_nao_atualiza(self):
        p=self.payment();p["transaction_amount_refunded"]="2.00"
        r=processar_legado_uma_vez_homolog(self.db,p,1)
        self.assertTrue(r["revisao_historica"]);self.assertEqual(self.s.commits,0)
    def test_sem_fuso_recusa(self):
        p=self.payment(); p["date_approved"]="2026-10-31T11:00:00"
        with self.assertRaises(BloqueioV510): processar_legado_uma_vez_homolog(self.db,p,1)
    def test_banco_invalido_bloqueado(self):
        self.s.execute=lambda s,p=None: Res(["achetece_db"])
        with self.assertRaises(BloqueioV510): self.call()
    def test_sem_flag_bloqueia(self):
        os.environ.pop("ED02_V510_REPLAY_HOMOLOG")
        with self.assertRaises(BloqueioV510): self.call()
    def test_conflito_de_id_bloqueia(self):
        self.s.ledger=[{"id":1,"payment_id":"10001","empresa_id":9,
           "external_reference":"achetece:9:"+("f"*32),
           "estado_processamento":"aplicado_legado_v510", "status_provedor":"approved"}]
        with self.assertRaises(BloqueioV510): self.call()
    def test_periodo_ed02_ja_consumiu_pagamento(self):
        self.s.periods=[{"id":99}]
        with self.assertRaises(BloqueioV510): self.call()
    def test_reembolso_provedor_bloqueia_observado(self):
        self.s.ledger=[{"id":1,"payment_id":"10001","empresa_id":1,
            "external_reference":"achetece:1:"+("a"*32),
            "estado_processamento":"observado_ed02", "status_provedor":"refunded"}]
        with self.assertRaises(BloqueioV510): self.call()

if __name__ == "__main__": unittest.main()
