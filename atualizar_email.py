from main import app, db, Empresa  # importa tudo do seu main.py

with app.app_context():
    admin = Empresa.query.filter_by(email='vandrestein@gmail.com').first()

    if admin:
        admin.email = 'gestao.achetece@gmail.com'
        db.session.commit()
        print("✅ E-mail atualizado com sucesso!")
    else:
        print("❌ Administrador com e-mail 'vandrestein@gmail.com' não encontrado.")
