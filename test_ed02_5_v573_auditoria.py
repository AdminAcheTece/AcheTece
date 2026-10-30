import unittest
from ed02_5_v573_auditoria import auditar

BASE = '''
def index():
    query.filter(Empresa.assinatura_ativa)
def exportar():
    query.filter(Empresa.assinatura_ativa)
def utils_empresas_json():
    query.filter(Empresa.assinatura_ativa)
def empresa_perfil(empresa_id):
    if not empresa.assinatura_ativa:
        abort(404)
'''

class V573Auditoria(unittest.TestCase):
    def test_base_com_guarda(self):
        self.assertEqual(auditar(BASE), [])

    def test_csv_sem_guarda(self):
        self.assertIn('exportar: SEM_FILTRO_ASSINATURA', auditar(BASE.replace('query.filter(Empresa.assinatura_ativa)', 'query.all()', 1).replace('def exportar():\n    query.filter(Empresa.assinatura_ativa)', 'def exportar():\n    query.all()')))

    def test_json_sem_guarda(self):
        self.assertIn('utils_empresas_json: SEM_FILTRO_ASSINATURA', auditar(BASE.replace('def utils_empresas_json():\n    query.filter(Empresa.assinatura_ativa)', 'def utils_empresas_json():\n    query.all()')))

    def test_perfil_sem_guarda(self):
        self.assertIn('empresa_perfil: SEM_GUARDA_ASSINATURA', auditar(BASE.replace('if not empresa.assinatura_ativa:', 'if not empresa:')))

    def test_rota_ausente(self):
        self.assertIn('exportar: ROTA_AUSENTE', auditar('def index(): pass\ndef utils_empresas_json(): pass\ndef empresa_perfil(a): pass'))

    def test_fallback_aberto(self):
        self.assertIn('index: FALLBACK_POTENCIALMENTE_ABERTO', auditar(BASE.replace('query.filter(Empresa.assinatura_ativa)', 'query.filter(Empresa.assinatura_ativa)\n    x = "segue sem o filtro"', 1)))

if __name__ == '__main__':
    unittest.main()
