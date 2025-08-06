from flask import Flask, render_template, request, redirect, url_for, flash, session, render_template_string
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
import os  # ✅ Adicione aqui
from werkzeug.security import generate_password_hash, check_password_hash
import csv
import io
import math
import re
import mercadopago
from datetime import datetime

from functools import wraps

def login_admin_requerido(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('admin_email') != 'gestao.achetece@gmail.com':
            flash('Acesso não autorizado.')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

app = Flask(__name__)
app.secret_key = 'S3cr3t_K3y_AcheTece_2025_test#flask!'
app.config['SQLALCHEMY_DATABASE_URI'] = 'sqlite:///banco.db'
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# Configurações de envio de e-mail
app.config['MAIL_SERVER'] = 'smtp.gmail.com'
app.config['MAIL_PORT'] = 587
app.config['MAIL_USE_TLS'] = True
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

mail = Mail(app)
db = SQLAlchemy(app)

def gerar_token(email):
    serializer = URLSafeTimedSerializer(app.secret_key)
    return serializer.dumps(email, salt='recupera-senha')

def enviar_email_recuperacao(email):
    token = gerar_token(email)
    link = url_for('redefinir_senha', token=token, _external=True)

    msg = Message(
        subject="Redefinição de Senha - AcheTece",
        sender=app.config['MAIL_USERNAME'],
        recipients=[email]
    )
    msg.body = f"""
Olá,

Recebemos uma solicitação para redefinir sua senha.

Clique no link abaixo para criar uma nova senha:
{link}

Este link é válido por 1 hora.

Se você não solicitou isso, ignore este e-mail.

Equipe AcheTece
"""
    mail.send(msg)


# SDK Mercado Pago
import os
sdk = mercadopago.SDK(os.getenv("MERCADO_PAGO_TOKEN"))

# MODELOS

class Empresa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    nome = db.Column(db.String(100), nullable=False, unique=True)
    apelido = db.Column(db.String(50), unique=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    senha = db.Column(db.String(200), nullable=False)
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    telefone = db.Column(db.String(20))
    status_pagamento = db.Column(db.String(20), default='pendente')
    data_pagamento = db.Column(db.DateTime)
    teares = db.relationship('Tear', backref='empresa', lazy=True)

class Tear(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    marca = db.Column(db.String(100), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    finura = db.Column(db.Integer, nullable=False)
    diametro = db.Column(db.Integer, nullable=False)
    alimentadores = db.Column(db.Integer, nullable=False)
    elastano = db.Column(db.String(10), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)

@app.route('/')
def index():
    teares = Tear.query.all()
    return render_template('index.html', teares=teares)

from werkzeug.security import check_password_hash

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        senha = request.form.get('senha', '')

        # Remove sessões anteriores
        session.pop('empresa_id', None)
        session.pop('admin', None)

        empresa = Empresa.query.filter_by(email=email).first()

        if empresa and check_password_hash(empresa.senha, senha):
            session['empresa_id'] = empresa.id

            # Verifica se é admin pelo e-mail
            if empresa.email == "gestao.achetece@gmail.com": # <--- altere aqui
                session['admin'] = True
            else:
                session['admin'] = False

            return redirect(url_for('painel_malharia'))

        else:
            erro = "E-mail ou senha incorretos. Tente novamente."
            return render_template('login.html', erro=erro)

    return render_template('login.html')

@app.route('/logout')
def logout():
    # Remove a sessão do usuário (malharia)
    session.pop('empresa_id', None)

    # Opcional: limpa toda a sessão (se quiser zerar tudo)
    # session.clear()

    # Redireciona para a página inicial (ou login, se preferir)
    return redirect(url_for('index'))

@app.route('/cadastrar_empresa', methods=['GET', 'POST'])
def cadastrar_empresa():
    estados = ['AC', 'AL', 'AM', 'AP', 'BA', 'CE', 'DF', 'ES', 'GO', 'MA', 'MG',
               'MS', 'MT', 'PA', 'PB', 'PE', 'PI', 'PR', 'RJ', 'RN', 'RO', 'RR',
               'RS', 'SC', 'SE', 'SP', 'TO']

    cidades = ['Blumenau', 'Brusque', 'Gaspar', 'Joinville', 'São Paulo', 'Rio de Janeiro', 'Jaraguá do Sul']

    if request.method == 'POST':
        nome = request.form['nome']
        apelido = request.form['apelido']
        email = request.form['email']
        senha = request.form['senha']
        cidade = request.form['cidade']
        estado = request.form['estado']
        telefone = request.form['telefone']

        dados = {
            'nome': nome,
            'apelido': apelido,
            'email': email,
            'cidade': cidade,
            'estado': estado,
            'telefone': telefone
        }

        erros = {}
        telefone_limpo = re.sub(r'\D', '', telefone)

        if len(telefone_limpo) < 10 or len(telefone_limpo) > 13:
            erros['telefone'] = 'Telefone inválido. Use apenas números com DDD. Ex: 47999991234'

        if Empresa.query.filter_by(nome=nome).first():
            erros['nome'] = 'Nome da empresa já existe.'

        if Empresa.query.filter_by(apelido=apelido).first():
            erros['apelido'] = 'Apelido já está em uso.'

        if Empresa.query.filter_by(email=email).first():
            erros['email'] = 'E-mail já cadastrado.'

        if estado not in estados:
            erros['estado'] = 'Estado inválido.'

        if cidade not in cidades:
            erros['cidade'] = 'Cidade inválida.'

        if erros:
            return render_template(
                'cadastrar_empresa.html',
                erro='Corrija os campos destacados abaixo.',
                erros=erros,
                estados=estados,
                cidades=cidades,
                **dados
            )

        nova_empresa = Empresa(
            nome=nome,
            apelido=apelido,
            email=email,
            senha=generate_password_hash(senha),
            cidade=cidade,
            estado=estado,
            telefone=telefone_limpo,
            status_pagamento='pendente'
        )
        db.session.add(nova_empresa)
        db.session.commit()

        session['empresa_id'] = nova_empresa.id
        return redirect(url_for('checkout'))

    return render_template('cadastrar_empresa.html', estados=estados, cidades=cidades)

@app.route('/checkout')
def checkout():
    if 'empresa_id' not in session:
        return redirect(url_for('login'))

    empresa = Empresa.query.get(session['empresa_id'])

    if not empresa:
        session.pop('empresa_id', None)
        return redirect(url_for('login'))

    success_url = "https://achetece.replit.app/painel_malharia"
    failure_url = "https://achetece.replit.app/planos"
    pending_url = "https://achetece.replit.app/painel_malharia"
    notify_url = "https://achetece.replit.app/webhook"

    preference_data = {
        "items": [{
            "title": "Assinatura mensal AcheTece",
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": 2.00
        }],
        "payer": {
            "email": empresa.email
        },
        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url
        },
        "auto_return": "approved",  # ✅ ESSENCIAL
        "notification_url": notify_url
    }

    try:
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response", {})
        init_point = preference.get("init_point")

        if not init_point:
            return f"<h2>Erro: 'init_point' ausente na resposta.<br><br>Detalhes: {preference}</h2>", 500

        return redirect(init_point)

    except Exception as e:
        return f"<h2>Erro ao iniciar pagamento: {e}</h2>", 500

# ✅ ROTA /PLANOS – EXIBIDA EM CASO DE FALHA NO PAGAMENTO
@app.route('/planos')
def planos():
    empresa = None
    if 'empresa_id' in session:
        empresa = Empresa.query.get(session['empresa_id'])
    return render_template('planos.html', empresa=empresa)

@app.route('/teste_email')
def teste_email():
    try:
        msg = Message(
            subject="Teste de envio - AcheTece",
            sender=os.getenv('MAIL_USERNAME'),
            recipients=["seu_email_destino@gmail.com"],  # pode usar o mesmo que cadastrou
            body="Este é um teste de envio de e-mail usando Flask-Mail e Replit Secrets."
        )
        mail.send(msg)
        return "<h2>E-mail enviado com sucesso!</h2>"
    except Exception as e:
        return f"<h2>Erro ao enviar e-mail: {e}</h2>"

@app.route('/editar_empresa', methods=['GET', 'POST'])
def editar_empresa():
    if 'empresa_id' not in session:
        return redirect(url_for('login'))

    empresa = Empresa.query.get(session['empresa_id'])

    if request.method == 'POST':
        empresa.nome = request.form['nome']
        empresa.apelido = request.form['apelido']
        empresa.email = request.form['email']
        empresa.cidade = request.form['cidade']
        empresa.estado = request.form['estado']
        telefone = request.form['telefone']

        telefone_limpo = re.sub(r'\D', '', telefone)
        if len(telefone_limpo) < 10 or len(telefone_limpo) > 13:
            return render_template('editar_empresa.html', empresa=empresa, erro='Telefone inválido. Use apenas números com DDD. Ex: 47999991234')

        empresa.telefone = telefone_limpo
        db.session.commit()

        flash('Alterações salvas com sucesso!')
        return redirect(url_for('painel_malharia'))

    return render_template('editar_empresa.html', empresa=empresa)

@app.route('/painel_malharia')
def painel_malharia():
    # Garante que o usuário está autenticado
    if 'empresa_id' not in session:
        return redirect(url_for('login'))

    empresa = Empresa.query.get(session['empresa_id'])

    # Verifica se a empresa existe
    if not empresa:
        session.pop('empresa_id', None)
        return redirect(url_for('login'))

    # Verifica se o pagamento está ativo
    if empresa.status_pagamento != 'ativo':
        return render_template('pagamento_pendente.html', empresa=empresa)

    # Carrega os teares vinculados à empresa, se houver
    teares = Tear.query.filter_by(empresa_id=empresa.id).all()

    return render_template('painel_malharia.html', empresa=empresa, teares=teares)

from datetime import datetime
from flask import request, jsonify
from flask_mail import Message

@app.route('/webhook', methods=['POST'])
def webhook():
    data = request.get_json()
    print("📡 Webhook recebido:")
    print(data)  # 🔍 Mostra o conteúdo bruto recebido

    if data and data.get("type") == "payment":
        payment_id = data["data"]["id"]
        print(f"🔍 Verificando pagamento ID: {payment_id}")

        try:
            payment = sdk.payment().get(payment_id)["response"]
            print("📄 Dados do pagamento:", payment)

            email = payment["payer"]["email"]
            print(f"👤 E-mail do pagador: {email}")
            print(f"💳 Status do pagamento: {payment['status']}")

            if payment["status"] in ["approved", "authorized"]:
                empresa = Empresa.query.filter_by(email=email).first()
                if empresa:
                    empresa.status_pagamento = "ativo"
                    empresa.data_pagamento = datetime.now()
                    db.session.commit()
                    print(f"✅ Pagamento confirmado para {email}")

                    # Envia e-mail de boas-vindas com link de acesso
                    try:
                        msg = Message(
                            subject="Seu acesso está liberado - AcheTece",
                            sender=app.config['MAIL_USERNAME'],
                            recipients=[email]
                        )
                        msg.body = f'''
Olá {empresa.nome},

Recebemos a confirmação do seu pagamento e sua malharia foi liberada com sucesso no AcheTece!

Agora você já pode acessar sua conta clicando no link abaixo:
🔗 https://achetece.replit.app/login

Após fazer login, acesse o painel da sua empresa e cadastre seus teares.

Qualquer dúvida, estamos à disposição.

Abraços,
Equipe AcheTece
'''
                        mail.send(msg)
                        print("📩 E-mail de confirmação enviado com sucesso.")
                    except Exception as e:
                        print("❌ Erro ao enviar e-mail:", e)
                else:
                    print(f"⚠️ Empresa não encontrada para e-mail: {email}")
            else:
                print(f"⚠️ Pagamento com status: {payment['status']} para {email}")

        except Exception as e:
            print(f"❌ Erro ao processar webhook: {e}")
    else:
        print("⚠️ Webhook recebido sem tipo 'payment'.")

    return jsonify({"status": "ok"}), 200

@app.route('/cadastrar_teares', methods=['GET', 'POST'])
def cadastrar_teares():
    if 'empresa_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        marca = request.form['marca']
        modelo = request.form['modelo']
        tipo = request.form['tipo']
        finura = request.form['finura']
        diametro = request.form['diametro']
        alimentadores = request.form['alimentadores']
        elastano = request.form['elastano']

        novo_tear = Tear(
            marca=marca,
            modelo=modelo,
            tipo=tipo,
            finura=finura,
            diametro=diametro,
            alimentadores=alimentadores,
            elastano=elastano,
            empresa_id=session['empresa_id']
        )
        db.session.add(novo_tear)
        db.session.commit()
        return redirect(url_for('painel_malharia'))
    return render_template('cadastrar_teares.html')

@app.route('/editar_tear/<int:id>', methods=['GET', 'POST'])
def editar_tear(id):
    tear = Tear.query.get_or_404(id)
    if 'empresa_id' not in session or tear.empresa_id != session['empresa_id']:
        return redirect(url_for('login'))

    if request.method == 'POST':
        tear.marca = request.form['marca']
        tear.modelo = request.form['modelo']
        tear.tipo = request.form['tipo']
        tear.finura = request.form['finura']
        tear.diametro = request.form['diametro']
        tear.alimentadores = request.form['alimentadores']
        tear.elastano = request.form['elastano']
        db.session.commit()
        return redirect(url_for('painel_malharia'))

    return render_template('editar_tear.html', tear=tear)

@app.route('/excluir_tear/<int:id>', methods=['POST'])
def excluir_tear(id):
    tear = Tear.query.get_or_404(id)
    if 'empresa_id' not in session or tear.empresa_id != session['empresa_id']:
        return redirect(url_for('login'))

    db.session.delete(tear)
    db.session.commit()
    return redirect(url_for('painel_malharia'))

@app.route('/esqueci_senha', methods=['GET', 'POST'])
def esqueci_senha():
    if request.method == 'POST':
        email = request.form.get('email')
        empresa = Empresa.query.filter_by(email=email).first()

        if empresa:
            try:
                # Gera token e link com domínio fixo
                token = gerar_token(email)
                dominio = os.getenv('APP_DOMAIN', 'achetece.replit.app')  # fallback
                link = url_for('redefinir_senha', token=token, _external=True)

                # 🔍 Debug no console
                print(f"[DEBUG] Link de redefinição gerado: {link}")

                msg = Message(
                    subject='Recuperação de Senha - AcheTece',
                    sender=app.config['MAIL_USERNAME'],
                    recipients=[email]
                )

                msg.html = render_template_string("""
                <html>
                  <body style="font-family: Arial, sans-serif; background-color: #f9f9f9; padding: 20px;">
                    <div style="max-width: 600px; margin: auto; background-color: #ffffff; padding: 30px; border-radius: 8px; box-shadow: 0 2px 6px rgba(0,0,0,0.05);">
                      <h2 style="color: #003bb3;">Redefinição de Senha</h2>
                      <p>Olá {{ nome }},</p>
                      <p>Recebemos uma solicitação para redefinir a senha da sua conta no AcheTece.</p>
                      <p>Para criar uma nova senha, clique no botão abaixo:</p>

                      <p style="text-align: center; margin: 30px 0;">
                        <a href="{{ link }}" target="_blank" style="background-color: #003bb3; color: #ffffff; text-decoration: none; padding: 14px 24px; border-radius: 6px; display: inline-block; font-weight: bold;">Redefinir Senha</a>
                      </p>

                      <p>Este link é válido por 1 hora. Se você não solicitou isso, pode ignorar este e-mail.</p>

                      <p style="margin-top: 40px;">Atenciosamente,<br>Equipe AcheTece</p>
                    </div>
                  </body>
                </html>
                """, nome=empresa.nome, link=link)

                mail.send(msg)
                return render_template('esqueci_senha.html', mensagem='📧 Instruções enviadas para seu e-mail.')

            except Exception as e:
                print("[ERRO ao enviar e-mail]", e)
                return render_template('esqueci_senha.html', erro='Erro ao enviar e-mail. Verifique as configurações.')

        return render_template('esqueci_senha.html', erro='E-mail não encontrado no sistema.')

    return render_template('esqueci_senha.html')
  
@app.route('/busca', methods=['GET', 'POST'])
def buscar_teares():
    filtros = {'tipo': '', 'diâmetro': '', 'galga': '', 'estado': '', 'cidade': ''}
    opcoes = {'tipo': [], 'diâmetro': [], 'galga': [], 'estado': [], 'cidade': []}

    todos_teares = Tear.query.join(Empresa).add_columns(
        Tear.tipo, Tear.diametro, Tear.finura,
        Empresa.estado, Empresa.cidade
    ).all()

    for tear in todos_teares:
        if tear.tipo not in opcoes['tipo']:
            opcoes['tipo'].append(tear.tipo)
        if str(tear.diametro) not in opcoes['diâmetro']:
            opcoes['diâmetro'].append(str(tear.diametro))
        if str(tear.finura) not in opcoes['galga']:
            opcoes['galga'].append(str(tear.finura))
        if tear.estado not in opcoes['estado']:
            opcoes['estado'].append(tear.estado)
        if tear.cidade not in opcoes['cidade']:
            opcoes['cidade'].append(tear.cidade)

    query = Tear.query.join(Empresa)
    if request.method == 'POST':
        for campo in filtros:
            valor = request.form.get(campo)
            filtros[campo] = valor
            if valor:
                if campo == 'tipo':
                    query = query.filter(Tear.tipo == valor)
                elif campo == 'diâmetro':
                    query = query.filter(Tear.diametro == int(valor))
                elif campo == 'galga':
                    query = query.filter(Tear.finura == int(valor))
                elif campo == 'estado':
                    query = query.filter(Empresa.estado == valor)
                elif campo == 'cidade':
                    query = query.filter(Empresa.cidade == valor)

    pagina = int(request.args.get('pagina', 1))
    por_pagina = 5
    total = query.count()
    total_paginas = math.ceil(total / por_pagina)
    teares_paginados = query.offset((pagina - 1) * por_pagina).limit(por_pagina).all()

    resultados = []
    for tear in teares_paginados:
        numero_telefone = re.sub(r'\D', '', tear.empresa.telefone or '')
        mensagem = f"Olá, encontrei seu tear no AcheTece e tenho demanda para esse tipo de máquina. Gostaria de conversar sobre possíveis serviços de tecelagem."

        resultados.append({
            'Empresa': tear.empresa.apelido,
            'Tipo': tear.tipo,
            'Diâmetro': tear.diametro,
            'Galga': tear.finura,
            'Alimentadores': tear.alimentadores,
            'Estado': tear.empresa.estado,
            'Cidade': tear.empresa.cidade,
            'Telefone': numero_telefone,
            'Mensagem': mensagem
        })

    return render_template('busca.html', opcoes=opcoes, filtros=filtros, resultados=resultados, pagina=pagina, total_paginas=total_paginas)

@app.route('/exportar')
def exportar():
    filtros = {'tipo': request.args.get('tipo', ''),
               'diâmetro': request.args.get('diâmetro', ''),
               'galga': request.args.get('galga', ''),
               'estado': request.args.get('estado', ''),
               'cidade': request.args.get('cidade', '')}

    query = Tear.query.join(Empresa)
    for campo, valor in filtros.items():
        if valor:
            if campo == 'tipo':
                query = query.filter(Tear.tipo == valor)
            elif campo == 'diâmetro':
                query = query.filter(Tear.diametro == int(valor))
            elif campo == 'galga':
                query = query.filter(Tear.finura == int(valor))
            elif campo == 'estado':
                query = query.filter(Empresa.estado == valor)
            elif campo == 'cidade':
                query = query.filter(Empresa.cidade == valor)

    teares = query.all()
    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Empresa', 'Tipo', 'Diâmetro', 'Galga', 'Alimentadores', 'Estado', 'Cidade'])

    for tear in teares:
        writer.writerow([
            tear.empresa.apelido,
            tear.tipo,
            tear.diametro,
            tear.finura,
            tear.alimentadores,
            tear.empresa.estado,
            tear.empresa.cidade
        ])

    output.seek(0)
    return send_file(io.BytesIO(output.read().encode('utf-8')),
                     mimetype='text/csv',
                     as_attachment=True,
                     download_name='teares_filtrados.csv')

from sqlalchemy import or_

@app.route('/malharia-info')
def malharia_info():
    return render_template('malharia_info.html')

@app.route('/admin/empresas', methods=['GET', 'POST'])
@login_admin_requerido
def admin_empresas():
    pagina = int(request.args.get('pagina', 1))
    por_pagina = 10

    # Inicializa variáveis
    status = ''
    data_inicio = ''
    data_fim = ''
    query = Empresa.query

    if request.method == 'POST':
        status = request.form.get('status', '')
        data_inicio = request.form.get('data_inicio', '')
        data_fim = request.form.get('data_fim', '')
        # Redireciona para GET com os filtros como parâmetros na URL
        return redirect(url_for('admin_empresas', pagina=1, status=status, data_inicio=data_inicio, data_fim=data_fim))
    else:
        # Requisição GET
        status = request.args.get('status', '')
        data_inicio = request.args.get('data_inicio', '')
        data_fim = request.args.get('data_fim', '')

        if status:
            query = query.filter(Empresa.status_pagamento == status)
        if data_inicio:
            data_inicio_dt = datetime.strptime(data_inicio, "%Y-%m-%d")
            query = query.filter(Empresa.data_pagamento >= data_inicio_dt)
        if data_fim:
            data_fim_dt = datetime.strptime(data_fim, "%Y-%m-%d")
            query = query.filter(Empresa.data_pagamento <= data_fim_dt)

    total = query.count()
    empresas = query.order_by(Empresa.nome).offset((pagina - 1) * por_pagina).limit(por_pagina).all()
    total_paginas = (total + por_pagina - 1) // por_pagina

    return render_template(
        'admin_empresas.html',
        empresas=empresas,
        pagina=pagina,
        total_paginas=total_paginas,
        status=status,
        data_inicio=data_inicio,
        data_fim=data_fim
    )

@app.route('/admin/excluir_empresa/<int:empresa_id>', methods=['POST'])
@login_admin_requerido
def excluir_empresa(empresa_id):
    if not session.get('admin_email') == 'gestao.achetece@gmail.com':
        flash('Acesso não autorizado.')
        return redirect(url_for('login'))

    empresa = Empresa.query.get_or_404(empresa_id)

    # Se quiser, adicione lógica para apagar os teares vinculados:
    # Tear.query.filter_by(empresa_id=empresa.id).delete()

    db.session.delete(empresa)
    db.session.commit()
    flash(f'Empresa "{empresa.razao_social}" excluída com sucesso!')
    return redirect(url_for('admin_empresas'))

@app.route('/redefinir_senha/<token>', methods=['GET', 'POST'])
def redefinir_senha(token):
    print(f"[DEBUG] Token recebido na rota: {token}")

    serializer = URLSafeTimedSerializer(app.secret_key)

    try:
        # Valida o token e extrai o e-mail
        email = serializer.loads(token, salt='recupera-senha')
        print(f"[DEBUG] E-mail extraído do token: {email}")
    except SignatureExpired as e:
        print("[ERRO] Token expirado:", e)
        flash("⏰ O link expirou. Solicite um novo.")
        return render_template("erro_token.html")
    except BadSignature as e:
        print("[ERRO] Token inválido:", e)
        flash("⚠️ O link é inválido ou já foi utilizado.")
        return render_template("erro_token.html")

    # Verifica se o e-mail extraído existe no banco
    empresa = Empresa.query.filter_by(email=email).first()
    if not empresa:
        print("[ERRO] Empresa não encontrada com o e-mail:", email)
        return "❌ Usuário não encontrado.", 404

    if request.method == 'POST':
        nova_senha = request.form['senha']
        empresa.senha = generate_password_hash(nova_senha)
        db.session.commit()
        flash('✅ Senha redefinida com sucesso! Faça login com a nova senha.')
        return redirect(url_for('login'))

    # Exibe o formulário para redefinir senha
    return render_template('redefinir_senha.html', token_valido=True)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')

        if email == 'gestao.achetece@gmail.com' and senha == '123adm@achetece':
            session['admin_email'] = email
            flash('Login de administrador realizado com sucesso.', 'success')  # ✅
            return redirect(url_for('admin_empresas'))
        else:
            flash('Email ou Senha incorreta.', 'error')  # ✅
            return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_email', None)
    flash('Você saiu do painel administrativo.')
    return redirect(url_for('index'))  # Altere 'index' conforme o nome da rota da sua página inicial

@app.route('/pagar', methods=['GET'])
def pagar():
    try:
        preference_data = {
            "items": [
                {
                    "title": "Plano Mensal AcheTece",
                    "quantity": 1,
                    "unit_price": 2.00,
                    "currency_id": "BRL"
                }
            ],
            "back_urls": {
                "success": "https://achetece.replit.app/pagamento_sucesso",
                "failure": "https://achetece.replit.app/pagamento_erro",
                "pending": "https://achetece.replit.app/pagamento_pendente"
            },
            "auto_return": "approved"
        }

        preference_response = sdk.preference().create(preference_data)
        preference = preference_response["response"]

        return redirect(preference["init_point"])

    except Exception as e:
        print(f"Erro ao gerar preferência de pagamento: {e}")
        return render_template("erro_pagamento.html")

@app.route('/pagamento_sucesso')
def pagamento_sucesso():
    return render_template("pagamento_sucesso.html")

@app.route('/pagamento_erro')
def pagamento_erro():
    return render_template("pagamento_erro.html")

@app.route('/pagamento_pendente')
def pagamento_pendente():
    return render_template("pagamento_pendente.html")

@app.route('/rota-teste')
def rota_teste():
    return "✅ A rota funciona!"

@app.route('/reset/<token>', methods=['GET', 'POST'])
def redefinir_token_test(token):
    return f"Token recebido: {token}"

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
