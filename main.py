from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    render_template_string, send_file, jsonify
)
from flask_mail import Mail, Message
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
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
from sqlalchemy import inspect, text   # <-- para checar/alterar colunas
from authlib.integrations.flask_client import OAuth
from enum import Enum
from urllib.parse import quote_plus

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
app.logger.setLevel(logging.INFO)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-unsafe')

# DB (SQLite local por padrão; use DATABASE_URL no Render se quiser Postgres)
# === DATABASE_URL normalizado (Postgres em produção, SQLite no dev) ===
db_url = os.getenv('DATABASE_URL', 'sqlite:///banco.db')

# Render (e outros) às vezes fornecem "postgres://" ou "postgresql://"
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql+psycopg://', 1)
elif db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg://', 1)

# Se for Postgres remoto, garanta SSL (mantendo query params se houver)
if db_url.startswith('postgresql+psycopg://') and 'sslmode=' not in db_url:
    db_url += ('&' if '?' in db_url else '?') + 'sslmode=require'

app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['PREFERRED_URL_SCHEME'] = 'https'

# E-mail (ajuste no Render)
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')

mail = Mail(app)

# Instancia o SQLAlchemy **depois** de definir a URI
db = SQLAlchemy(app)

# ========= NOVO: Configuração OAuth Google =========
oauth = OAuth(app)
google = oauth.register(
    name='google',
    client_id=os.environ.get("GOOGLE_CLIENT_ID"),
    client_secret=os.environ.get("GOOGLE_CLIENT_SECRET"),
    access_token_url='https://oauth2.googleapis.com/token',
    authorize_url='https://accounts.google.com/o/oauth2/auth',
    api_base_url='https://www.googleapis.com/oauth2/v1/',
    userinfo_endpoint='https://openidconnect.googleapis.com/v1/userinfo',
    client_kwargs={'scope': 'openid email profile'},
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

class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255))   # pode ficar None se usar só Google
    google_id = db.Column(db.String(255))
    role = db.Column(db.String(20), index=True, nullable=True)  # 'cliente' | 'malharia' | 'admin'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

class Empresa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), unique=True)
    usuario = db.relationship('Usuario', backref=db.backref('empresa', uselist=False))
    nome = db.Column(db.String(100), nullable=False, unique=True)
    apelido = db.Column(db.String(50), unique=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    senha = db.Column(db.String(200), nullable=False)
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    telefone = db.Column(db.String(20))
    status_pagamento = db.Column(db.String(20), default='pendente')
    data_pagamento = db.Column(db.DateTime)
    teares = db.relationship('Tear', backref='empresa', lazy=True, cascade="all, delete-orphan")
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

class ClienteProfile(db.Model):
    __tablename__ = 'cliente_profile'
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), unique=True, nullable=False)
    nome = db.Column(db.String(120))
    empresa = db.Column(db.String(160))
    whatsapp = db.Column(db.String(20))
    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

    # Relacionamento c/ Usuario (cria atributo usuario.cliente_profile)
    usuario = db.relationship('Usuario', backref=db.backref('cliente_profile', uselist=False))

def _get_empresa_usuario_da_sessao():
    """Retorna (empresa, usuario) a partir de session['empresa_id'].
       Se não existir Usuario vinculado, cria um básico como malharia."""
    if 'empresa_id' not in session:
        return None, None
    emp = Empresa.query.get(session['empresa_id'])
    if not emp:
        session.pop('empresa_id', None)
        session.pop('empresa_apelido', None)
        return None, None

    u = emp.usuario or Usuario.query.filter_by(email=emp.email).first()
    if not u:
        # fallback seguro caso o backfill ainda não tenha rodado
        u = Usuario(email=emp.email, senha_hash=emp.senha, role=None, is_active=True)
        db.session.add(u); db.session.flush()
        emp.user_id = u.id
        db.session.commit()
    elif not emp.user_id:
        emp.user_id = u.id
        db.session.commit()

    # garante apelido na sessão para mostrar no menu
    session['empresa_apelido'] = emp.apelido or emp.nome or emp.email.split('@')[0]
    return emp, u

def _ensure_auth_layer_and_link():
    # 1) Criar tabela 'usuario' se necessário
    try:
        Usuario.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as e:
        app.logger.warning(f"create usuario table: {e}")

    # 2) Adicionar coluna user_id em 'empresa' se necessário
    try:
        insp = inspect(db.engine)
        cols = [c['name'] for c in insp.get_columns('empresa')]
        if 'user_id' not in cols:
            db.session.execute(text('ALTER TABLE empresa ADD COLUMN user_id INTEGER'))
            db.session.commit()
    except Exception as e:
        app.logger.warning(f"add user_id to empresa failed: {e}")

    # 3) Backfill: criar Usuario para cada Empresa que ainda não tem vínculo
    try:
        empresas = Empresa.query.all()
        for e in empresas:
            if e.user_id:
                continue
            u = Usuario.query.filter_by(email=e.email).first()
            if not u:
                u = Usuario(
                    email=e.email,
                    senha_hash=e.senha,   # aproveita o hash que você já guarda em Empresa.senha
                    role=None,
                    is_active=True
                )
                db.session.add(u)
                db.session.flush()  # garante u.id
            e.user_id = u.id
        db.session.commit()
    except Exception as e:
        app.logger.warning(f"backfill usuarios from empresas failed: {e}")

# --- util para garantir a tabela cliente_profile (idempotente) ---
def _ensure_cliente_profile_table():
    try:
        ClienteProfile.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as e:
        app.logger.warning(f"create cliente_profile table: {e}")

# --- migrações simples de inicialização ---
def _startup_migrations():
    """
    Executa na inicialização do app para garantir que o banco está pronto.
    - Cria TODAS as tabelas definidas pelos modelos (db.create_all)
    - Executa ajustes auxiliares (ex.: coluna user_id em Empresa + backfill)
    - Garante cliente_profile (idempotente)
    """
    try:
        # 1) cria todas as tabelas que ainda não existirem
        db.create_all()

        # 2) seus ajustes específicos já existentes
        _ensure_auth_layer_and_link()   # garante empresa.user_id + backfill
        _ensure_cliente_profile_table() # seguro repetir; não faz mal

        app.logger.info("Startup migrations OK (create_all + ajustes).")
    except Exception as e:
        app.logger.error(f"Startup migrations failed: {e}")

# --- dispara as migrações no carregamento do app ---
with app.app_context():
    _startup_migrations()

# ---------------------------
# Decorator de acesso pago (com bypass em modo DEMO)
# ---------------------------
def assinatura_ativa_requerida(f):
    """Exige pagamento ativo, mas libera ações no modo DEMO ou para empresas com apelido iniciado por [DEMO]."""
    @wraps(f)
    def wrapper(*args, **kwargs):
        emp, _ = _get_empresa_usuario_da_sessao()
        if not emp:
            flash("Faça login para continuar.", "error")
            return redirect(url_for("login"))

        # BYPASS em modo DEMO OU se a empresa for marcada como [DEMO]
        # (DEMO_MODE pode ser definido no ambiente; por padrão fica 'true' para facilitar seus testes)
        is_demo = os.getenv("DEMO_MODE", "true").lower() == "true"
        apel = (emp.apelido or emp.nome or "")
        if is_demo or apel.startswith("[DEMO]"):
            return f(*args, **kwargs)

        # Regra normal: exige pagamento ativo/aprovado
        status = (emp.status_pagamento or "pendente").lower()
        if status not in ("ativo", "aprovado"):
            flash("Ative seu plano para acessar esta funcionalidade.", "error")
            return redirect(url_for("painel_malharia"))

        return f(*args, **kwargs)
    return wrapper

# === DEMO MODE (para testes de filtros/design) ===
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"   # deixe true enquanto estiver testando
DEMO_TOKEN = os.getenv("DEMO_TOKEN", "localdemo")              # token simples p/ ações demo

# ---------------------------
# Rotas
# ---------------------------

# --- ROTA TEMPORÁRIA PARA POPULAR DADOS DEMO (remova depois) ---
import os
from flask import abort
try:
    from seed_demo import seed
except Exception:
    seed = None

SEED_TOKEN = os.environ.get("SEED_TOKEN", "troque-este-token")

from random import choice
from werkzeug.security import generate_password_hash

@app.get("/demo/seed_empresas")
def demo_seed_empresas():
    """Cria/atualiza ~20 empresas DEMO (sem teares). Idempotente."""
    if not DEMO_MODE or request.args.get("token") != DEMO_TOKEN:
        return "forbidden", 403

    DEMO_TAG = "[DEMO]"
    DEFAULT_HASH = generate_password_hash("demo123")

    # (UF, Cidade) comuns do setor têxtil
    LOCS = [
        ("SC","Blumenau"), ("SC","Brusque"), ("SC","Jaraguá do Sul"), ("SC","Joinville"),
        ("PR","Maringá"), ("PR","Curitiba"), ("PR","Londrina"),
        ("RS","Caxias do Sul"), ("RS","Novo Hamburgo"),
        ("SP","Americana"), ("SP","São Paulo"), ("SP","Campinas"), ("SP","Jacareí"),
        ("MG","Belo Horizonte"), ("MG","Juiz de Fora"),
        ("RJ","Rio de Janeiro"),
        ("CE","Fortaleza"),
        ("BA","Salvador"),
        ("GO","Goiânia"),
        ("PE","Caruaru"),
    ]

    base_nomes = [
        "Malharia Modelo", "Fios & Malhas", "TramaSul Têxteis", "Tecidos Paraná", "Nordeste Knit",
        "Mineira Malharia", "Tear&Cia", "Prime Tricot", "Top Yarn", "Circular Têxtil",
        "Malhas Brasil", "TexFiber", "Malharia Alfa", "Delta Knit", "LoomTech",
        "Ponto a Ponto", "WeavePro", "UniKnit", "Nova Trama", "City Knit"
    ]

    criadas, atualizadas = 0, 0
    for i, (uf, cid) in enumerate(LOCS):
        nome  = base_nomes[i]
        apel  = f"{DEMO_TAG} {nome.split()[0]}"
        email = f"demo{i+1:02d}@achetece.demo"
        tel   = f"47{990000000 + i:011d}"[:11]  # 11 dígitos

        emp = Empresa.query.filter((Empresa.email==email) | (Empresa.apelido==apel)).first()
        if not emp:
            emp = Empresa(
                nome=nome, apelido=apel, email=email, senha=DEFAULT_HASH,
                estado=uf, cidade=cid, telefone=tel,
                status_pagamento="aprovado", data_pagamento=datetime.utcnow(),
                responsavel_nome="Demo", responsavel_sobrenome=str(i+1)
            )
            db.session.add(emp); criadas += 1
        else:
            emp.nome = nome; emp.apelido = apel; emp.estado = uf; emp.cidade = cid
            if not emp.senha: emp.senha = DEFAULT_HASH
            emp.status_pagamento = "aprovado"; emp.data_pagamento = datetime.utcnow()
            if not emp.telefone: emp.telefone = tel
            atualizadas += 1

    db.session.commit()
    # garante vínculo Usuario (seu startup já faz, mas reforçamos)
    try: _ensure_auth_layer_and_link()
    except: pass

    return f"OK — empresas DEMO criadas={criadas}, atualizadas={atualizadas}"

@app.get("/demo/login_empresa/<int:empresa_id>")
def demo_login_empresa(empresa_id):
    if not DEMO_MODE or request.args.get("token") != DEMO_TOKEN:
        return "forbidden", 403
    emp = Empresa.query.get_or_404(empresa_id)
    session["empresa_id"] = emp.id
    session["empresa_apelido"] = emp.apelido or emp.nome
    goto = request.args.get("goto")
    if goto == "cadastrar_teares":
        return redirect(url_for("cadastrar_teares"))
    return redirect(url_for("painel_malharia"))

@app.get("/seed-demo")
def run_seed_demo():
    if seed is None:
        return "seed_demo.py não encontrado ou sem função seed().", 500
    token = request.args.get("token")
    if token != SEED_TOKEN:
        return abort(403)
    with app.app_context():
        seed()
    return "✅ Seed executado. Empresas e teares DEMO criados."

# Página inicial AGORA também é a busca
@app.route("/", methods=["GET", "POST"])
def index():
    # ==== Opções dos selects ====
    filtros = {'tipo': '', 'diâmetro': '', 'galga': '', 'estado': '', 'cidade': ''}
    opcoes = {'tipo': [], 'diâmetro': [], 'galga': [], 'estado': [], 'cidade': []}

    todos_teares = (
        Tear.query.join(Empresa)
        .add_columns(Tear.tipo, Tear.diametro, Tear.finura, Empresa.estado, Empresa.cidade)
        .all()
    )
    for t in todos_teares:
        if t.tipo and t.tipo not in opcoes['tipo']:
            opcoes['tipo'].append(t.tipo)
        if t.diametro is not None and str(t.diametro) not in opcoes['diâmetro']:
            opcoes['diâmetro'].append(str(t.diametro))
        if t.finura is not None and str(t.finura) not in opcoes['galga']:
            opcoes['galga'].append(str(t.finura))
        if t.estado and t.estado not in opcoes['estado']:
            opcoes['estado'].append(t.estado)
        if t.cidade and t.cidade not in opcoes['cidade']:
            opcoes['cidade'].append(t.cidade)

    # ==== Filtros (POST ou GET) ====
    origem = request.form if request.method == 'POST' else request.args
    query = Tear.query.join(Empresa)

    for campo in filtros:
        valor = (origem.get(campo) or "").strip()
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

    # ==== Paginação ====
    pagina = int(request.args.get('pagina', 1) or 1)
    por_pagina = 5
    total = query.count()
    total_paginas = (total + por_pagina - 1) // por_pagina
    teares_paginados = query.offset((pagina - 1) * por_pagina).limit(por_pagina).all()

    # ==== Resultados da tabela ====
    resultados = []
    for tear in teares_paginados:
        emp = tear.empresa
        apelido = (emp.apelido or emp.nome or (emp.email.split('@')[0] if emp and emp.email else '—'))
        numero = re.sub(r'\D', '', (emp.telefone or ''))
        contato_link = None
        if numero:
            # monta link wa.me; se não tiver DD/país, ao menos tenta o número bruto
            contato_link = f"https://wa.me/{'55' + numero if not numero.startswith('55') else numero}"

        resultados.append({
            'Empresa': apelido or '—',
            'Tipo': tear.tipo or '—',
            'Diâmetro': tear.diametro if tear.diametro is not None else '—',
            'Galga': tear.finura if tear.finura is not None else '—',
            'Alimentadores': tear.alimentadores if tear.alimentadores is not None else '—',
            'Estado': emp.estado or '—',
            'Cidade': emp.cidade or '—',
            'Contato': contato_link  # use isso no template para mostrar o botão WhatsApp
        })

    return render_template(
        "index.html",
        opcoes=opcoes,
        filtros=filtros,
        resultados=resultados,
        pagina=pagina,
        total_paginas=total_paginas
    )

@app.route("/buscar_teares", methods=["GET", "POST"])
def buscar_teares_redirect():
    qs = request.query_string.decode("utf-8")
    return redirect(f"{url_for('index')}{('?' + qs) if qs else ''}")

@app.route('/dashboard_cliente')
def dashboard_cliente():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))
    if u.role != 'cliente':
        return redirect(url_for('pos_login'))

    cp = ClienteProfile.query.filter_by(user_id=u.id).first()
    if not cp:
        return redirect(url_for('criar_conta_cliente'))

    # Aqui você vai evoluir depois (buscas salvas, histórico etc.)
    return render_template_string("""
        <main style="max-width:920px;margin:24px auto;padding:0 16px;font-family:Inter,Arial,sans-serif">
          <h2 style="margin:0 0 8px;color:#4a145e">Bem-vindo(a), {{nome or 'Cliente'}}</h2>
          <p style="margin:0 0 16px;color:#555">Seu perfil está ativo. Em breve: recursos para buscar malharias, salvar filtros e conversar.</p>
          <a href="{{ url_for('busca') }}" style="display:inline-block;background:#8A00FF;color:#fff;padding:10px 16px;border-radius:14px;text-decoration:none;font-weight:700">Buscar malharias</a>
        </main>
    """, nome=(cp.nome if cp else None))

@app.route('/pos_login')
def pos_login():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))

    # Se não escolheu perfil ainda, vai para os cards
    if not u.role:
        return redirect(url_for('escolher_perfil'))

    # Roteamento por perfil
    if u.role == 'admin':
        return redirect(url_for('admin_empresas'))
    if u.role == 'cliente':
        cp = ClienteProfile.query.filter_by(user_id=u.id).first()
        if not cp:
            return redirect(url_for('criar_conta_cliente'))
        return redirect(url_for('dashboard_cliente'))
    if u.role == 'malharia':
        # Sempre envia ao painel (pago ou não) — gating fica no template/botões
        return redirect(url_for('painel_malharia'))

    # Qualquer valor inesperado de role -> força escolher
    return redirect(url_for('escolher_perfil'))

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
    session['empresa_apelido'] = empresa.apelido or empresa.nome or empresa.email.split('@')[0]
    session['admin'] = (empresa.email == "gestao.achetece@gmail.com")

    # Redireciona para o destino salvo ou para o hub pós-login
    dest = session.pop('login_next', None) or url_for('pos_login')
    return redirect(dest)

# --- IMPORTS necessários no topo (garanta que estão presentes) ---
# from werkzeug.security import check_password_hash, generate_password_hash
# from flask import request, render_template, redirect, url_for, flash, session
# from your_app import app, db
# from your_models import Malharia
# import hashlib

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'GET':
        return render_template('login.html')

    # 1) Leia e normalize os campos do formulário
    email = (request.form.get('email') or '').strip().lower()
    senha = (request.form.get('senha') or '')

    # 2) Busca case-insensitive em Empresa (malharia)
    user = Empresa.query.filter(db.func.lower(Empresa.email) == email).first()

    GENERIC_FAIL = 'E-mail ou senha incorretos. Tente novamente.'

    if not user:
        app.logger.info(f"[LOGIN FAIL] user not found for {email}")
        flash(GENERIC_FAIL)
        return redirect(url_for('login'))

    # 3) Verificação de senha (hash PBKDF2)
    ok = False
    try:
        ok = check_password_hash(user.senha, senha)
    except Exception as e:
        app.logger.warning(f"[LOGIN WARN] check_password_hash raised for {email}: {e}")

    if not ok:
        app.logger.info(f"[LOGIN FAIL] bad password for {email}")
        flash(GENERIC_FAIL)
        return redirect(url_for('login'))

    # 4) Gate de pagamento (se quiser manter)
    if user.status_pagamento and user.status_pagamento not in (None, 'aprovado', 'ativo'):
        app.logger.info(f"[LOGIN BLOCK] payment status={user.status_pagamento} for {email}")
        flash('Pagamento ainda não aprovado. Assim que confirmar, o acesso será liberado.')
        return redirect(url_for('login'))

    # 5) Sucesso: cria sessão e vai para o painel
    session['empresa_id'] = user.id
    session['empresa_apelido'] = user.apelido or user.nome or user.email.split('@')[0]
    app.logger.info(f"[LOGIN OK] {email} -> painel_malharia")
    return redirect(url_for('painel_malharia'))

@app.route("/fale_conosco")
@app.route("/suporte")
def fale_conosco():
    # Se você já tem um template fale_conosco.html, ele abre aqui:
    try:
        return render_template("fale_conosco.html")
    except Exception:
        # fallback: se ainda não tiver o template, redireciona para a home
        return redirect(url_for("index"))


# --- Quem somos (várias URLs apontando para o mesmo endpoint) ---
@app.route("/quem_somos", endpoint="quem_somos")
@app.route("/quem_somos/")
@app.route("/quem-somos")
@app.route("/quem-somos/")
def view_quem_somos():
    return render_template("quem_somos.html")

# opcional: compatibilidade com .html direto
@app.route("/quem_somos.html")
def quem_somos_html():
    return redirect(url_for("quem_somos"), code=301)


@app.route('/_set_role/<valor>')
def _set_role(valor):
    emp, u = _get_empresa_usuario_da_sessao()
    if not u:
        return redirect(url_for('login'))
    if valor == 'none':
        u.role = None
    elif valor in ('cliente', 'malharia', 'admin'):
        u.role = valor
    else:
        return "Valor inválido. Use: none | cliente | malharia | admin", 400
    db.session.commit()
    return f"Role atualizado para: {u.role}"

@app.route("/_debug_cliente")
def _debug_cliente():
    emp, u = _get_empresa_usuario_da_sessao()
    try:
        uid = u.id if u else None
        role = getattr(u, "role", None) if u else None
        cp = ClienteProfile.query.filter_by(user_id=uid).first() if uid else None
        return {
            "logado": bool(emp and u),
            "user_id": uid,
            "role": role,
            "cliente_profile_existe": bool(cp),
        }
    except Exception as e:
        app.logger.exception("[_debug_cliente] %s", e)
        return {"erro": str(e)}, 500

@app.route('/logout')
def logout():
    session.pop('empresa_id', None)
    session.pop('empresa_apelido', None)
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
        session['empresa_apelido'] = nova_empresa.apelido or nova_empresa.nome or nova_empresa.email.split('@')[0]

        # NOVO FLUXO: vai para o painel (não mais para o checkout direto)
        flash("Cadastro concluído! Complete seu perfil e ative seu plano quando quiser.", "success")
        return redirect(url_for('painel_malharia'))

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
        session.pop('empresa_apelido', None)
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
        session.pop('empresa_apelido', None)
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
    # Mantém apelido atualizado na sessão
    session['empresa_apelido'] = empresa.apelido or empresa.nome or empresa.email.split('@')[0]
    return redirect(url_for('editar_empresa', ok=1))

@app.route('/painel_malharia')
def painel_malharia():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))

    # Sempre abre o painel independentemente do pagamento
    teares = Tear.query.filter_by(empresa_id=emp.id).all()
    # Passa flag para template controlar botões/avisos
    is_ativa = (emp.status_pagamento or "pendente") == "ativo"
    return render_template('painel_malharia.html', empresa=emp, teares=teares, assinatura_ativa=is_ativa)

# ======= CRUD de teares (gated por assinatura quando criar/editar/excluir) =======

@app.route('/cadastrar_teares', methods=['GET', 'POST'])
@assinatura_ativa_requerida
def cadastrar_teares():
    emp, _ = _get_empresa_usuario_da_sessao()
    if request.method == 'POST':
        novo_tear = Tear(
            marca=request.form['marca'],
            modelo=request.form['modelo'],
            tipo=request.form['tipo'],
            finura=request.form['finura'],
            diametro=request.form['diametro'],
            alimentadores=request.form['alimentadores'],
            elastano=request.form['elastano'],
            empresa_id=emp.id
        )
        db.session.add(novo_tear)
        db.session.commit()
        return redirect(url_for('painel_malharia'))
    return render_template('cadastrar_teares.html')

@app.route('/editar_tear/<int:id>', methods=['GET', 'POST'])
@assinatura_ativa_requerida
def editar_tear(id):
    emp, _ = _get_empresa_usuario_da_sessao()
    tear = Tear.query.get_or_404(id)
    if tear.empresa_id != emp.id:
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
@assinatura_ativa_requerida
def excluir_tear(id):
    emp, _ = _get_empresa_usuario_da_sessao()
    tear = Tear.query.get_or_404(id)
    if tear.empresa_id != emp.id:
        return redirect(url_for('login'))

    db.session.delete(tear)
    db.session.commit()
    return redirect(url_for('painel_malharia'))

# ======= Autoexclusão de conta pela malharia =======
@app.route('/empresa/excluir', methods=['POST'])
def excluir_empresa_self():
    emp, _ = _get_empresa_usuario_da_sessao()
    if not emp:
        return redirect(url_for('login'))
    # Remove teares (cascade já configurado, mas garantimos)
    Tear.query.filter_by(empresa_id=emp.id).delete()
    db.session.delete(emp)
    db.session.commit()
    session.clear()
    flash("Sua conta foi excluída.", "success")
    return redirect(url_for('index'))

# ======= Fluxo de recuperação de senha =======
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

# ======= Busca pública (compat) =======
@app.route('/busca', methods=['GET', 'POST'])
def buscar_teares():
    # Redireciona para a home, preservando filtros/paginação
    qs = request.query_string.decode('utf-8')
    # POST também deve ir pra home
    return redirect(f"{url_for('index')}{('?' + qs) if qs else ''}")

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
    # Mantido por compatibilidade; aponta para /checkout
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

@app.route("/_routes")
def _routes():
    return "<br>".join(sorted(str(r) for r in app.url_map.iter_rules()))

@app.route('/_check_usuario')
def _check_usuario():
    try:
        total_u = Usuario.query.count()
        sem_vinculo = Empresa.query.filter(Empresa.user_id.is_(None)).count()
        amostras = [u.email for u in Usuario.query.limit(3).all()]
        return f'Usuarios: {total_u} | Empresas sem vínculo: {sem_vinculo} | Ex.: {amostras}'
    except Exception as e:
        return f'Erro: {e}', 500

@app.route('/escolher_perfil', methods=['GET', 'POST'])
def escolher_perfil():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))

    if request.method == 'POST':
        role = (request.form.get('role') or '').strip().lower()
        if role not in ('cliente', 'malharia'):
            flash('Selecione um perfil válido.')
            return redirect(url_for('escolher_perfil'))
        u.role = role
        db.session.commit()
        return redirect(url_for('pos_login'))

    return render_template('escolher_perfil.html')

@app.route('/criar_conta_cliente', methods=['GET', 'POST'])
def criar_conta_cliente():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))
    if u.role != 'cliente':
        return redirect(url_for('pos_login'))

    estados = [
        'AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
        'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO'
    ]

    cp = ClienteProfile.query.filter_by(user_id=u.id).first()

    if request.method == 'POST':
        nome = (request.form.get('nome') or '').strip()
        empresa_cli = (request.form.get('empresa') or '').strip()
        whatsapp = re.sub(r'\D', '', (request.form.get('whatsapp') or ''))
        estado = (request.form.get('estado') or '').strip().upper()
        cidade = (request.form.get('cidade') or '').strip()

        erros = {}
        if not nome or len(re.sub(r'[^A-Za-zÀ-ÿ]', '', nome)) < 2:
            erros['nome'] = 'Informe seu nome.'
        if estado and estado not in estados:
            erros['estado'] = 'Estado inválido.'
        if whatsapp and (len(whatsapp) < 10 or len(whatsapp) > 13):
            erros['whatsapp'] = 'WhatsApp inválido (use DDD + número).'

        if erros:
            return render_template('criar_conta_cliente.html', erros=erros, estados=estados,
                                   nome=nome, empresa=empresa_cli, whatsapp=whatsapp,
                                   estado=estado, cidade=cidade)

        if not cp:
            cp = ClienteProfile(user_id=u.id)
            db.session.add(cp)

        cp.nome = nome
        cp.empresa = empresa_cli or None
        cp.whatsapp = whatsapp or None
        cp.estado = estado or None
        cp.cidade = cidade or None
        db.session.commit()

        flash('Perfil de cliente salvo com sucesso.')
        return redirect(url_for('dashboard_cliente'))

    # GET
    valores = {
        'nome': (cp.nome if cp else ''),
        'empresa': (cp.empresa if cp else ''),
        'whatsapp': (cp.whatsapp if cp else ''),
        'estado': (cp.estado if cp else ''),
        'cidade': (cp.cidade if cp else '')
    }
    return render_template('criar_conta_cliente.html', estados=estados, **valores)

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

# -------- Autocomplete de Localização (cidades) --------
from flask import jsonify, request
from unicodedata import normalize

def _norm_txt(s: str) -> str:
    """Normaliza para busca sem acento e case-insensitive."""
    if not s:
        return ""
    return normalize("NFKD", s).encode("ascii", "ignore").decode("ascii").lower().strip()

def _todas_cidades_por_uf() -> dict:
    """
    Retorna { 'SC': ['Joinville', 'Jaraguá do Sul', ...], 'SP': [...], ... }
    Usa sua função já existente _get_cidades_por_uf(uf). Se não tiver, adapte para ler do JSON.
    """
    ufs = ["AC","AL","AM","AP","BA","CE","DF","ES","GO","MA","MG","MS","MT","PA","PB","PE",
           "PI","PR","RJ","RN","RO","RR","RS","SC","SE","SP","TO"]
    mapa = {}
    for sigla in ufs:
        try:
            lista = _get_cidades_por_uf(sigla) or []
        except Exception:
            lista = []
        mapa[sigla] = lista
    return mapa

# Cache simples em memória para não reler toda hora
_CIDADES_CACHE = None

@app.get("/api/suggest/localizacao")
def api_suggest_localizacao():
    """
    Query params:
      q   = termo digitado (obrigatório)
      uf  = UF para filtrar (opcional). Ex.: SC
      limit = máximo de resultados (opcional, padrão 10)
    Resposta: lista JSON de objetos { "label": "Jaraguá do Sul - SC", "cidade": "Jaraguá do Sul", "uf": "SC" }
    """
    global _CIDADES_CACHE
    q = _norm_txt(request.args.get("q", ""))
    uf = (request.args.get("uf") or "").upper().strip()
    try:
        limit = int(request.args.get("limit", 10))
    except Exception:
        limit = 10

    if not q:
        return jsonify([])

    if _CIDADES_CACHE is None:
        _CIDADES_CACHE = _todas_cidades_por_uf()

    candidatos = []
    if uf and uf in _CIDADES_CACHE:
        fonte = [(c, uf) for c in _CIDADES_CACHE[uf]]
    else:
        # Varre todas as UFs
        fonte = []
        for uf_sigla, cidades in _CIDADES_CACHE.items():
            fonte.extend((c, uf_sigla) for c in cidades)

    # filtra por contém (melhor que startswith) e sem acento
    resultados = []
    for cidade, uf_sigla in fonte:
        if q in _norm_txt(cidade):
            resultados.append({
                "label": f"{cidade} - {uf_sigla}",
                "cidade": cidade,
                "uf": uf_sigla
            })
            if len(resultados) >= limit:
                break

    return jsonify(resultados)

# --- DEBUG: listar apenas as empresas DEMO com teares (remover depois) ---
from flask import render_template_string
from sqlalchemy import or_

@app.get("/utils/demo-empresas")
def utils_demo_empresas():
    demo_emails = [
        "modelo@teste.com","fios@teste.com","tramasul@teste.com",
        "parana@teste.com","nordeste@teste.com","mineira@teste.com",
    ]

    empresas = (Empresa.query
        .filter(or_(Empresa.apelido.ilike("%[DEMO]%"),
                    Empresa.email.in_(demo_emails)))
        .order_by(Empresa.id.asc())
        .all())

    html = """
    <html><head><meta charset="utf-8"><title>Empresas DEMO</title>
      <style>
        body{font-family:Inter,Arial,sans-serif;margin:24px;color:#222}
        .card{border:1px solid #eee;border-radius:12px;padding:16px;margin:12px 0;background:#fff}
        .muted{color:#666;font-size:13px}
        table{width:100%;border-collapse:collapse;margin-top:10px}
        th,td{border-bottom:1px solid #f0f0f0;padding:8px 6px;text-align:left;font-size:14px}
        th{background:#fafafa}
        .pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#f4f0ff}
      </style>
    </head><body>
      <h1>Empresas DEMO cadastradas</h1>
      {% if not empresas %}
        <p class="muted">Nenhuma empresa DEMO encontrada.</p>
      {% endif %}
      {% for e in empresas %}
        <div class="card">
          <div>
            <strong>{{ e.nome }}</strong>
            {% if e.apelido %}<span class="pill">{{ e.apelido }}</span>{% endif %}<br>
            <span class="muted">{{ e.cidade }} - {{ e.estado }} • {{ e.email }} • status={{ e.status_pagamento or '—' }}</span>
          </div>
          <table>
            <thead><tr><th>Marca</th><th>Modelo</th><th>Tipo</th><th>Finura</th><th>Diâmetro</th><th>Alimentadores</th><th>Elastano</th></tr></thead>
            <tbody>
              {% set teares = e.teares or [] %}
              {% if teares|length == 0 %}
                <tr><td colspan="7" class="muted">Sem teares cadastrados.</td></tr>
              {% else %}
                {% for t in teares %}
                  <tr>
                    <td>{{ t.marca }}</td>
                    <td>{{ t.modelo|default('') }}</td>
                    <td>{{ t.tipo|default('') }}</td>
                    <td>{{ t.finura }}</td>
                    <td>{{ t.diametro }}</td>
                    <td>{{ t.alimentadores }}</td>
                    <td>{{ t.elastano }}</td>
                  </tr>
                {% endfor %}
              {% endif %}
            </tbody>
          </table>
          <p class="muted">ID: {{ e.id }} • <a href="/malharia_info/{{ e.id }}">abrir /malharia_info/{{ e.id }}</a></p>
        </div>
      {% endfor %}
    </body></html>
    """
    return render_template_string(html, empresas=empresas)

# --- DEBUG: últimas 30 empresas com teares (remover depois) ---
from flask import render_template_string

@app.get("/utils/empresas-recentes")
def utils_empresas_recentes():
    empresas = Empresa.query.order_by(Empresa.id.desc()).limit(30).all()
    html = """
    <html><head><meta charset="utf-8"><title>Empresas recentes</title>
      <style>
        body{font-family:Inter,Arial,sans-serif;margin:24px;color:#222}
        .card{border:1px solid #eee;border-radius:12px;padding:16px;margin:12px 0;background:#fff}
        .muted{color:#666;font-size:13px}
        table{width:100%;border-collapse:collapse;margin-top:10px}
        th,td{border-bottom:1px solid #f0f0f0;padding:8px 6px;text-align:left;font-size:14px}
        th{background:#fafafa}
        .pill{display:inline-block;padding:2px 8px;border-radius:999px;font-size:12px;background:#f4f0ff}
      </style>
    </head><body>
      <h1>Empresas recentes (top 30)</h1>
      {% if not empresas %}
        <p class="muted">Nenhuma empresa encontrada.</p>
      {% endif %}
      {% for e in empresas %}
        <div class="card">
          <div>
            <strong>{{ e.nome }}</strong>
            {% if e.apelido %}<span class="pill">{{ e.apelido }}</span>{% endif %}<br>
            <span class="muted">{{ e.cidade }} - {{ e.estado }} • {{ e.email }} • status={{ e.status_pagamento or '—' }}</span>
          </div>
          <table>
            <thead><tr><th>Marca</th><th>Modelo</th><th>Tipo</th><th>Finura</th><th>Diâmetro</th><th>Alimentadores</th><th>Elastano</th></tr></thead>
            <tbody>
              {% set teares = e.teares or [] %}
              {% if teares|length == 0 %}
                <tr><td colspan="7" class="muted">Sem teares cadastrados.</td></tr>
              {% else %}
                {% for t in teares %}
                  <tr>
                    <td>{{ t.marca }}</td>
                    <td>{{ t.modelo|default('') }}</td>
                    <td>{{ t.tipo|default('') }}</td>
                    <td>{{ t.finura }}</td>
                    <td>{{ t.diametro }}</td>
                    <td>{{ t.alimentadores }}</td>
                    <td>{{ t.elastano }}</td>
                  </tr>
                {% endfor %}
              {% endif %}
            </tbody>
          </table>
          <p class="muted">ID: {{ e.id }} • <a href="/malharia_info/{{ e.id }}">abrir /malharia_info/{{ e.id }}</a></p>
        </div>
      {% endfor %}
    </body></html>
    """
    return render_template_string(html, empresas=empresas)

# --- DEBUG: (re)criar/normalizar DEMO de forma idempotente (remova depois) ---
from werkzeug.security import generate_password_hash
from datetime import datetime, timedelta

@app.get("/utils/demo-reseed")
def utils_demo_reseed():
    DEMO_TAG = "[DEMO]"
    DEFAULT_HASH = generate_password_hash("demo123")
    empresas_def = [
        ("Malharia Modelo",   "Modelo",   "SC", "Blumenau",      "modelo@teste.com"),
        ("Fios & Malhas",     "Fios",     "SP", "Americana",     "fios@teste.com"),
        ("TramaSul Têxteis",  "TramaSul", "RS", "Caxias do Sul", "tramasul@teste.com"),
        ("Tecelagem Paraná",  "Paraná",   "PR", "Maringá",       "parana@teste.com"),
        ("Nordeste Knit",     "Nordeste", "CE", "Fortaleza",     "nordeste@teste.com"),
        ("Mineira Malharia",  "Mineira",  "MG", "Juiz de Fora",  "mineira@teste.com"),
    ]
    teares_def = {
        f"{DEMO_TAG} Modelo":   [("Mayer",28,30,90,"Sim"), ("Terrot",32,34,96,"Não"), ("Pilotelli",24,26,72,"Sim")],
        f"{DEMO_TAG} Fios":     [("Unitex",28,30,84,"Não"), ("Santoni",20,22,48,"Sim")],
        f"{DEMO_TAG} TramaSul": [("Terrot",34,36,108,"Sim"), ("Mayer",18,20,36,"Não"), ("Pilotelli",26,26,64,"Sim")],
        f"{DEMO_TAG} Paraná":   [("Mayer",28,34,96,"Sim"), ("Unitex",24,30,80,"Não")],
        f"{DEMO_TAG} Nordeste": [("Santoni",32,34,100,"Sim"), ("Terrot",28,30,90,"Não"), ("Mayer",22,26,60,"Sim")],
        f"{DEMO_TAG} Mineira":  [("Pilotelli",28,30,88,"Sim"), ("Terrot",24,26,68,"Não")],
    }

    criadas, atualizadas, teares_criados = 0, 0, 0

    for (nome, apelido_base, estado, cidade, email) in empresas_def:
        apelido = f"{DEMO_TAG} {apelido_base}"
        emp = Empresa.query.filter((Empresa.email==email) | (Empresa.apelido==apelido)).first()
        if not emp:
            emp = Empresa(nome=nome, apelido=apelido, estado=estado, cidade=cidade,
                          email=email, senha=DEFAULT_HASH)
            db.session.add(emp)
            criadas += 1
        else:
            # garante apelido com DEMO e campos essenciais
            emp.nome = nome
            emp.apelido = apelido
            if not emp.senha: emp.senha = DEFAULT_HASH
            emp.estado, emp.cidade, emp.email = estado, cidade, email
            atualizadas += 1

        # marca como pago/ativo se campos existirem
        if hasattr(emp, "status_pagamento"): emp.status_pagamento = "aprovado"
        if hasattr(emp, "data_pagamento"): emp.data_pagamento = datetime.utcnow()
        for f, v in [("telefone","(00) 0000-0000"), ("responsavel_nome","Demo"), ("responsavel_sobrenome","Seed")]:
            if hasattr(emp, f) and getattr(emp, f) in (None, ""): setattr(emp, f, v)

        db.session.flush()  # garante emp.id

        # cria teares só se ainda não houver
        if Tear.query.filter_by(empresa_id=emp.id).count() == 0:
            for (marca, finura, diametro, alimentadores, elastano) in teares_def.get(apelido, []):
                t = Tear(empresa_id=emp.id, marca=marca, finura=int(finura),
                         diametro=int(diametro), alimentadores=int(alimentadores),
                         elastano=elastano)
                # se seu modelo exigir:
                if hasattr(Tear, "modelo") and getattr(t, "modelo", None) in (None, ""):
                    setattr(t, "modelo", "DEMO-01")
                if hasattr(Tear, "tipo") and getattr(t, "tipo", None) in (None, ""):
                    setattr(t, "tipo", "circular")
                if hasattr(Tear, "ativo"):
                    t.ativo = True
                db.session.add(t)
                teares_criados += 1

    db.session.commit()
    return f"OK: empresas criadas={criadas}, atualizadas={atualizadas}, teares criados={teares_criados}"

# --- DEBUG: listar apenas as empresas DEMO com teares (remover depois) ---
from flask import render_template_string
from sqlalchemy import or_

if __name__ == '__main__':
    from seed_demo import seed
    seed()  # cria dados demo na primeira execução
    app.run(debug=True, host='0.0.0.0', port=5000)
