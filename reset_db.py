from main import db, app

with app.app_context():
    db.drop_all()    # Apaga todas as tabelas existentes
    db.create_all()  # Cria todas as tabelas com base nos modelos atuais

print("✅ Banco de dados reiniciado com sucesso.")