"""Regressões HTTP usando PostgreSQL descartável no loopback."""

import csv
from datetime import UTC, datetime, timedelta
import importlib.util
import io
import os
from pathlib import Path
import sys
import unittest
from unittest.mock import patch
from urllib.parse import urlsplit


@unittest.skipUnless(os.getenv("ACHETECE_UX2_TEST_DSN"), "Requer PostgreSQL local descartável.")
class PublicoUX2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        dsn = os.environ["ACHETECE_UX2_TEST_DSN"]
        url = urlsplit(dsn)
        if (url.scheme != "postgresql+psycopg" or url.hostname != "127.0.0.1"
                or url.path != "/achetece_ux2_test" or url.query):
            raise RuntimeError("Use o banco achetece_ux2_test isolado no loopback.")
        nome = "achetece_publico_test_app"
        spec = importlib.util.spec_from_file_location(nome, Path(__file__).with_name("main.py"))
        cls.m = importlib.util.module_from_spec(spec)
        sys.modules[nome] = cls.m
        cls.addClassCleanup(sys.modules.pop, nome, None)
        with patch.dict(os.environ, {
            "SQLALCHEMY_DATABASE_URI": dsn, "SECRET_KEY": "teste-local-busca",
        }, clear=True):
            spec.loader.exec_module(cls.m)
        cls.m.app.config.update(TESTING=True, SESSION_COOKIE_DOMAIN=None)
        with cls.m.app.app_context():
            if cls.m.inspect(cls.m.db.engine).get_table_names():
                raise RuntimeError("O banco descartável precisa estar vazio antes do teste.")
            cls.m.db.create_all()
            cls.m._init_analytics_table()
        cls.m._BOOTSTRAP_DONE = True
        cls.m._ANALYTICS_READY = True

    def setUp(self):
        self.client = self.m.app.test_client()

    def test_busca_api_csv_perfil_e_painel(self):
        m = self.m
        dados = (
            ("Elegível", "ativo", 1),
            ("Pendente Oculta", "pendente", 1),
            ("Vencida Oculta", "ativo", 100),
            ("Bloqueada Oculta", "bloqueado", 1),
            ("Sem Vigência Oculta", "ativo", None),
            ("[DEMO] Oculta", "ativo", 1),
            ("Arquivada Oculta", "ativo", 1),
        )
        ids = []
        with m.app.app_context():
            for i, (nome, status, atraso) in enumerate(dados):
                email = f"malharia{i}@example.invalid"
                empresa = m.Empresa(
                    nome=nome, apelido=nome, senha="teste", email=email,
                    telefone="(47) 99999-0000", estado="SC", cidade="Jaraguá do Sul",
                    plano="mensal", status_pagamento=status,
                    data_pagamento=(datetime.now(UTC).replace(tzinfo=None) - timedelta(days=atraso)
                                    if atraso is not None else None),
                )
                m.db.session.add(empresa)
                m.db.session.flush()
                ids.append(empresa.id)
                m.db.session.add(m.Tear(
                    marca="=1+1", modelo="M", tipo="MONO", finura=28, diametro=30,
                    alimentadores=90, pistas_cilindro=4, elastano="Sim", empresa_id=empresa.id,
                ))
                if i == 0:
                    m.db.session.add(m.Tear(
                        marca="Marca", modelo="M", tipo="MONO", finura=28, diametro=30,
                        alimentadores=90, pistas_cilindro=4, elastano="Sim", empresa_id=empresa.id,
                    ))
            m.db.session.add(m.EmpresaArquivamento(empresa_id=ids[-1], administrador="teste"))
            m.db.session.commit()

        inicial = self.client.get("/").get_data(as_text=True)
        self.assertIn("Resultados da busca", inicial)
        self.assertNotIn("0 teares encontrados", inicial)
        self.assertNotIn("Elegível", inicial)
        params = {"estado": "SC", "cidade": "Jaraguá do Sul", "galga": "28",
                  "diâmetro": "30", "pistas_cilindro": "4", "elastano": "Sim", "pp": 1}
        busca = self.client.get("/", query_string=params).get_data(as_text=True)
        self.assertIn("2 teares encontrados", busca)
        self.assertIn("Elegível", busca)
        self.assertIn("Página 1 de 2", busca)
        self.assertIn("pistas_cilindro=4", busca)
        self.assertNotIn("Oculta", busca)
        pagina2 = self.client.get("/", query_string={**params, "pagina": 2}).get_data(as_text=True)
        self.assertIn("Página 2 de 2", pagina2)
        vazio = self.client.get("/?galga=99").get_data(as_text=True)
        self.assertIn("Nenhum tear encontrado", vazio)

        exportacao = self.client.get("/exportar", query_string=params)
        linhas = list(csv.reader(io.StringIO(exportacao.get_data(as_text=True))))
        self.assertEqual(len(linhas), 3)
        self.assertEqual(linhas[-1][1], "'=1+1")
        api = self.client.get("/utils/empresas_json?escopo=pagantes").get_json()
        self.assertEqual([empresa["id"] for empresa in api], ids[:1])
        self.assertEqual(self.client.get(f"/empresa/{ids[0]}").status_code, 200)
        contato = self.client.get(f"/empresa/{ids[0]}/whatsapp")
        self.assertEqual(contato.location, "https://wa.me/5547999990000")
        for id_invisivel in ids[1:]:
            self.assertEqual(self.client.get(f"/empresa/{id_invisivel}").status_code, 404)
            self.assertEqual(self.client.get(f"/empresa/{id_invisivel}/whatsapp").status_code, 404)

        self.assertEqual(self.client.get("/painel_malharia").status_code, 302)
        self.assertEqual(self.client.get("/admin/empresas").status_code, 302)
        with self.client.session_transaction() as sessao:
            sessao["empresa_id"] = ids[0]
        painel = self.client.get("/painel_malharia")
        self.assertEqual(painel.status_code, 200)
        self.assertIn("Publicado", painel.get_data(as_text=True))
        self.assertIn("no-store", painel.headers["Cache-Control"])
        with m.app.app_context():
            tear = m.Tear.query.filter_by(empresa_id=ids[0]).first().id
        self.assertEqual(self.client.post(f"/tear/{tear}/excluir").status_code, 400)
        for _ in range(13):
            contato = self.client.get(f"/empresa/{ids[0]}/whatsapp")
        self.assertEqual(contato.status_code, 302)
        self.assertEqual(self.client.get(f"/empresa/{ids[0]}/whatsapp").status_code, 429)


    def test_precos_comerciais_dependem_da_configuracao_do_checkout(self):
        rota = "/malharia_info"
        for mensal, anual, mostra in ((2.0, 2.0, False), (47.0, 2.0, False), (47.0, 479.4, True)):
            with self.subTest(mensal=mensal, anual=anual), patch.multiple(
                self.m, PLAN_MONTHLY=mensal, PLAN_YEARLY=anual,
            ):
                pagina = self.client.get(rota).get_data(as_text=True)
                self.assertEqual("R$47,00" in pagina, mostra)
                self.assertEqual("R$479,40" in pagina, mostra)
                self.assertNotIn("12x", pagina)
