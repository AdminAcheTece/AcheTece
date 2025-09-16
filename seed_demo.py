# seed_demo.py
# Popula e limpa dados DEMO para o AcheTece
# Requisitos: models Empresa e Tear no main.py

from datetime import datetime, timedelta
from werkzeug.security import generate_password_hash

from main import app, db
from main import Empresa, Tear  # ajuste caso seus modelos estejam em outro módulo

DEMO_TAG = "[DEMO]"
DEFAULT_DEMO_PASSWORD = "demo123"
DEFAULT_DEMO_HASH = generate_password_hash(DEFAULT_DEMO_PASSWORD)

# Defaults para campos obrigatórios de Tear em alguns projetos
DEFAULT_TEAR_MODELO = "DEMO-01"
DEFAULT_TEAR_TIPO = "circular"

# -------- Dados base --------
EMPRESAS = [
    # nome, apelido, estado, cidade, email
    ("Malharia Modelo",         f"{DEMO_TAG} Modelo",   "SC", "Blumenau",      "modelo@teste.com"),
    ("Fios & Malhas",           f"{DEMO_TAG} Fios",     "SP", "Americana",     "fios@teste.com"),
    ("TramaSul Têxteis",        f"{DEMO_TAG} TramaSul", "RS", "Caxias do Sul", "tramasul@teste.com"),
    ("Tecelagem Paraná",        f"{DEMO_TAG} Paraná",   "PR", "Maringá",       "parana@teste.com"),
    ("Nordeste Knit",           f"{DEMO_TAG} Nordeste", "CE", "Fortaleza",     "nordeste@teste.com"),
    ("Mineira Malharia",        f"{DEMO_TAG} Mineira",  "MG", "Juiz de Fora",  "mineira@teste.com"),
]

TEARES = {
    # apelido_empresa: lista de teares (marca, finura(int), diametro(int), alimentadores(int), elastano["Sim"/"Não"])
    f"{DEMO_TAG} Modelo":   [("Mayer", 28, 30,  90, "Sim"), ("Terrot", 32, 34,  96, "Não"), ("Pilotelli", 24, 26, 72, "Sim")],
    f"{DEMO_TAG} Fios":     [("Unitex",28, 30,  84, "Não"), ("Santoni",20, 22,  48, "Sim")],
    f"{DEMO_TAG} TramaSul": [("Terrot",34, 36, 108, "Sim"), ("Mayer", 18, 20,  36, "Não"), ("Pilotelli",26, 26, 64, "Sim")],
    f"{DEMO_TAG} Paraná":   [("Mayer", 28, 34,  96, "Sim"), ("Unitex",24, 30,  80, "Não")],
    f"{DEMO_TAG} Nordeste": [("Santoni",32,34, 100, "Sim"), ("Terrot",28, 30,  90, "Não"), ("Mayer",22, 26, 60, "Sim")],
    f"{DEMO_TAG} Mineira":  [("Pilotelli",28,30,  88, "Sim"), ("Terrot",24, 26, 68, "Não")],
}

# -------- Utilidades --------
def _safe_set(obj, **pairs):
    """Define apenas os atributos que existirem no modelo."""
    for k, v in pairs.items():
        if hasattr(obj, k):
            setattr(obj, k, v)

# -------- Funções principais --------
def seed():
    print(">> Iniciando SEED DEMO...")
    with app.app_context():
        # Evita duplicados por apelido e por email
        apelidos_alvo = [e[1] for e in EMPRESAS]
        existentes_por_apelido = {
            e.apelido for e in Empresa.query.filter(Empresa.apelido.in_(apelidos_alvo)).all()
        }
        emails_alvo = [e[4] for e in EMPRESAS]
        existentes_por_email = {
            e.email for e in Empresa.query.filter(Empresa.email.in_(emails_alvo)).all()
        }

        criadas = 0
        for (nome, apelido, estado, cidade, email) in EMPRESAS:
            if apelido in existentes_por_apelido or email in existentes_por_email:
                continue

            emp = Empresa(
                nome=nome,
                apelido=apelido,
                estado=estado,
                cidade=cidade,
                email=email,
                senha=DEFAULT_DEMO_HASH,   # <- resolve NOT NULL em empresa.senha
            )

            # Marca como ativa/paga se esses campos existirem no seu modelo
            _safe_set(
                emp,
                status_pagamento="aprovado",
                assinatura_ativa=True,
                ativo=True,
                plano_tipo="mensal",
                data_pagamento=datetime.utcnow(),
                data_validade=datetime.utcnow() + timedelta(days=365),
                telefone="(00) 0000-0000",
                responsavel_nome="Demo",
                responsavel_sobrenome="Seed",
            )

            db.session.add(emp)
            criadas += 1

        db.session.commit()
        print(f">> Empresas criadas: {criadas}")

        # Cria teares por empresa (sem duplicar se já houver algum)
        total_teares = 0
        for apelido, teares in TEARES.items():
            emp = Empresa.query.filter_by(apelido=apelido).first()
            if not emp:
                print(f"!! Empresa não encontrada para apelido {apelido}. Pulando.")
                continue

            ja = Tear.query.filter_by(empresa_id=emp.id).count()
            if ja > 0:
                print(f"-> {apelido}: já possui {ja} teares. Pulando para evitar duplicidade.")
                continue

            for (marca, finura, diametro, alimentadores, elastano) in teares:
                t = Tear(
                    empresa_id=emp.id,
                    marca=marca,
                    finura=int(finura),
                    diametro=int(diametro),
                    alimentadores=int(alimentadores),
                    elastano=elastano,
                )

                # Preenche campos obrigatórios extras se existirem (ex.: modelo NOT NULL)
                _safe_set(
                    t,
                    modelo=DEFAULT_TEAR_MODELO,
                    tipo=DEFAULT_TEAR_TIPO,
                    ativo=True,
                )

                db.session.add(t)
                total_teares += 1

        db.session.commit()
        print(f">> Teares criados: {total_teares}")

    print("✅ SEED DEMO concluído.")

def clear_demo():
    print(">> Removendo dados DEMO...")
    with app.app_context():
        # apaga teares das empresas DEMO primeiro (respeita FK)
        empresas_demo = Empresa.query.filter(Empresa.apelido.like(f"{DEMO_TAG}%")).all()
        emp_ids = [e.id for e in empresas_demo]
        if emp_ids:
            deletados_teares = Tear.query.filter(Tear.empresa_id.in_(emp_ids)).delete(synchronize_session=False)
            print(f">> Teares removidos: {deletados_teares}")

        deletadas_empresas = Empresa.query.filter(Empresa.apelido.like(f"{DEMO_TAG}%")).delete(synchronize_session=False)
        db.session.commit()
        print(f">> Empresas removidas: {deletadas_empresas}")

    print("✅ LIMPEZA DEMO concluída.")

if __name__ == "__main__":
    # 1) Popular:
    seed()
    # 2) Para limpar, comente a linha acima e descomente abaixo:
    # clear_demo()
