import unittest
from ed02_5_v571_auditoria import audit, ROUTES

MINIMAL = '''
class Empresa:
    def assinatura_ativa(self):
        return self.status_pagamento == "ativo" and self.data_pagamento and self._dias_por_plano(self.plano)

@app.route("/")
def index():
    return q.filter(Empresa.assinatura_ativa)
@app.route("/exportar")
def exportar():
    return q.filter(Empresa.assinatura_ativa)
@app.route("/utils/empresas_json")
def utils_empresas_json():
    return q.filter(Empresa.assinatura_ativa)
@app.get("/empresa/<int:empresa_id>")
def empresa_perfil(empresa_id):
    return q.filter(Empresa.assinatura_ativa)
'''

class AuditTests(unittest.TestCase):
    def test_quatro_superficies_com_filtro(self):
        r=audit(MINIMAL)
        self.assertTrue(all(r[k]['pass'] for k in ROUTES))
    def test_csv_sem_filtro_bloqueia(self):
        s=MINIMAL.replace('def exportar():\n    return q.filter(Empresa.assinatura_ativa)', 'def exportar():\n    return q.all()')
        self.assertFalse(audit(s)['csv']['pass'])
    def test_json_sem_filtro_bloqueia(self):
        s=MINIMAL.replace('def utils_empresas_json():\n    return q.filter(Empresa.assinatura_ativa)', 'def utils_empresas_json():\n    return q.all()')
        self.assertFalse(audit(s)['json']['pass'])
    def test_home_filtro_em_comentario_nao_conta(self):
        s=MINIMAL.replace('def index():\n    return q.filter(Empresa.assinatura_ativa)', 'def index():\n    # Empresa.assinatura_ativa\n    return q.all()')
        self.assertFalse(audit(s)['home']['pass'])
    def test_perfil_sem_filtro_bloqueia(self):
        s=MINIMAL.replace('def empresa_perfil(empresa_id):\n    return q.filter(Empresa.assinatura_ativa)', 'def empresa_perfil(empresa_id):\n    return q.first()')
        self.assertFalse(audit(s)['perfil']['pass'])
    def test_reconhece_condicoes_legadas(self):
        r=audit(MINIMAL)
        self.assertTrue(all(r['ed01_contract'].values()))

if __name__=='__main__': unittest.main()
