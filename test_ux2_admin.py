"""Regressões do painel administrativo em banco descartável local."""

import csv
from datetime import UTC, datetime
import importlib.util
import io
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest.mock import patch


class AdminUX2Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        pasta = tempfile.TemporaryDirectory()
        cls.addClassCleanup(pasta.cleanup)
        env = {
            'SQLALCHEMY_DATABASE_URI': f'sqlite:///{pasta.name}/admin.sqlite',
            'SECRET_KEY': 'teste-local-admin',
            'ADMIN_EMAIL': 'admin@example.invalid',
            'ADMIN_PASSWORD_HASH': 'verificado-no-teste-de-login',
        }
        nome = 'achetece_admin_test_app'
        spec = importlib.util.spec_from_file_location(nome, Path(__file__).with_name('main.py'))
        cls.app_module = importlib.util.module_from_spec(spec)
        sys.modules[nome] = cls.app_module
        cls.addClassCleanup(sys.modules.pop, nome, None)
        with patch.dict(os.environ, env, clear=True):
            spec.loader.exec_module(cls.app_module)
        cls.app_module.app.config.update(TESTING=True, SESSION_COOKIE_DOMAIN=None)
        cls.app_module._BOOTSTRAP_DONE = True
        cls.app_module._ANALYTICS_READY = True

    def setUp(self):
        m = self.app_module
        self.client = m.app.test_client()
        with m.app.app_context():
            m.db.drop_all()
            m.db.create_all()
            primeira = m.Empresa(
                nome='=SUM(2+2)', apelido='Fábrica Especial', email='teste-especial@example.invalid',
                senha='hash de teste', estado='SC', cidade='Jaraguá do Sul',
                plano='anual', status_pagamento='ativo',
                data_pagamento=datetime.now(UTC).replace(tzinfo=None),
            )
            outras = [
                m.Empresa(nome=f'Empresa {i:02}', email=f'i{i}@example.invalid',
                          senha='hash de teste', status_pagamento='pendente', estado='PR')
                for i in range(12)
            ]
            ultima = m.Empresa(nome='Z Malharia Remota', email='alvo@example.invalid', senha='hash de teste')
            m.db.session.add_all([primeira, *outras, ultima])
            m.db.session.flush()
            self.empresa_id = primeira.id
            m.db.session.add(m.Tear(
                marca='Marca', modelo='Modelo', tipo='MONO', finura=28,
                diametro=30, alimentadores=90, elastano='Não', empresa_id=primeira.id,
            ))
            m.db.session.commit()

    def autenticar(self):
        with self.client.session_transaction() as sessao:
            sessao['admin_email'] = self.app_module.ADMIN_EMAIL
            sessao['admin_auth_version'] = 'v2'

    def test_busca_e_exportacao_usam_o_universo_filtrado(self):
        self.assertEqual(self.client.get('/admin/empresas').status_code, 302)
        self.assertEqual(self.client.get('/admin/empresas/exportar').status_code, 302)
        self.autenticar()
        inicio = self.client.get('/admin/empresas')
        self.assertEqual(inicio.status_code, 200)
        self.assertNotIn('Z Malharia Remota', inicio.get_data(as_text=True))
        resultado = self.client.get('/admin/empresas?q=alvo')
        self.assertIn('Z Malharia Remota', resultado.get_data(as_text=True))
        self.assertNotIn('Empresa 01', resultado.get_data(as_text=True))
        pagina_invalida = self.client.get('/admin/empresas?pagina=inválida')
        self.assertEqual(pagina_invalida.status_code, 200)

        exportacao = self.client.get('/admin/empresas/exportar?estado=SC&plano=anual')
        self.assertEqual(exportacao.status_code, 200)
        self.assertEqual(exportacao.headers['Cache-Control'], 'no-store')
        linhas = list(csv.reader(io.StringIO(exportacao.data.decode('utf-8-sig'))))
        self.assertEqual(len(linhas), 2)
        self.assertEqual(linhas[1][0], "'=SUM(2+2)")
        self.assertEqual(linhas[1][1], 'Fábrica Especial')

    def test_arquivamento_exige_autorizacao_e_confirmacao_e_preserva_dados(self):
        ficha = f'/admin/empresas/{self.empresa_id}'
        rota = f'{ficha}/arquivar'
        self.assertEqual(self.client.post(rota).status_code, 302)
        self.autenticar()
        self.assertEqual(self.client.get(ficha).status_code, 200)
        with self.client.session_transaction() as sessao:
            token = sessao['admin_csrf']
        self.assertEqual(self.client.post(rota, data={'confirmar': 'sim'}).status_code, 400)
        self.assertEqual(self.client.post(rota, data={'csrf_token': token}).status_code, 400)
        legado = self.client.get(f'/admin/editar_status/{self.empresa_id}?status=pendente')
        self.assertEqual(legado.status_code, 410)

        for _ in range(2):
            response = self.client.post(rota, data={'csrf_token': token, 'confirmar': 'sim'})
            self.assertEqual(response.status_code, 302)
        m = self.app_module
        with m.app.app_context():
            empresa = m.db.session.get(m.Empresa, self.empresa_id)
            self.assertTrue(empresa.assinatura_ativa)
            self.assertEqual(empresa.status_pagamento, 'ativo')
            self.assertEqual(len(empresa.teares), 1)
            registro = m.EmpresaArquivamento.query.one()
            self.assertEqual(registro.empresa_id, empresa.id)
            self.assertEqual(registro.administrador, self.app_module.ADMIN_EMAIL)
        self.assertIn('Empresa arquivada em', self.client.get(ficha).get_data(as_text=True))
        lista = self.client.get('/admin/empresas?visibilidade=arquivada').get_data(as_text=True)
        self.assertIn('Fábrica Especial', lista)
        self.assertNotIn('Empresa 01', lista)

    def test_desarquivamento_mantem_historico_e_rejeita_confirmacao_antiga(self):
        m = self.app_module
        ficha = f'/admin/empresas/{self.empresa_id}'
        rota = f'{ficha}/desarquivar'
        self.assertEqual(self.client.post(rota).status_code, 302)
        self.autenticar()
        self.client.get(ficha)
        with self.client.session_transaction() as sessao:
            token = sessao['admin_csrf']
        dados = {'csrf_token': token, 'confirmar': 'sim'}
        with m.app.app_context():
            original = m.db.session.get(m.Empresa, self.empresa_id).data_pagamento
        for ciclo in (1, 2):
            self.assertEqual(self.client.post(f'{ficha}/arquivar', data=dados).status_code, 302)
            with m.app.app_context():
                ativo = m.db.session.get(m.EmpresaArquivamento, self.empresa_id)
                data = ativo.registrado_em.isoformat()
            form = dict(dados, arquivado_em=data)
            self.assertEqual(self.client.post(rota, data={'confirmar': 'sim'}).status_code, 400)
            self.assertEqual(self.client.post(rota, data={'csrf_token': token}).status_code, 400)
            self.assertEqual(self.client.post(rota, data=dict(form, arquivado_em='antigo')).status_code, 409)
            for _ in range(2):
                self.assertEqual(self.client.post(rota, data=form).status_code, 302)
            with m.app.app_context():
                self.assertIsNone(m.db.session.get(m.EmpresaArquivamento, self.empresa_id))
                self.assertEqual(m.EmpresaDesarquivamento.query.count(), ciclo)
                evento = m.EmpresaDesarquivamento.query.order_by(
                    m.EmpresaDesarquivamento.id.desc()).first()
                self.assertEqual(evento.arquivada_em.isoformat(), data)
                self.assertEqual(evento.arquivada_por, m.ADMIN_EMAIL)
                self.assertEqual(evento.desarquivada_por, m.ADMIN_EMAIL)
                empresa = m.db.session.get(m.Empresa, self.empresa_id)
                self.assertEqual((empresa.status_pagamento, empresa.data_pagamento), ('ativo', original))
                self.assertEqual(len(empresa.teares), 1)
            self.assertIn('Desarquivada em', self.client.get(ficha).get_data(as_text=True))


if __name__ == '__main__':
    unittest.main()
