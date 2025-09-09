from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    render_template_string, send_file, jsonify
)
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime
import mercadopago
import os
import csv
import io
import math
import re
import uuid
import json
import logging
from unicodedata import normalize

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')  # ajuste para 'estático' se o seu folder tem acento
CACHE_DIR = os.path.join(BASE_DIR, 'cache_ibge')

os.makedirs(CACHE_DIR, exist_ok=True)

def _norm(s: str) -> str:
    # normaliza para comparar sem acento/variação
    return normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII').strip().lower()


# ========= NOVO: Authlib (OAuth Google) =========
from authlib.integrations.flask_client import OAuth
# ===============================================

# ---------------------------
# Helpers e Configurações
# ---------------------------

def login_admin_requerido(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('admin_email') != 'gestao.achetece@gmail.com':
            flash('Acesso não autorizado.')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

def base_url():
    """Retorna a URL base preferindo APP_BASE_URL (Render)."""
    env_url = os.getenv('APP_BASE_URL')
    if env_url:
        return env_url.rstrip('/')
    try:
        return request.url_root.rstrip('/')
    except Exception:
        return "http://localhost:5000"

# App
app = Flask(__name__)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-unsafe')

# DB (SQLite local por padrão; use DATABASE_URL no Render se quiser Postgres)
app.config['SQLALCHEMY_DATABASE_URI'] = os.getenv('DATABASE_URL', 'sqlite:///banco.db')
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False

# E-mail (ajuste no Render)
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

mail = Mail(app)
db = SQLAlchemy(app)

# ========= NOVO: Configuração OAuth Google =========
oauth = OAuth(app)

# Preferimos usar o discovery document do Google (OIDC)
google = oauth.register(
    name='google',
    client_id=os.getenv("GOOGLE_CLIENT_ID"),
    client_secret=os.getenv("GOOGLE_CLIENT_SECRET"),
    server_metadata_url='https://accounts.google.com/.well-known/openid-configuration',
    client_kwargs={
        'scope': 'openid email profile',
        # Para garantir que o consent apareça quando necessário e termos refresh_token:
        'prompt': 'consent',
        'access_type': 'offline'
    }
)
# ===================================================

# Mercado Pago SDK
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN") or os.getenv("MERCADO_PAGO_TOKEN", "")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)

# Preço do plano (opcional via env; default 2.00 para teste)
PLAN_MONTHLY = float(os.getenv("PLAN_MONTHLY", "2.00"))
PLAN_YEARLY = float(os.getenv("PLAN_YEARLY", "2.00"))  # mantido default para não quebrar

# ---------------------------
# Utilitários
# ---------------------------

def gerar_token(email):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    return serializer.dumps(email, salt='recupera-senha')

def enviar_email_recuperacao(email, nome_empresa=""):
    token = gerar_token(email)
    link = f"{base_url()}{url_for('redefinir_senha', token=token)}"

    msg = Message(
        subject="Redefinição de Senha - AcheTece",
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
    """, nome=nome_empresa or email, link=link)

    mail.send(msg)

# ---------------------------
# Modelos
# ---------------------------

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
    responsavel_nome = db.Column(db.String(120))
    responsavel_sobrenome = db.Column(db.String(120))
    # Campos opcionais citados no webhook (guardados se existirem)
    # mercadopago_payment_id = db.Column(db.String(64))
    # mercadopago_reference   = db.Column(db.String(128))

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

# ---------------------------
# Rotas
# ---------------------------

@app.route('/')
def index():
    teares = Tear.query.all()
    return render_template('index.html', teares=teares)

# ========= NOVO: Login com Google =========
@app.route('/login/google')
def login_google():
    # Guarda "next" se foi passado, para redirecionar após login
    nxt = request.args.get('next')
    if nxt:
        session['login_next'] = nxt
    # Define a URL absoluta de callback
    redirect_uri = f"{base_url()}{url_for('authorize_google')}"
    return google.authorize_redirect(redirect_uri)

@app.route('/callback/google')
def authorize_google():
    # Obtém token e dados do usuário
    try:
        token = google.authorize_access_token()
        userinfo = google.get('userinfo').json()  # OIDC userinfo
    except Exception as e:
        app.logger.exception(f"[GOOGLE OAUTH] Falha na autorização: {e}")
        flash("Falha ao autenticar com o Google. Tente novamente.", "error")
        return redirect(url_for('login'))

    email = (userinfo.get('email') or '').strip().lower()
    nome_completo = (userinfo.get('name') or '').strip()

    if not email:
        flash("Não foi possível obter o e-mail do Google.", "error")
        return redirect(url_for('login'))

    # Se já existe empresa com esse e-mail, loga
    empresa = Empresa.query.filter_by(email=email).first()

    if not empresa:
        # Cria um registro mínimo para a empresa (status pendente por padrão)
        apelido_sugestao = None
        # tenta fazer um apelido simples do nome
        if nome_completo:
            apelido_sugestao = re.sub(r'[^A-Za-zÀ-ÿ0-9 ]', '', nome_completo).strip()
            if apelido_sugestao:
                # limita tamanho e evita conflito
                apelido_sugestao = apelido_sugestao[:50]
                if Empresa.query.filter_by(apelido=apelido_sugestao).first():
                    apelido_sugestao = None

        senha_aleatoria = generate_password_hash(uuid.uuid4().hex[:12])

        # Garante nome único (campo nome é unique=True)
        nome_emp = nome_completo or email.split('@')[0]
        base_nome = nome_emp
        sufixo = 1
        while Empresa.query.filter_by(nome=nome_emp).first():
            sufixo += 1
            nome_emp = f"{base_nome}-{sufixo}"

        empresa = Empresa(
            nome=nome_emp,
            apelido=apelido_sugestao,
            email=email,
            senha=senha_aleatoria,
            status_pagamento='pendente',
            responsavel_nome=nome_completo.split(' ')[0] if nome_completo else None,
            responsavel_sobrenome=' '.join(nome_completo.split(' ')[1:]) if len(nome_completo.split(' ')) > 1 else None
        )
        db.session.add(empresa)
        db.session.commit()

        flash("Conta criada via Google. Complete seus dados no painel.", "success")

    # Autentica sessão do usuário
    session['empresa_id'] = empresa.id
    session['admin'] = (empresa.email == "gestao.achetece@gmail.com")

    # Redireciona para o destino salvo ou painel
    dest = session.pop('login_next', None) or url_for('painel_malharia')
    return redirect(dest)
# =========================================

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form.get('email', '').strip().lower()
        senha = request.form.get('senha', '')

        session.pop('empresa_id', None)
        session.pop('admin', None)

        empresa = Empresa.query.filter_by(email=email).first()
        if empresa and check_password_hash(empresa.senha, senha):
            session['empresa_id'] = empresa.id
            session['admin'] = (empresa.email == "gestao.achetece@gmail.com")
            return redirect(url_for('painel_malharia'))
        else:
            erro = "E-mail ou senha incorretos. Tente novamente."
            return render_template('login.html', erro=erro)

    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('empresa_id', None)
    return redirect(url_for('index'))

@app.route('/cadastrar_empresa', methods=['GET', 'POST'])
def cadastrar_empresa():
    # Lista de UFs permanece fixa
    estados = [
        'AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
        'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO'
    ]

    if request.method == 'POST':
        nome = request.form['nome']
        apelido = request.form['apelido']
        email = request.form['email'].lower().strip()
        senha = request.form['senha']
        cidade = request.form['cidade']
        estado = request.form['estado']
        telefone = request.form['telefone']
        responsavel_nome = (request.form.get('responsavel_nome') or '').strip()
        responsavel_sobrenome = (request.form.get('responsavel_sobrenome') or '').strip()

        dados = {
            'nome': nome, 'apelido': apelido, 'email': email,
            'cidade': cidade, 'estado': estado, 'telefone': telefone,
            'responsavel_nome': responsavel_nome, 'responsavel_sobrenome': responsavel_sobrenome
        }

        erros = {}
        telefone_limpo = re.sub(r'\D', '', telefone or '')

        # --- validações existentes ---
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

        # --- validação da cidade com base no UF selecionado ---
        try:
            cidades_validas = _get_cidades_por_uf(estado)  # usa arquivo/cached/IBGE
        except Exception as e:
            app.logger.warning(f"Falha ao obter cidades para UF={estado}: {e}")
            cidades_validas = []

        if cidades_validas:
            if cidade not in cidades_validas:
                erros['cidade'] = 'Cidade inválida.'
        else:
            app.logger.warning(f"Nenhuma cidade carregada para UF={estado}. Pulando validação estrita de cidade.")

        if not responsavel_nome or len(re.sub(r'[^A-Za-zÀ-ÿ]', '', responsavel_nome)) < 2:
            erros['responsavel_nome'] = 'Informe ao menos o primeiro nome do responsável.'

        if erros:
            return render_template(
                'cadastrar_empresa.html',
                erro='Corrija os campos destacados abaixo.',
                erros=erros,
                estados=estados,
                **dados
            )

        # --- criação do registro ---
        nova_empresa = Empresa(
            nome=nome,
            apelido=apelido,
            email=email,
            senha=generate_password_hash(senha),
            cidade=cidade,
            estado=estado,
            telefone=telefone_limpo,
            status_pagamento='pendente',
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=responsavel_sobrenome or None
        )
        db.session.add(nova_empresa)
        db.session.commit()

        session['empresa_id'] = nova_empresa.id
        return redirect(url_for('checkout'))

    return render_template('cadastrar_empresa.html', estados=estados)

from uuid import uuid4
from flask import request, redirect, url_for

@app.route('/checkout')
def checkout():
    # --- Autenticação de sessão ---
    if 'empresa_id' not in session:
        return redirect(url_for('login'))

    empresa = Empresa.query.get(session['empresa_id'])
    if not empresa:
        session.pop('empresa_id', None)
        return redirect(url_for('login'))

    # --- URLs de retorno e webhook ---
    base = base_url()
    success_url = f"{base}/pagamento_aprovado"
    failure_url = f"{base}/pagamento_erro"
    pending_url = f"{base}/pagamento_pendente"
    notify_url  = f"{base}/webhook"

    # --- Identificação única e estável da empresa ---
    ext_ref = f"achetece:{empresa.id}:{uuid4().hex}"

    first_name = (getattr(empresa, 'responsavel_nome', '') or '').strip() or empresa.email.split('@')[0]
    last_name  = (getattr(empresa, 'responsavel_sobrenome', '') or '').strip()

    # --- plano ---
    plano = (request.args.get('plano') or 'mensal').lower()
    if plano not in ('mensal', 'anual'):
        plano = 'mensal'

    if plano == 'anual':
        titulo_plano = "Assinatura anual AcheTece"
        preco = float(PLAN_YEARLY)
    else:
        titulo_plano = "Assinatura mensal AcheTece"
        preco = float(PLAN_MONTHLY)

    # --- Preferência do Checkout Pro ---
    preference_data = {
        "items": [{
            "title": titulo_plano,
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": preco
        }],
        "payer": {
            "name": first_name,
            "surname": last_name,
            "first_name": first_name,
            "last_name": last_name,
            "email": empresa.email
        },
        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url
        },
        "auto_return": "approved",
        "notification_url": notify_url,
        "external_reference": ext_ref,
        "metadata": {
            "empresa_id": int(empresa.id),
            "empresa_email": empresa.email,
            "plano": plano
        },
        "statement_descriptor": "AcheTece"
    }

    try:
        app.logger.info(
            f"[CHECKOUT] Criando preferência | empresa_id={empresa.id} | email={empresa.email} "
            f"| plano={plano} | price={preco:.2f} | notify={notify_url} | ext_ref={ext_ref}"
        )
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response", {}) if isinstance(preference_response, dict) else {}
        init_point = preference.get("init_point")

        if not init_point:
            app.logger.error(f"[CHECKOUT] init_point ausente. Response: {preference}")
            return f"<h2>Erro: 'init_point' ausente na resposta.<br><br>Detalhes: {preference}</h2>", 500

        return redirect(init_point)

    except Exception as e:
        app.logger.exception(f"[CHECKOUT] Erro ao iniciar pagamento: {e}")
        return f"<h2>Erro ao iniciar pagamento: {e}</h2>", 500

@app.route('/planos')
def planos():
    empresa = Empresa.query.get(session['empresa_id']) if 'empresa_id' in session else None
    return render_template('planos.html', empresa=empresa)

@app.route('/teste_email')
def teste_email():
    try:
        msg = Message(
            subject="Teste de envio - AcheTece",
            sender=app.config['MAIL_USERNAME'],
            recipients=[os.getenv('TEST_EMAIL', 'seu_email_destino@gmail.com')],
            body=f"Teste de e-mail do AcheTece ({base_url()})."
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
    if not empresa:
        session.pop('empresa_id', None)
        return redirect(url_for('login'))

    estados = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
               'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']
    cidades = ['Blumenau','Brusque','Gaspar','Joinville','São Paulo','Rio de Janeiro','Jaraguá do Sul']

    if request.method == 'GET':
        return render_template('editar_empresa.html',
                               estados=estados, cidades=cidades,
                               nome=empresa.nome or '', apelido=empresa.apelido or '',
                               email=empresa.email or '', cidade=empresa.cidade or '',
                               estado=empresa.estado or '', telefone=empresa.telefone or '',
                               responsavel_nome=(empresa.responsavel_nome or ''),
                               responsavel_sobrenome=(empresa.responsavel_sobrenome or ''))

    # POST
    nome = request.form.get('nome', '').strip()
    apelido = request.form.get('apelido', '').strip()
    email = request.form.get('email', '').strip().lower()
    senha = request.form.get('senha', '').strip()
    cidade = request.form.get('cidade', '').strip()
    estado = request.form.get('estado', '').strip()
    telefone = request.form.get('telefone', '').strip()
    responsavel_nome = (request.form.get('responsavel_nome') or '').strip()
    responsavel_sobrenome = (request.form.get('responsavel_sobrenome') or '').strip()

    dados = {'nome': nome, 'apelido': apelido, 'email': email, 'cidade': cidade,
             'estado': estado, 'telefone': telefone,
             'responsavel_nome': responsavel_nome, 'responsavel_sobrenome': responsavel_sobrenome}

    erros = {}
    telefone_limpo = re.sub(r'\D', '', telefone) if telefone else ''

    if telefone and (len(telefone_limpo) < 10 or len(telefone_limpo) > 13):
        erros['telefone'] = 'Telefone inválido. Use apenas números com DDD. Ex: 47999991234'

    if nome and nome != (empresa.nome or '') and Empresa.query.filter_by(nome=nome).first():
        erros['nome'] = 'Nome da empresa já existe.'
    if apelido and apelido != (empresa.apelido or '') and Empresa.query.filter_by(apelido=apelido).first():
        erros['apelido'] = 'Apelido já está em uso.'
    if email and email != (empresa.email or '') and Empresa.query.filter_by(email=email).first():
        erros['email'] = 'E-mail já cadastrado.'

    if estado and estado not in estados:
        erros['estado'] = 'Estado inválido.'
    if cidade and cidade not in cidades:
        erros['cidade'] = 'Cidade inválida.'

    if not responsavel_nome or len(re.sub(r'[^A-Za-zÀ-ÿ]', '', responsavel_nome)) < 2:
        erros['responsavel_nome'] = 'Informe ao menos o primeiro nome do responsável.'
    if not email or not re.fullmatch(r'[^@]+@[^@]+\.[^@]+', email):
        erros['email'] = 'E-mail inválido.'
    if senha and len(senha) < 6:
        erros['senha'] = 'A nova senha deve ter pelo menos 6 caracteres.'

    if erros:
        return render_template('editar_empresa.html',
                               erro='Corrija os campos destacados abaixo.',
                               erros=erros, estados=estados, cidades=cidades, **dados)

    empresa.nome = nome or empresa.nome
    empresa.apelido = apelido or empresa.apelido
    empresa.email = email or empresa.email
    empresa.cidade = cidade or empresa.cidade
    empresa.estado = estado or empresa.estado
    empresa.telefone = telefone_limpo or empresa.telefone
    empresa.responsavel_nome = responsavel_nome or empresa.responsavel_nome
    empresa.responsavel_sobrenome = responsavel_sobrenome or None

    if senha:
        empresa.senha = generate_password_hash(senha)

    db.session.commit()
    return redirect(url_for('editar_empresa', ok=1))

@app.route('/painel_malharia')
def painel_malharia():
    if 'empresa_id' not in session:
        return redirect(url_for('login'))

    empresa = Empresa.query.get(session['empresa_id'])
    if not empresa:
        session.pop('empresa_id', None)
        return redirect(url_for('login'))

    if empresa.status_pagamento != 'ativo':
        return render_template('pagamento_pendente.html', empresa=empresa)

    teares = Tear.query.filter_by(empresa_id=empresa.id).all()
    return render_template('painel_malharia.html', empresa=empresa, teares=teares)

from datetime import datetime
import re
from flask import request, jsonify

def _try_int(s):
    try:
        return int(str(s).strip())
    except:
        return None

def _extract_empresa_id(payment):
    """
    Tenta achar o ID da Empresa nas seguintes fontes (nessa ordem):
    1) metadata.empresa_id
    2) external_reference (formatos: "achetece:123:..." | "123" | "qualquer coisa 123")
    3) None
    """
    meta = (payment or {}).get("metadata") or {}
    if isinstance(meta, dict):
        emp_id = _try_int(meta.get("empresa_id"))
        if emp_id:
            return emp_id

    ext = (payment or {}).get("external_reference")
    if ext:
        # formato "achetece:123:*"
        if isinstance(ext, str) and ext.startswith("achetece:"):
            parts = ext.split(":")
            if len(parts) >= 2:
                emp_id = _try_int(parts[1])
                if emp_id:
                    return emp_id
        # só números
        emp_id = _try_int(ext)
        if emp_id:
            return emp_id
        # qualquer dígito dentro do texto
        m = re.search(r"(\d+)", str(ext))
        if m:
            emp_id = _try_int(m.group(1))
            if emp_id:
                return emp_id

    return None


@app.route('/webhook', methods=['POST', 'GET'])
def webhook():
    """
    Webhook do Mercado Pago:
    - Busca o pagamento por ID.
    - Resolve a empresa via metadata/external_reference (com vários formatos) ou fallback por e-mail.
    - Mapeia status -> {ativo|pendente|inativo}.
    - Idempotente: só envia e-mail quando mudar para 'ativo'.
    """
    # ---- Captura do payload (POST e GET) ----
    data = None
    try:
        data = request.get_json(silent=True)
    except Exception as e:
        app.logger.warning(f"[WEBHOOK] get_json falhou: {e}")

    topic = None
    payment_id = None

    if data and isinstance(data, dict):
        topic = data.get('type') or data.get('topic')
        if isinstance(data.get('data'), dict):
            payment_id = data['data'].get('id')
        payment_id = payment_id or data.get('id')
    else:
        topic = request.args.get('topic') or request.form.get('topic') or request.args.get('type')
        payment_id = request.args.get('id') or request.form.get('id')

    app.logger.info(f"[WEBHOOK] topic={topic} | payment_id={payment_id} | raw={data}")

    if not payment_id or str(topic).lower() != 'payment':
        app.logger.info("[WEBHOOK] Notificação ignorada (sem topic=payment ou sem id).")
        return "ok", 200

    # ---- Consulta ao pagamento ----
    try:
        payment_resp = sdk.payment().get(payment_id)
        payment = payment_resp.get("response", {}) if isinstance(payment_resp, dict) else {}
    except Exception as e:
        app.logger.exception(f"[WEBHOOK] Erro ao consultar pagamento {payment_id}: {e}")
        return "ok", 200

    status = (payment or {}).get("status", "") or ""
    status = status.lower()
    payer_email = (((payment or {}).get("payer")) or {}).get("email")
    mp_payment_id = str((payment or {}).get("id"))
    external_reference = (payment or {}).get("external_reference")
    app.logger.info(f"[WEBHOOK] MP id={mp_payment_id} | status={status} | payer={payer_email} | ext_ref={external_reference} | meta={(payment or {}).get('metadata')}")

    # ---- Resolve Empresa ----
    empresa = None

    # 1) Tenta por metadata/ext_ref (vários formatos)
    emp_id = _extract_empresa_id(payment)
    if emp_id:
        empresa = Empresa.query.get(emp_id)
        app.logger.info(f"[WEBHOOK] Empresa via id={emp_id}: {bool(empresa)}")

    # 2) Fallback por e-mail do pagador (atenção: pode ser diferente do e-mail da empresa)
    if not empresa and payer_email:
        empresa = Empresa.query.filter_by(email=(payer_email or "").strip().lower()).first()
        app.logger.info(f"[WEBHOOK] Empresa via payer_email={payer_email}: {bool(empresa)}")

    if not empresa:
        app.logger.error("[WEBHOOK] Empresa não encontrada (nem por external_reference/metadata nem por e-mail).")
        return "ok", 200

    # ---- Mapeia status do MP -> status interno ----
    novo_status = empresa.status_pagamento or "pendente"
    if status in ["approved", "authorized"]:
        novo_status = "ativo"
    elif status in ["in_process", "pending"]:
        novo_status = "pendente"
    elif status in ["rejected", "cancelled", "refunded", "charged_back"]:
        novo_status = "inativo"
    else:
        app.logger.warning(f"[WEBHOOK] Status do MP não mapeado: {status}. Mantendo {novo_status}.")

    # ---- Atualização idempotente ----
    mudou_para_ativo = (empresa.status_pagamento != "ativo" and novo_status == "ativo")

    try:
        empresa.mercadopago_payment_id = mp_payment_id
    except Exception:
        pass
    try:
        empresa.mercadopago_reference = str(external_reference or (payment.get("metadata") or {}).get("empresa_id") or "")
    except Exception:
        pass

    empresa.status_pagamento = novo_status
    if novo_status == "ativo":
        empresa.data_pagamento = datetime.utcnow()

    try:
        db.session.commit()
        app.logger.info(f"[WEBHOOK] Empresa {empresa.id} ({empresa.email}) atualizada para: {empresa.status_pagamento}")
    except Exception as e:
        app.logger.exception(f"[WEBHOOK] Erro ao dar commit: {e}")
        return "ok", 200

    # ---- E-mail somente ao mudar para ATIVO ----
    if mudou_para_ativo:
        try:
            apelido = (empresa.apelido or empresa.nome or "").strip() or "Cliente"
            msg = Message(
                subject="✅ Pagamento aprovado! Acesse seu painel no AcheTece",
                sender=app.config.get('MAIL_USERNAME'),
                recipients=[empresa.email]
            )
            msg.html = render_template_string("""
<!DOCTYPE html>
<html lang="pt-br">
<head>
  <meta charset="UTF-8">
  <title>Pagamento confirmado</title>
  <style>
    body { font-family: Arial, sans-serif; background-color: #f2f2f2; padding:0; margin:0; }
    .container { max-width:600px; margin:40px auto; background:#fff; border-radius:8px; padding:30px;
                 box-shadow:0 2px 8px rgba(0,0,0,.05); }
    .logo { text-align:center; margin-bottom:30px; }
    .logo img { max-height:70px; }
    h2 { color:#003bb3; text-align:center; }
    p { font-size:16px; color:#444; line-height:1.6; margin:20px 0; }
    .botao { display:block; width:max-content; margin:30px auto; padding:14px 28px; background:#003bb3; color:#fff;
             text-decoration:none; border-radius:8px; font-weight:bold; }
    .footer { text-align:center; font-size:13px; color:#999; margin-top:30px; }
    .footer a { color:#666; text-decoration:none; }
    .footer a:hover { text-decoration:underline; }
  </style>
</head>
<body>
  <div class="container">
    <div class="logo">
      <img src="{{ base }}/static/logo-email.png" alt="AcheTece">
    </div>
    <h2>✅ Pagamento confirmado com sucesso!</h2>
    <p>Olá <strong>{{ apelido }}</strong>,</p>
    <p>Seu pagamento foi aprovado e seu acesso ao painel já está liberado.</p>
    <a class="botao" href="{{ base }}/login" target="_blank">Ir para o painel</a>
    <p style="text-align:center; font-size:14px; color:#777;">
      Dúvidas? <a href="https://wa.me/5547991120670" target="_blank">Fale com nossa equipe</a>.
    </p>
    <div class="footer">
      AcheTece © {{ ano }} – Todos os direitos reservados.
      <br><a href="{{ base }}">www.achetece.com.br</a>
    </div>
  </div>
</body>
</html>
            """, apelido=apelido, ano=datetime.utcnow().year, base=base_url())
            mail.send(msg)
            app.logger.info(f"[WEBHOOK] E-mail HTML enviado para {empresa.email}")
        except Exception as e:
            app.logger.exception(f"[WEBHOOK] Erro ao enviar e-mail: {e}")

    return "ok", 200

@app.route("/quem_somos")
@app.route("/quem_somos/")
@app.route("/quem-somos")
@app.route("/quem-somos/")
def quem_somos():
    return render_template("quem_somos.html")

# compatibilidade se alguém acessar .html direto:
@app.route("/quem_somos.html")
def quem_somos_html():
    return redirect(url_for("quem_somos"), code=301)

@app.route('/cadastrar_teares', methods=['GET', 'POST'])
def cadastrar_teares():
    if 'empresa_id' not in session:
        return redirect(url_for('login'))
    if request.method == 'POST':
        novo_tear = Tear(
            marca=request.form['marca'],
            modelo=request.form['modelo'],
            tipo=request.form['tipo'],
            finura=request.form['finura'],
            diametro=request.form['diametro'],
            alimentadores=request.form['alimentadores'],
            elastano=request.form['elastano'],
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
        email = (request.form.get('email') or '').strip().lower()
        empresa = Empresa.query.filter_by(email=email).first()
        if empresa:
            try:
                enviar_email_recuperacao(email, empresa.nome)
                return render_template('esqueci_senha.html', mensagem='📧 Instruções enviadas para seu e-mail.')
            except Exception as e:
                app.logger.exception(f"Erro ao enviar e-mail de recuperação: {e}")
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
        mensagem = "Olá, encontrei seu tear no AcheTece e tenho demanda para esse tipo de máquina. Gostaria de conversar sobre possíveis serviços de tecelagem."
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

    return render_template('busca.html', opcoes=opcoes, filtros=filtros,
                           resultados=resultados, pagina=pagina, total_paginas=total_paginas)

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

@app.route('/malharia_info', methods=['GET'])
def malharia_info():
    return render_template('malharia_info.html')

@app.route('/admin/empresas', methods=['GET', 'POST'])
@login_admin_requerido
def admin_empresas():
    pagina = int(request.args.get('pagina', 1))
    por_pagina = 10

    status = ''
    data_inicio = ''
    data_fim = ''
    query = Empresa.query

    if request.method == 'POST':
        status = request.form.get('status', '')
        data_inicio = request.form.get('data_inicio', '')
        data_fim = request.form.get('data_fim', '')
        return redirect(url_for('admin_empresas', pagina=1, status=status, data_inicio=data_inicio, data_fim=data_fim))
    else:
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

    return render_template('admin_empresas.html',
                           empresas=empresas, pagina=pagina, total_paginas=total_paginas,
                           status=status, data_inicio=data_inicio, data_fim=data_fim)

# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
# NOVA ROTA: Alterar/editar status de pagamento (pendente <-> ativo)
# Aceita GET (para o link do botão) e POST (para uso via form)
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>
@app.route('/admin/editar_status/<int:empresa_id>', methods=['GET', 'POST'])
@login_admin_requerido
def admin_editar_status(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)

    # Permite forçar um valor (?status=ativo) ou alterna automaticamente
    novo_status = request.values.get('status')
    if not novo_status:
        novo_status = 'ativo' if empresa.status_pagamento != 'ativo' else 'pendente'

    empresa.status_pagamento = novo_status
    empresa.data_pagamento = datetime.utcnow() if novo_status == 'ativo' else None
    db.session.commit()

    flash(f'Status de "{empresa.apelido or empresa.nome}" atualizado para {novo_status}.', 'success')
    return redirect(url_for(
        'admin_empresas',
        pagina=request.args.get('pagina', 1),
        status=request.args.get('status', ''),
        data_inicio=request.args.get('data_inicio', ''),
        data_fim=request.args.get('data_fim', '')
    ))
# >>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>>

@app.route('/admin/excluir_empresa/<int:empresa_id>', methods=['POST'])
@login_admin_requerido
def excluir_empresa(empresa_id):
    if not session.get('admin_email') == 'gestao.achetece@gmail.com':
        flash('Acesso não autorizado.')
        return redirect(url_for('login'))

    empresa = Empresa.query.get_or_404(empresa_id)
    db.session.delete(empresa)
    db.session.commit()
    flash(f'Empresa "{empresa.nome}" excluída com sucesso!')
    return redirect(url_for('admin_empresas'))

@app.route('/redefinir_senha/<token>', methods=['GET', 'POST'])
def redefinir_senha(token):
    app.logger.info(f"Token recebido: {token}")
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])

    try:
        email = serializer.loads(token, salt='recupera-senha', max_age=3600)  # 1h
        app.logger.info(f"E-mail do token: {email}")
    except SignatureExpired:
        flash("⏰ O link expirou. Solicite um novo.")
        return render_template("erro_token.html")
    except BadSignature:
        flash("⚠️ O link é inválido ou já foi utilizado.")
        return render_template("erro_token.html")

    empresa = Empresa.query.filter_by(email=email).first()
    if not empresa:
        return "❌ Usuário não encontrado.", 404

    if request.method == 'POST':
        nova_senha = request.form['senha']
        empresa.senha = generate_password_hash(nova_senha)
        db.session.commit()
        flash('✅ Senha redefinida com sucesso! Faça login com a nova senha.')
        return redirect(url_for('login'))

    return render_template('redefinir_senha.html', token_valido=True)

@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email')
        senha = request.form.get('senha')

        if email == 'gestao.achetece@gmail.com' and senha == '123adm@achetece':
            session['admin_email'] = email
            flash('Login de administrador realizado com sucesso.', 'success')
            return redirect(url_for('admin_empresas'))
        else:
            flash('Email ou Senha incorreta.', 'error')
            return redirect(url_for('admin_login'))
    return render_template('admin_login.html')

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_email', None)
    flash('Você saiu do painel administrativo.')
    return redirect(url_for('index'))

@app.route('/pagar', methods=['GET'])
def pagar():
    return redirect(url_for('checkout'))

@app.route('/pagamento_aprovado')
def pagamento_aprovado():
    return render_template('pagamento_aprovado.html')

@app.route('/pagamento_sucesso')
def pagamento_sucesso():
    return render_template("pagamento_sucesso.html")

@app.route('/pagamento_erro')
def pagamento_erro():
    return render_template("pagamento_erro.html")

@app.route('/pagamento_pendente')
def pagamento_pendente():
    return render_template("pagamento_pendente.html")

@app.route("/contato", methods=["GET", "POST"])
def contato():
    enviado = False
    erro = None
    if request.method == "POST":
        nome = (request.form.get("nome") or "").strip()
        email = (request.form.get("email") or "").strip()
        mensagem = (request.form.get("mensagem") or "").strip()
        if not (nome and email and mensagem):
            erro = "Preencha todos os campos."
        else:
            try:
                msg = Message(
                    subject=f"[AcheTece] Novo contato — {nome}",
                    recipients=[os.getenv("CONTACT_TO", app.config.get("MAIL_USERNAME") or "")]
                )
                msg.reply_to = email
                msg.body = f"Nome: {nome}\nE-mail: {email}\n\nMensagem:\n{mensagem}"
                mail.send(msg)
                enviado = True
            except Exception as e:
                erro = f"Falha ao enviar: {e}"
    return render_template("fale_conosco.html", enviado=enviado, erro=erro)

@app.route("/termos")
def termos():
    return render_template("termos_politicas.html")

@app.route('/rota-teste')
def rota_teste():
    return "✅ A rota funciona!"

# --- IBGE / Cidades por UF ---
import json, os
from pathlib import Path
import requests
from flask import jsonify

_CIDADES_CACHE = {}
_CIDADES_JSON_PATH = Path(app.root_path) / "static" / "cidades_por_uf.json"

def _carregar_cidades_estatico():
    """Carrega cidades do JSON estático (se existir)."""
    try:
        if _CIDADES_JSON_PATH.exists():
            with open(_CIDADES_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k.upper(): v for k, v in data.items()}
    except Exception as e:
        app.logger.warning(f"Falha ao ler cidades_por_uf.json: {e}")
    return {}

_CIDADES_ESTATICO = _carregar_cidades_estatico()

def _buscar_cidades_ibge(uf: str):
    """Busca cidades do UF na API do IBGE e retorna lista de nomes."""
    url = f"https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios?orderBy=nome"
    r = requests.get(url, timeout=15)
    r.raise_for_status()
    dados = r.json() or []
    return [item.get("nome") for item in dados if isinstance(item, dict) and item.get("nome")]

def _get_cidades_por_uf(uf: str):
    """
    Retorna lista de cidades (strings) para a UF informada (ex: 'SC', 'SP').
    1) tenta do cache local (cache_ibge/SC.json, por exemplo)
    2) senão, baixa do IBGE e grava no cache
    3) em falha, retorna []
    """
    if not uf:
        return []

    uf = uf.strip().upper()
    cache_path = os.path.join(CACHE_DIR, f'{uf}.json')

    # 1) tenta cache local
    try:
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 2:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
    except Exception as e:
        logging.warning(f'Falha ao ler cache de cidades {uf}: {e}')

    # 2) baixa do IBGE
    try:
        url = f'https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios'
        r = requests.get(url, timeout=10)
        r.raise_for_status()
        municipios = r.json()
        # pega somente o nome
        cidades = sorted([m.get('nome', '').strip() for m in municipios if m.get('nome')], key=_norm)

        # grava cache local para próximas chamadas
        if cidades:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cidades, f, ensure_ascii=False)
            return cidades

    except Exception as e:
        logging.warning(f'Falha ao baixar cidades do IBGE para UF={uf}: {e}')

    # 3) falha total -> lista vazia
    return []

@app.route("/api/cidades")
def api_cidades():
    uf = request.args.get("uf", "")
    return jsonify(_get_cidades_por_uf(uf))

@app.route('/teste_email_pagamento')
def teste_email_pagamento():
    html_email = render_template_string("""
    <!DOCTYPE html>
    <html lang="pt-br">
    <head>
      <meta charset="UTF-8">
      <title>Pagamento confirmado</title>
      <style>
        body { font-family: Arial, sans-serif; background-color: #f2f2f2; padding:0; margin:0; }
        .container { max-width: 600px; margin: 40px auto; background-color: #ffffff; border-radius: 8px;
                     padding: 30px; box-shadow: 0 2px 8px rgba(0,0,0,0.05); }
        .logo { text-align: center; margin-bottom: 30px; }
        .logo img { max-height: 70px; }
        h2 { color: #003bb3; text-align: center; }
        p { font-size: 16px; color: #444; line-height: 1.6; margin: 20px 0; }
        .botao { display: block; width: max-content; margin: 30px auto; padding: 14px 28px;
                 background-color: #003bb3; color: #fff; text-decoration: none; border-radius: 8px; font-weight: bold; }
        .footer { text-align: center; font-size: 13px; color: #999; margin-top: 30px; }
        .footer a { color: #666; text-decoration: none; }
        .footer a:hover { text-decoration: underline; }
      </style>
    </head>
    <body>
      <div class="container">
        <div class="logo">
          <img src="{{ base }}/static/logo-email.png" alt="AcheTece">
        </div>
        <h2>✅ Pagamento confirmado com sucesso!</h2>
        <p>Olá <strong>Tecelagem Estrela</strong>,</p>
        <p>Seu pagamento foi aprovado com sucesso e agora você tem acesso completo à nossa plataforma.</p>
        <p>Acesse o painel da sua malharia para cadastrar seus teares e começar a receber contatos de clientes interessados.</p>
        <a class="botao" href="{{ base }}/login" target="_blank">Ir para o painel</a>
        <p style="text-align: center; font-size: 14px; color: #777;">
          Em caso de dúvidas, entre em contato pelo WhatsApp:<br>
          <a href="https://wa.me/5547991120670" target="_blank">Clique aqui para falar com a equipe AcheTece</a>
        </p>
        <div class="footer">
          AcheTece © 2025 – Todos os direitos reservados.<br>
          <a href="{{ base }}">www.achetece.com.br</a>
        </div>
      </div>
    </body>
    </html>
    """, base=base_url())
    return html_email

from flask import jsonify, session, request
import re

def _try_int(s):
    try:
        return int(str(s).strip())
    except:
        return None

def _empresa_from_ext(ext):
    """
    Aceita formatos tipo:
    - achetece:123:uuid
    - 123
    - qualquer-coisa-123
    """
    if not ext:
        return None
    if isinstance(ext, str) and ext.startswith("achetece:"):
        parts = ext.split(":")
        if len(parts) >= 2:
            emp_id = _try_int(parts[1])
            if emp_id:
                return Empresa.query.get(emp_id)
    emp_id = _try_int(ext)
    if emp_id:
        return Empresa.query.get(emp_id)
    m = re.search(r"(\d+)", str(ext))
    if m:
        emp_id = _try_int(m.group(1))
        if emp_id:
            return Empresa.query.get(emp_id)
    return None

@app.route('/api/pagamento_status')
def api_pagamento_status():
    """
    Retorna {"status": "ativo|pendente|inativo|desconhecido", "empresa_id": <id or null>}
    Prioriza sessão; se não houver, tenta via ?ext=<external_reference>.
    """
    empresa = None

    emp_id = session.get('empresa_id')
    if emp_id:
        empresa = Empresa.query.get(emp_id)

    if not empresa:
        ext = request.args.get('ext')
        if ext:
            empresa = _empresa_from_ext(ext)

    if not empresa:
        return jsonify({"status": "desconhecido", "empresa_id": None}), 200

    status = (empresa.status_pagamento or "pendente").lower()
    if status not in ("ativo", "pendente", "inativo"):
        status = "desconhecido"

    return jsonify({"status": status, "empresa_id": int(empresa.id)}), 200

from flask import Flask
import os

def create_app():
    app = Flask(__name__)
    app.secret_key = os.getenv("SECRET_KEY", "dev")  # necessário p/ flash & session

    app.config['WHATSAPP_URL'] = os.getenv('WHATSAPP_URL', 'https://wa.me/5547999999999')
    app.jinja_env.globals.update(config=app.config)

    from auth import bp as auth_bp
    app.register_blueprint(auth_bp, url_prefix="")  # sem prefixo

    @app.get("/", endpoint="index")
    def index():
        from flask import render_template
        return render_template("index.html")

    return app

if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
