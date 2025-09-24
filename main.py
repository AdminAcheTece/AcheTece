from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    render_template_string, send_file, jsonify, abort
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
import re
import uuid
import logging
import json
import requests
from unicodedata import normalize
from sqlalchemy import inspect, text, or_, func
from pathlib import Path
import random

# --------------------------------------------------------------------
# Configuração básica
# --------------------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.INFO)
app.config['SECRET_KEY'] = os.getenv('SECRET_KEY', 'dev-key-unsafe')
app.config['PREFERRED_URL_SCHEME'] = 'https'

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
STATIC_DIR = os.path.join(BASE_DIR, 'static')
CACHE_DIR = os.path.join(BASE_DIR, 'cache_ibge')
os.makedirs(CACHE_DIR, exist_ok=True)

def base_url():
    env_url = os.getenv('APP_BASE_URL')
    if env_url:
        return env_url.rstrip('/')
    try:
        return request.url_root.rstrip('/')
    except Exception:
        return "http://localhost:5000"

# Banco
db_url = os.getenv('DATABASE_URL', 'sqlite:///banco.db')
if db_url.startswith('postgres://'):
    db_url = db_url.replace('postgres://', 'postgresql+psycopg://', 1)
elif db_url.startswith('postgresql://'):
    db_url = db_url.replace('postgresql://', 'postgresql+psycopg://', 1)
if db_url.startswith('postgresql+psycopg://') and 'sslmode=' not in db_url:
    db_url += ('&' if '?' in db_url else '?') + 'sslmode=require'
app.config['SQLALCHEMY_DATABASE_URI'] = db_url
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
db = SQLAlchemy(app)

# E-mail
app.config['MAIL_SERVER'] = os.getenv('MAIL_SERVER', 'smtp.gmail.com')
app.config['MAIL_PORT'] = int(os.getenv('MAIL_PORT', 587))
app.config['MAIL_USE_TLS'] = os.getenv('MAIL_USE_TLS', 'true').lower() == 'true'
app.config['MAIL_USERNAME'] = os.getenv('MAIL_USERNAME')
app.config['MAIL_PASSWORD'] = os.getenv('MAIL_PASSWORD')
mail = Mail(app)

# Mercado Pago (mantido para compat)
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN") or os.getenv("MERCADO_PAGO_TOKEN", "")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
PLAN_MONTHLY = float(os.getenv("PLAN_MONTHLY", "2.00"))
PLAN_YEARLY = float(os.getenv("PLAN_YEARLY", "2.00"))

# DEMO
DEMO_MODE = os.getenv("DEMO_MODE", "true").lower() == "true"
DEMO_TOKEN = os.getenv("DEMO_TOKEN", "localdemo")
SEED_TOKEN = os.getenv("SEED_TOKEN", "ACHETECE")

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _norm(s: str) -> str:
    return normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII').strip().lower()

def gerar_token(email):
    return URLSafeTimedSerializer(app.config['SECRET_KEY']).dumps(email, salt='recupera-senha')

def enviar_email_recuperacao(email, nome_empresa=""):
    token = gerar_token(email)
    link = f"{base_url()}{url_for('redefinir_senha', token=token)}"
    msg = Message(
        subject="Redefinição de Senha - AcheTece",
        sender=app.config['MAIL_USERNAME'],
        recipients=[email]
    )
    msg.html = render_template_string("""
    <html><body style="font-family:Arial,sans-serif">
      <h2>Redefinição de Senha</h2>
      <p>Olá {{ nome }},</p>
      <p>Clique abaixo para criar uma nova senha (válido por 1h):</p>
      <p><a href="{{ link }}" target="_blank">Redefinir Senha</a></p>
    </body></html>
    """, nome=nome_empresa or email, link=link)
    mail.send(msg)

def login_admin_requerido(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('admin_email') != 'gestao.achetece@gmail.com':
            flash('Acesso não autorizado.')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# --------------------------------------------------------------------
# Modelos
# --------------------------------------------------------------------
class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255))
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

class Tear(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    marca = db.Column(db.String(100), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)            # MONO | DUPLA | ...
    finura = db.Column(db.Integer, nullable=False)              # galga
    diametro = db.Column(db.Integer, nullable=False)
    alimentadores = db.Column(db.Integer, nullable=False)
    elastano = db.Column(db.String(10), nullable=False)         # Sim | Não
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
    usuario = db.relationship('Usuario', backref=db.backref('cliente_profile', uselist=False))

# --------------------------------------------------------------------
# Setup inicial (idempotente)
# --------------------------------------------------------------------
def _ensure_auth_layer_and_link():
    try:
        Usuario.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as e:
        app.logger.warning(f"create usuario table: {e}")
    try:
        insp = inspect(db.engine)
        cols = [c['name'] for c in insp.get_columns('empresa')]
        if 'user_id' not in cols:
            db.session.execute(text('ALTER TABLE empresa ADD COLUMN user_id INTEGER'))
            db.session.commit()
    except Exception as e:
        app.logger.warning(f"add user_id to empresa failed: {e}")
    try:
        empresas = Empresa.query.all()
        for e in empresas:
            if e.user_id:
                continue
            u = Usuario.query.filter_by(email=e.email).first()
            if not u:
                u = Usuario(email=e.email, senha_hash=e.senha, role=None, is_active=True)
                db.session.add(u)
                db.session.flush()
            e.user_id = u.id
        db.session.commit()
    except Exception as e:
        app.logger.warning(f"backfill usuarios from empresas failed: {e}")

def _ensure_cliente_profile_table():
    try:
        ClienteProfile.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as e:
        app.logger.warning(f"create cliente_profile table: {e}")

with app.app_context():
    try:
        db.create_all()
        _ensure_auth_layer_and_link()
        _ensure_cliente_profile_table()
        app.logger.info("Migrações de inicialização OK (create_all + ajustes).")
    except Exception as e:
        app.logger.error(f"Startup migrations failed: {e}")

# --------------------------------------------------------------------
# Sessão / Regras de acesso
# --------------------------------------------------------------------
def _get_empresa_usuario_da_sessao():
    if 'empresa_id' not in session:
        return None, None
    emp = Empresa.query.get(session['empresa_id'])
    if not emp:
        session.pop('empresa_id', None)
        session.pop('empresa_apelido', None)
        return None, None
    u = emp.usuario or Usuario.query.filter_by(email=emp.email).first()
    if not u:
        u = Usuario(email=emp.email, senha_hash=e.senha, role=None, is_active=True)  # type: ignore
        db.session.add(u); db.session.flush()
        emp.user_id = u.id
        db.session.commit()
    elif not emp.user_id:
        emp.user_id = u.id
        db.session.commit()
    session['empresa_apelido'] = emp.apelido or emp.nome or emp.email.split('@')[0]
    return emp, u

def assinatura_ativa_requerida(f):
    @wraps(f)
    def wrapper(*args, **kwargs):
        emp, _ = _get_empresa_usuario_da_sessao()
        if not emp:
            flash("Faça login para continuar.", "error")
            return redirect(url_for("login"))
        is_demo = DEMO_MODE or (emp.apelido or emp.nome or "").startswith("[DEMO]")
        if is_demo:
            return f(*args, **kwargs)
        status = (emp.status_pagamento or "pendente").lower()
        if status not in ("ativo", "aprovado"):
            flash("Ative seu plano para acessar esta funcionalidade.", "error")
            return redirect(url_for("painel_malharia"))
        return f(*args, **kwargs)
    return wrapper

# --------------------------------------------------------------------
# INDEX: sempre lista e filtra progressivamente
# --------------------------------------------------------------------
def _num_key(x):
    try:
        return float(str(x).replace(",", "."))
    except Exception:
        return 0.0

def _to_int(s):
    try:
        return int(float(str(s).replace(",", ".")))
    except Exception:
        return None

@app.route("/", methods=["GET"])
def index():
    # ---- helpers locais (seguros com dígitos/virgula) ----
    def _num_key(x):
        try:
            return float(str(x).replace(",", "."))
        except Exception:
            return 0.0

    def _to_int(s):
        try:
            return int(float(str(s).replace(",", ".").strip()))
        except Exception:
            return None

    # >>> Agora só GET (request.args), nada de POST/values
    v = request.args
    filtros = {
        "tipo":     (v.get("tipo") or "").strip(),
        "diâmetro": (v.get("diâmetro") or v.get("diametro") or "").strip(),
        "galga":    (v.get("galga") or "").strip(),
        "estado":   (v.get("estado") or "").strip(),
        "cidade":   (v.get("cidade") or "").strip(),
    }

    # ===== Base SEM restrição: TODOS os teares =====
    q_base = Tear.query.outerjoin(Empresa)

    # Se existir campo 'ativo' em Tear e você quiser considerar só ativos:
    try:
        q_base = q_base.filter(Tear.ativo.is_(True))
    except Exception:
        pass

    # ===== Opções dos selects (a partir de TODOS) =====
    # Agora calculamos 'estado' sempre; 'cidade' fica condicionada ao estado selecionado
    opcoes = {"tipo": [], "diâmetro": [], "galga": [], "estado": [], "cidade": []}

    # Vamos acumular estados e mapear cidades por UF
    from collections import defaultdict
    cidades_por_uf = defaultdict(set)
    tipos_set, diam_set, galga_set, estados_set = set(), set(), set(), set()

    for t_tipo, t_diam, t_fin, e_uf, e_cid in q_base.with_entities(
        Tear.tipo, Tear.diametro, Tear.finura, Empresa.estado, Empresa.cidade
    ).all():
        if t_tipo:
            tipos_set.add(t_tipo)
        if t_diam is not None:
            diam_set.add(str(t_diam))
        if t_fin is not None:
            galga_set.add(str(t_fin))
        if e_uf:
            estados_set.add(e_uf)
            if e_cid:
                cidades_por_uf[e_uf].add(e_cid)

    opcoes["tipo"] = sorted(tipos_set)
    opcoes["diâmetro"] = sorted(diam_set, key=_num_key)
    opcoes["galga"] = sorted(galga_set, key=_num_key)
    opcoes["estado"] = sorted(estados_set)
    # cidades: só do estado selecionado; caso não tenha estado, deixamos lista vazia
    if filtros["estado"]:
        opcoes["cidade"] = sorted(cidades_por_uf.get(filtros["estado"], set()))
    else:
        opcoes["cidade"] = []

    # ===== Aplica filtros progressivos =====
    q = q_base
    if filtros["tipo"]:
        q = q.filter(db.func.lower(Tear.tipo) == filtros["tipo"].lower())

    di = _to_int(filtros["diâmetro"])
    if di is not None:
        q = q.filter(Tear.diametro == di)

    ga = _to_int(filtros["galga"])
    if ga is not None:
        q = q.filter(Tear.finura == ga)

    if filtros["estado"]:
        q = q.filter(db.func.lower(Empresa.estado) == filtros["estado"].lower())
    if filtros["cidade"]:
        q = q.filter(db.func.lower(Empresa.cidade) == filtros["cidade"].lower())

    # ===== Paginação (default 20 por página; aceito ?pp=50) =====
    pagina = max(1, int(request.args.get("pagina", 1) or 1))
    por_pagina = int(request.args.get("pp", 20) or 20)
    por_pagina = max(1, min(100, por_pagina))  # guarda-chuva

    total = q.count()
    q = q.order_by(Tear.id.desc())
    teares_page = q.offset((pagina - 1) * por_pagina).limit(por_pagina).all()
    total_paginas = max(1, (total + por_pagina - 1) // por_pagina)

    # ===== Monta linhas da tabela =====
    resultados = []
    for tear in teares_page:
        emp = getattr(tear, "empresa", None)
        apelido = (
            (emp.apelido if emp else None)
            or (getattr(emp, "nome_fantasia", None) if emp else None)
            or (getattr(emp, "nome", None) if emp else None)
            or ((emp.email.split("@")[0]) if emp and getattr(emp, "email", None) else None)
            or "—"
        )
        numero = re.sub(r"\D", "", (emp.telefone or "")) if emp else ""
        contato_link = f"https://wa.me/{'55' + numero if numero and not numero.startswith('55') else numero}" if numero else None

        item = {
            "empresa": apelido,
            "tipo": tear.tipo or "—",
            "galga": tear.finura if tear.finura is not None else "—",
            "diametro": tear.diametro if tear.diametro is not None else "—",
            "alimentadores": getattr(tear, "alimentadores", None) if getattr(tear, "alimentadores", None) is not None else "—",
            "uf": (emp.estado if emp and getattr(emp, "estado", None) else "—"),
            "cidade": (emp.cidade if emp and getattr(emp, "cidade", None) else "—"),
            "contato": contato_link,
            # chaves duplicadas (compat possíveis no template)
            "Empresa": apelido,
            "Tipo": tear.tipo or "—",
            "Galga": tear.finura if tear.finura is not None else "—",
            "Diâmetro": tear.diametro if tear.diametro is not None else "—",
            "Alimentadores": getattr(tear, "alimentadores", None) if getattr(tear, "alimentadores", None) is not None else "—",
            "UF": (emp.estado if emp and getattr(emp, "estado", None) else "—"),
            "Cidade": (emp.cidade if emp and getattr(emp, "cidade", None) else "—"),
            "Contato": contato_link,
        }
        resultados.append(item)

    app.logger.info({"rota": "index", "total_encontrado": total, "pagina": pagina, "pp": por_pagina, "filtros": filtros})

    return render_template(
        "index.html",
        opcoes=opcoes,
        filtros=filtros,
        resultados=resultados,   # linhas da página
        teares=teares_page,      # se houver cards
        total=total,             # *** use isto para exibir "N resultados"
        pagina=pagina,
        por_pagina=por_pagina,
        total_paginas=total_paginas,
        # compat: se o template ainda referenciar 'estados', entregamos a mesma lista
        estados=opcoes["estado"],
    )

# --------------------------------------------------------------------
# Login / Sessão  (FLUXO MULTI-ETAPAS OLX-LIKE)
# --------------------------------------------------------------------

# 1) /login (somente GET) – mantém endpoint 'login' para compatibilidade
@app.get("/login", endpoint="login")
def view_login():
    return render_template("login.html")

# 2) Escolher método de acesso (código por e-mail ou senha)
@app.get("/login/metodo")
def view_login_method():
    email = (request.args.get("email") or "").strip()
    if not email:
        flash("Informe um e-mail para continuar.", "warning")
        return redirect(url_for("login"))
    return render_template("login_method.html", email=email)

# 3) Código por e-mail – POST dispara envio (por ora, apenas redireciona; enviaremos de fato no próximo passo)
@app.post("/login/codigo")
def post_login_code():
    email = (request.form.get("email") or "").strip()
    if not email:
        flash("Informe um e-mail válido.", "warning")
        return redirect(url_for("login"))
    # (envio real do e-mail e gravação de OTP entram na próxima etapa)
    return redirect(url_for("get_login_code", email=email))

# 4) Tela para digitar o código
@app.get("/login/codigo", endpoint="get_login_code")
def get_login_code():
    email = (request.args.get("email") or "").strip()
    if not email:
        return redirect(url_for("login"))
    return render_template("login_code.html", email=email)

# 5) Reenviar código
@app.get("/login/codigo/reenviar")
def resend_login_code():
    email = (request.args.get("email") or "").strip()
    if not email:
        return redirect(url_for("login"))
    # (reenviar de fato entraremos na próxima etapa)
    flash("Enviamos um novo código para o seu e-mail.", "success")
    return redirect(url_for("get_login_code", email=email))

# 6) Validar código (por enquanto apenas mantém fluxo sem 404)
@app.post("/login/codigo/validar")
def validate_login_code():
    email = (request.form.get("email") or "").strip()
    _otp = "".join((request.form.get(k, "") for k in ("d1","d2","d3","d4","d5","d6")))
    # (validação real entraremos na próxima etapa)
    flash("Validação do código pendente.", "info")
    return redirect(url_for("get_login_code", email=email))

# 7) Entrar com senha (GET da tela)
@app.get("/login/senha")
def view_login_password():
    email = (request.args.get("email") or "").strip()
    if not email:
        return redirect(url_for("login"))
    return render_template("login_password.html", email=email)

# 8) Entrar com senha (POST) – lógica antiga movida pra cá
@app.post("/login/senha/entrar")
def post_login_password():
    email = (request.form.get("email") or "").strip().lower()
    senha = (request.form.get("senha") or "")
    user = Empresa.query.filter(func.lower(Empresa.email) == email).first()
    GENERIC_FAIL = "E-mail ou senha incorretos. Tente novamente."
    if not user:
        flash(GENERIC_FAIL)
        return redirect(url_for("view_login_password", email=email))
    ok = False
    try:
        ok = check_password_hash(user.senha, senha)
    except Exception as e:
        app.logger.warning(f"[LOGIN WARN] check_password_hash: {e}")
    if not ok:
        flash(GENERIC_FAIL)
        return redirect(url_for("view_login_password", email=email))
    # Gate de pagamento (exceto DEMO)
    if not DEMO_MODE and (user.status_pagamento or "").lower() not in ("aprovado", "ativo"):
        flash("Pagamento ainda não aprovado.")
        return redirect(url_for("login"))
    # autentica sessão
    session["empresa_id"] = user.id
    session["empresa_apelido"] = user.apelido or user.nome or user.email.split("@")[0]
    return redirect(url_for("painel_malharia"))

# 9) Logout (mantido)
@app.route("/logout")
def logout():
    session.pop("empresa_id", None)
    session.pop("empresa_apelido", None)
    return redirect(url_for("index"))

# 10) Cadastro (mínimo para não dar 404) – tela nova que você já tem
@app.get("/cadastro")
def cadastro_get():
    return render_template("cadastro.html")

@app.post("/cadastro")
def cadastro_post():
    # (implementação completa depois; por ora evita 404 e mantém o fluxo)
    flash("Cadastro recebido.", "success")
    return redirect(url_for("login"))


# --------------------------------------------------------------------
# Painel da malharia + CRUD de teares
# --------------------------------------------------------------------
@app.route('/painel_malharia')
def painel_malharia():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))
    teares = Tear.query.filter_by(empresa_id=emp.id).all()
    is_ativa = (emp.status_pagamento or "pendente") == "ativo"
    return render_template('painel_malharia.html', empresa=emp, teares=teares, assinatura_ativa=is_ativa)

@app.route('/cadastrar_teares', methods=['GET', 'POST'])
@assinatura_ativa_requerida
def cadastrar_teares():
    emp, _ = _get_empresa_usuario_da_sessao()
    if request.method == 'POST':
        try:
            novo_tear = Tear(
                marca=(request.form['marca'] or '').strip(),
                modelo=(request.form['modelo'] or '').strip(),
                tipo=(request.form['tipo'] or '').strip(),
                finura=int(request.form['finura']),
                diametro=int(request.form['diametro']),
                alimentadores=int(request.form['alimentadores']),
                elastano=(request.form['elastano'] or '').strip(),
                empresa_id=emp.id
            )
        except Exception as e:
            flash(f"Campos inválidos: {e}", "error")
            return render_template('cadastrar_teares.html')
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
        try:
            tear.marca = (request.form['marca'] or '').strip()
            tear.modelo = (request.form['modelo'] or '').strip()
            tear.tipo = (request.form['tipo'] or '').strip()
            tear.finura = int(request.form['finura'])
            tear.diametro = int(request.form['diametro'])
            tear.alimentadores = int(request.form['alimentadores'])
            tear.elastano = (request.form['elastano'] or '').strip()
        except Exception as e:
            flash(f"Campos inválidos: {e}", "error")
            return render_template('editar_tear.html', tear=tear)
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

# --------------------------------------------------------------------
# Exportação CSV (usa filtros da home)
# --------------------------------------------------------------------
@app.route('/exportar')
def exportar():
    filtros_raw = {
        'tipo'    : (request.args.get('tipo', '') or '').strip(),
        'diâmetro': (request.args.get('diâmetro', '') or request.args.get('diametro', '') or '').strip(),
        'galga'   : (request.args.get('galga', '') or '').strip(),
        'estado'  : (request.args.get('estado', '') or '').strip(),
        'cidade'  : (request.args.get('cidade', '') or '').strip(),
    }
    def to_int(s):
        s = re.sub(r'\D', '', (s or ''))
        return int(s) if s else None
    def to_float(s):
        s = (s or '').strip().replace(',', '.')
        s = re.sub(r'[^0-9\.]', '', s)
        return float(s) if s else None

    galga    = to_int(filtros_raw['galga'])
    diametro = to_float(filtros_raw['diâmetro'])
    query = Tear.query.join(Empresa)
    if filtros_raw['tipo']:
        query = query.filter(Tear.tipo == filtros_raw['tipo'])
    if diametro is not None:
        query = query.filter(func.round(Tear.diametro, 2) == round(diametro, 2))
    if galga is not None:
        query = query.filter(Tear.finura == galga)
    if filtros_raw['estado']:
        query = query.filter(Empresa.estado == filtros_raw['estado'])
    if filtros_raw['cidade']:
        query = query.filter(Empresa.cidade == filtros_raw['cidade'])
    teares = query.all()

    output = io.StringIO()
    writer = csv.writer(output)
    writer.writerow(['Empresa', 'Marca', 'Modelo', 'Tipo', 'Diâmetro', 'Galga', 'Alimentadores', 'Elastano', 'Estado', 'Cidade'])
    for tear in teares:
        writer.writerow([
            tear.empresa.apelido or tear.empresa.nome or tear.empresa.email.split('@')[0],
            tear.marca, tear.modelo, tear.tipo, tear.diametro, tear.finura,
            tear.alimentadores, tear.elastano,
            tear.empresa.estado, tear.empresa.cidade
        ])
    output.seek(0)
    return send_file(
        io.BytesIO(output.read().encode('utf-8')),
        mimetype='text/csv',
        as_attachment=True,
        download_name='teares_filtrados.csv'
    )

# --------------------------------------------------------------------
# Cadastro/edição de empresa (essencial)
# --------------------------------------------------------------------
@app.route('/cadastrar_empresa', methods=['GET', 'POST'])
def cadastrar_empresa():
    estados = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']
    if request.method == 'POST':
        nome = request.form['nome']; apelido = request.form['apelido']
        email = request.form['email'].lower().strip(); senha = request.form['senha']
        cidade = request.form['cidade']; estado = request.form['estado']
        telefone = re.sub(r'\D', '', request.form.get('telefone',''))
        responsavel_nome = (request.form.get('responsavel_nome') or '').strip()
        responsavel_sobrenome = (request.form.get('responsavel_sobrenome') or '').strip()
        erros = {}
        if len(telefone) < 10 or len(telefone) > 13:
            erros['telefone'] = 'Telefone inválido.'
        if Empresa.query.filter_by(nome=nome).first(): erros['nome'] = 'Nome já existe.'
        if apelido and Empresa.query.filter_by(apelido=apelido).first(): erros['apelido'] = 'Apelido em uso.'
        if Empresa.query.filter_by(email=email).first(): erros['email'] = 'E-mail já cadastrado.'
        if estado not in estados: erros['estado'] = 'Estado inválido.'
        if not responsavel_nome or len(re.sub(r'[^A-Za-zÀ-ÿ]', '', responsavel_nome)) < 2:
            erros['responsavel_nome'] = 'Informe o nome do responsável.'
        if erros:
            return render_template('cadastrar_empresa.html', erro='Corrija os campos.', erros=erros,
                                   estados=estados, nome=nome, apelido=apelido, email=email,
                                   cidade=cidade, estado=estado, telefone=telefone,
                                   responsavel_nome=responsavel_nome, responsavel_sobrenome=responsavel_sobrenome)
        nova_empresa = Empresa(
            nome=nome, apelido=apelido or None, email=email,
            senha=generate_password_hash(senha),
            cidade=cidade, estado=estado, telefone=telefone,
            status_pagamento='pendente',
            responsavel_nome=responsavel_nome, responsavel_sobrenome=responsavel_sobrenome or None
        )
        db.session.add(nova_empresa); db.session.commit()
        session['empresa_id'] = nova_empresa.id
        session['empresa_apelido'] = nova_empresa.apelido or nova_empresa.nome or nova_empresa.email.split('@')[0]
        flash("Cadastro concluído!", "success")
        return redirect(url_for('painel_malharia'))
    return render_template('cadastrar_empresa.html', estados=estados)

@app.route('/editar_empresa', methods=['GET', 'POST'])
def editar_empresa():
    if 'empresa_id' not in session:
        return redirect(url_for('login'))
    empresa = Empresa.query.get(session['empresa_id'])
    if not empresa:
        session.clear(); return redirect(url_for('login'))
    estados = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']
    if request.method == 'GET':
        return render_template('editar_empresa.html',
                               estados=estados,
                               nome=empresa.nome or '', apelido=empresa.apelido or '',
                               email=empresa.email or '', cidade=empresa.cidade or '',
                               estado=empresa.estado or '', telefone=empresa.telefone or '',
                               responsavel_nome=(empresa.responsavel_nome or ''),
                               responsavel_sobrenome=(empresa.responsavel_sobrenome or ''))
    # POST
    nome = request.form.get('nome','').strip()
    apelido = request.form.get('apelido','').strip()
    email = request.form.get('email','').strip().lower()
    senha = request.form.get('senha','').strip()
    cidade = request.form.get('cidade','').strip()
    estado = request.form.get('estado','').strip()
    telefone = re.sub(r'\D', '', request.form.get('telefone',''))
    responsavel_nome = (request.form.get('responsavel_nome') or '').strip()
    responsavel_sobrenome = (request.form.get('responsavel_sobrenome') or '').strip()

    erros = {}
    if telefone and (len(telefone) < 10 or len(telefone) > 13):
        erros['telefone'] = 'Telefone inválido.'
    if nome and nome != (empresa.nome or '') and Empresa.query.filter_by(nome=nome).first():
        erros['nome'] = 'Nome já existe.'
    if apelido and apelido != (empresa.apelido or '') and Empresa.query.filter_by(apelido=apelido).first():
        erros['apelido'] = 'Apelido já em uso.'
    if email and email != (empresa.email or '') and Empresa.query.filter_by(email=email).first():
        erros['email'] = 'E-mail já cadastrado.'
    if estado and estado not in estados:
        erros['estado'] = 'Estado inválido.'
    if not responsavel_nome or len(re.sub(r'[^A-Za-zÀ-ÿ]', '', responsavel_nome)) < 2:
        erros['responsavel_nome'] = 'Informe o primeiro nome do responsável.'
    if erros:
        return render_template('editar_empresa.html',
                               erro='Corrija os campos.', erros=erros, estados=estados,
                               nome=nome or empresa.nome, apelido=apelido or empresa.apelido,
                               email=email or empresa.email, cidade=cidade or empresa.cidade,
                               estado=estado or empresa.estado, telefone=telefone or empresa.telefone,
                               responsavel_nome=responsavel_nome or (empresa.responsavel_nome or ''),
                               responsavel_sobrenome=responsavel_sobrenome or (empresa.responsavel_sobrenome or ''))

    empresa.nome = nome or empresa.nome
    empresa.apelido = apelido or empresa.apelido
    empresa.email = email or empresa.email
    empresa.cidade = cidade or empresa.cidade
    empresa.estado = estado or empresa.estado
    empresa.telefone = telefone or empresa.telefone
    empresa.responsavel_nome = responsavel_nome or empresa.responsavel_nome
    empresa.responsavel_sobrenome = responsavel_sobrenome or None
    if senha:
        empresa.senha = generate_password_hash(senha)
    db.session.commit()
    session['empresa_apelido'] = empresa.apelido or empresa.nome or empresa.email.split('@')[0]
    return redirect(url_for('editar_empresa', ok=1))

# --------------------------------------------------------------------
# Admin: empresas
# --------------------------------------------------------------------
@app.route('/admin/login', methods=['GET', 'POST'])
def admin_login():
    if request.method == 'POST':
        email = request.form.get('email'); senha = request.form.get('senha')
        if email == 'gestao.achetece@gmail.com' and senha == '123adm@achetece':
            session['admin_email'] = email
            flash('Login de administrador realizado.', 'success')
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

@app.route('/admin/empresas', methods=['GET', 'POST'])
@login_admin_requerido
def admin_empresas():
    pagina = int(request.args.get('pagina', 1))
    por_pagina = 10
    status = ''; data_inicio = ''; data_fim = ''
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
            query = query.filter(Empresa.data_pagamento >= datetime.strptime(data_inicio, "%Y-%m-%d"))
        if data_fim:
            query = query.filter(Empresa.data_pagamento <= datetime.strptime(data_fim, "%Y-%m-%d"))
    total = query.count()
    empresas = query.order_by(Empresa.nome).offset((pagina - 1) * por_pagina).limit(por_pagina).all()
    total_paginas = (total + por_pagina - 1) // por_pagina
    return render_template('admin_empresas.html',
                           empresas=empresas, pagina=pagina, total_paginas=total_paginas,
                           status=status, data_inicio=data_inicio, data_fim=data_fim)

@app.route('/admin/editar_status/<int:empresa_id>', methods=['GET', 'POST'])
@login_admin_requerido
def admin_editar_status(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    novo_status = request.values.get('status') or ('ativo' if empresa.status_pagamento != 'ativo' else 'pendente')
    empresa.status_pagamento = novo_status
    empresa.data_pagamento = datetime.utcnow() if novo_status == 'ativo' else None
    db.session.commit()
    flash(f'Status de "{empresa.apelido or empresa.nome}" atualizado para {novo_status}.', 'success')
    return redirect(url_for('admin_empresas',
                            pagina=request.args.get('pagina', 1),
                            status=request.args.get('status', ''),
                            data_inicio=request.args.get('data_inicio', ''),
                            data_fim=request.args.get('data_fim', '')))

@app.route('/admin/excluir_empresa/<int:empresa_id>', methods=['POST'])
@login_admin_requerido
def excluir_empresa(empresa_id):
    if session.get('admin_email') != 'gestao.achetece@gmail.com':
        flash('Acesso não autorizado.')
        return redirect(url_for('login'))
    empresa = Empresa.query.get_or_404(empresa_id)
    db.session.delete(empresa); db.session.commit()
    flash(f'Empresa "{empresa.nome}" excluída com sucesso!')
    return redirect(url_for('admin_empresas'))

# --------------------------------------------------------------------
# Admin: seed/impersonação (única família, sem duplicação)
# --------------------------------------------------------------------
DEMO_FILTER = or_(
    Empresa.apelido.ilike("%[DEMO]%"),
    Empresa.email.ilike("%@achetece.demo")
)

def _seed_ok():
    return request.args.get("token") == SEED_TOKEN

def _cria_teares_fake(empresa, n):
    tipos = ["MONO", "DUPLA"]
    marcas = ["Mayer", "Terrot", "Santoni", "Pilotelli", "Unitex"]
    modelos = ["Relanit", "Inovit", "DEMO-01", "DEMO-02", "DEMO-03"]
    diametros = [18, 20, 22, 24, 26, 28, 30, 32, 34, 36]
    galgas = [14, 18, 20, 22, 24, 26, 28, 30, 32]
    alimentadores_pool = [36, 48, 60, 72, 84, 90, 96, 108]
    novos = []
    for _ in range(max(0, int(n or 0))):
        t = Tear(
            marca=random.choice(marcas),
            modelo=random.choice(modelos),
            tipo=random.choice(tipos),
            finura=random.choice(galgas),
            diametro=random.choice(diametros),
            alimentadores=random.choice(alimentadores_pool),
            elastano=random.choice(["Sim", "Não"]),
            empresa_id=empresa.id
        )
        novos.append(t)
    if novos:
        db.session.bulk_save_objects(novos); db.session.commit()
    return len(novos)

def _topup(empresa, minimo):
    atual = Tear.query.filter_by(empresa_id=empresa.id).count()
    if atual >= (minimo or 0): return 0
    return _cria_teares_fake(empresa, (minimo - atual))

@app.route("/admin/seed_teares")
def admin_seed_teares():
    if not _seed_ok(): return "Não autorizado", 403
    empresa_id = request.args.get("empresa_id", type=int)
    n = request.args.get("n", default=5, type=int)
    if not empresa_id: return "Informe empresa_id", 400
    emp = Empresa.query.get_or_404(empresa_id)
    qtd = _cria_teares_fake(emp, n)
    return f"OK: +{qtd} teares em {emp.apelido or emp.nome or getattr(emp, 'nome_fantasia', emp.id)} (id={emp.id})."

@app.route("/admin/seed_teares_all")
def admin_seed_teares_all():
    if not _seed_ok(): return "Não autorizado", 403
    escopo = (request.args.get("escopo") or "demo").lower()  # demo|pagantes|todas
    uf = request.args.get("uf")
    ids = request.args.get("ids")
    n = request.args.get("n", type=int)
    minimo = request.args.get("min", type=int)

    q = Empresa.query
    if ids:
        lista = [int(x) for x in ids.split(",") if x.strip().isdigit()]
        q = q.filter(Empresa.id.in_(lista))
    else:
        if escopo == "demo":
            q = q.filter(DEMO_FILTER)
        elif escopo == "pagantes":
            q = q.filter(Empresa.status_pagamento == "aprovado")
        if uf:
            q = q.filter(func.upper(Empresa.estado) == uf.upper())

    empresas = q.order_by(Empresa.id.desc()).all()
    if not empresas: 
        return "Nenhuma empresa encontrada para o filtro.", 200

    total_empresas = len(empresas)
    total_add = 0
    rel = []
    for e in empresas:
        add = _topup(e, minimo) if minimo else _cria_teares_fake(e, n or 5)
        total_add += add
        rel.append(f"{e.id}:{add}")
    return f"OK: {total_add} teares adicionados em {total_empresas} empresas. Detalhe: {'; '.join(rel)}"

@app.route("/utils/empresas_json")
def utils_empresas_json():
    escopo = (request.args.get("escopo") or "demo").lower()
    uf = request.args.get("uf")
    q = Empresa.query
    if escopo == "demo":
        q = q.filter(DEMO_FILTER)
    elif escopo == "pagantes":
        q = q.filter(Empresa.status_pagamento == "aprovado")
    if uf:
        q = q.filter(func.upper(Empresa.estado) == uf.upper())
    empresas = q.order_by(Empresa.id.desc()).all()
    data = []
    for e in empresas:
        cnt = Tear.query.filter_by(empresa_id=e.id).count()
        data.append({
            "id": e.id,
            "apelido": e.apelido or e.nome or getattr(e, "nome_fantasia", "") or "",
            "estado": e.estado, "cidade": e.cidade,
            "status_pagamento": getattr(e, "status_pagamento", None),
            "teares": cnt
        })
    return jsonify(data)

@app.route("/admin/impersonar/<int:empresa_id>")
def admin_impersonar(empresa_id):
    if not _seed_ok(): return "Não autorizado", 403
    session["admin_impersonando"] = True
    session["perfil"] = "malharia"
    session["empresa_id"] = empresa_id
    try:
        return redirect(url_for("painel_malharia"))
    except Exception:
        return redirect("/")

@app.route("/admin/desimpersonar")
def admin_desimpersonar():
    session.pop("admin_impersonando", None)
    session.pop("perfil", None)
    session.pop("empresa_id", None)
    return redirect(url_for("index"))

# --------------------------------------------------------------------
# Outras rotas utilitárias/compat
# --------------------------------------------------------------------
@app.route('/busca', methods=['GET', 'POST'])
def buscar_teares():
    qs = request.query_string.decode('utf-8')
    return redirect(f"{url_for('index')}{('?' + qs) if qs else ''}")

@app.route('/planos')
def planos():
    empresa = Empresa.query.get(session['empresa_id']) if 'empresa_id' in session else None
    return render_template('planos.html', empresa=empresa)

@app.route('/pagar', methods=['GET'])
def pagar():
    return redirect(url_for('checkout'))

@app.route('/checkout')
def checkout():
    if 'empresa_id' not in session:
        return redirect(url_for('login'))
    empresa = Empresa.query.get(session['empresa_id'])
    if not empresa:
        session.clear(); return redirect(url_for('login'))

    base = base_url()
    success_url = f"{base}/pagamento_aprovado"
    failure_url = f"{base}/pagamento_erro"
    pending_url = f"{base}/pagamento_pendente"
    notify_url  = f"{base}/webhook"
    ext_ref = f"achetece:{empresa.id}:{uuid.uuid4().hex}"

    plano = (request.args.get('plano') or 'mensal').lower()
    titulo_plano = "Assinatura anual AcheTece" if plano == 'anual' else "Assinatura mensal AcheTece"
    preco = float(PLAN_YEARLY if plano == 'anual' else PLAN_MONTHLY)

    preference_data = {
        "items": [{"title": titulo_plano, "quantity": 1, "currency_id": "BRL", "unit_price": preco}],
        "payer": {"email": empresa.email},
        "back_urls": {"success": success_url, "failure": failure_url, "pending": pending_url},
        "auto_return": "approved",
        "notification_url": notify_url,
        "external_reference": ext_ref,
        "statement_descriptor": "AcheTece"
    }
    try:
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response", {}) if isinstance(preference_response, dict) else {}
        init_point = preference.get("init_point")
        if not init_point:
            return f"<h2>Erro: 'init_point' ausente na resposta.</h2>", 500
        return redirect(init_point)
    except Exception as e:
        app.logger.exception(f"[CHECKOUT] Erro: {e}")
        return f"<h2>Erro ao iniciar pagamento: {e}</h2>", 500

@app.route('/pagamento_aprovado')
def pagamento_aprovado():
    return render_template('pagamento_aprovado.html')

@app.route('/pagamento_sucesso')
def pagamento_sucesso():
    # compat (alguns templates/links antigos usam esta rota)
    return render_template('pagamento_aprovado.html')

@app.route('/pagamento_erro')
def pagamento_erro():
    return render_template('pagamento_erro.html')

@app.route('/pagamento_pendente')
def pagamento_pendente():
    return render_template('pagamento_pendente.html')

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
    """
    Stub de webhook para MP/integrações: registra payload e responde 200.
    Evita 404 do MercadoPago e facilita debug em produção.
    """
    try:
        payload = request.get_json(silent=True) or {}
        app.logger.info(f"[WEBHOOK] {payload}")
    except Exception as e:
        app.logger.warning(f"[WEBHOOK] parse error: {e}")
    return jsonify({"ok": True}), 200

@app.route("/contato", methods=["GET", "POST"])
def contato():
    enviado = False; erro = None
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
                    recipients=[os.getenv("CONTACT_TO", app.config.get("MAIL_USERNAME") or "")],
                    reply_to=email
                )
                msg.body = f"Nome: {nome}\nE-mail: {email}\n\nMensagem:\n{mensagem}"
                mail.send(msg); enviado = True
            except Exception as e:
                erro = f"Falha ao enviar: {e}"
    return render_template("fale_conosco.html", enviado=enviado, erro=erro)

# >>> ALIAS CORRIGIDO: endpoint 'quem_somos' (usado no template) <<<
@app.route("/quem_somos", endpoint="quem_somos")
@app.route("/quem_somos/")
@app.route("/quem-somos")
@app.route("/quem-somos/")
def view_quem_somos():
    return render_template("quem_somos.html")

# compat .html direto
@app.route("/quem_somos.html")
def quem_somos_html():
    return redirect(url_for("quem_somos"), code=301)

@app.route('/rota-teste')
def rota_teste():
    return "✅ A rota funciona!"

# --------------------------------------------------------------------
# Cidades por UF (cache local)
# --------------------------------------------------------------------
_CIDADES_CACHE = {}
_CIDADES_JSON_PATH = Path(app.root_path) / "static" / "cidades_por_uf.json"

def _carregar_cidades_estatico():
    try:
        if _CIDADES_JSON_PATH.exists():
            with open(_CIDADES_JSON_PATH, "r", encoding="utf-8") as f:
                data = json.load(f)
            return {k.upper(): v for k, v in data.items()}
    except Exception as e:
        app.logger.warning(f"Falha ao ler cidades_por_uf.json: {e}")
    return {}

_CIDADES_ESTATICO = _carregar_cidades_estatico()

def _get_cidades_por_uf(uf: str):
    if not uf: return []
    uf = uf.strip().upper()
    cache_path = os.path.join(CACHE_DIR, f'{uf}.json')
    try:
        if os.path.exists(cache_path) and os.path.getsize(cache_path) > 2:
            with open(cache_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                if isinstance(data, list) and data:
                    return data
    except Exception as e:
        app.logger.warning(f'Falha ao ler cache de cidades {uf}: {e}')
    try:
        url = f'https://servicodados.ibge.gov.br/api/v1/localidades/estados/{uf}/municipios'
        r = requests.get(url, timeout=10); r.raise_for_status()
        municipios = r.json()
        cidades = sorted([m.get('nome', '').strip() for m in municipios if m.get('nome')], key=_norm)
        if cidades:
            with open(cache_path, 'w', encoding='utf-8') as f:
                json.dump(cidades, f, ensure_ascii=False)
            return cidades
    except Exception as e:
        app.logger.warning(f'Falha ao baixar cidades do IBGE para UF={uf}: {e}')
    return []

@app.route("/api/cidades")
def api_cidades():
    uf = request.args.get("uf", "")
    return jsonify(_get_cidades_por_uf(uf))

# --------------------------------------------------------------------
# Recuperação de senha
# --------------------------------------------------------------------
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
                app.logger.exception(f"Erro ao enviar e-mail: {e}")
                return render_template('esqueci_senha.html', erro='Erro ao enviar e-mail.')
        return render_template('esqueci_senha.html', erro='E-mail não encontrado.')
    return render_template('esqueci_senha.html')

@app.route('/redefinir_senha/<token>', methods=['GET', 'POST'])
def redefinir_senha(token):
    serializer = URLSafeTimedSerializer(app.config['SECRET_KEY'])
    try:
        email = serializer.loads(token, salt='recupera-senha', max_age=3600)
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

# --------------------------------------------------------------------
# Páginas estáticas simples / compat
# --------------------------------------------------------------------
@app.route('/fale_conosco')
@app.route('/suporte')
def fale_conosco():
    try:
        return render_template("fale_conosco.html")
    except Exception:
        return redirect(url_for("index"))

@app.route("/termos")
def termos():
    return render_template("termos_politicas.html")

@app.route('/malharia_info')
def malharia_info():
    # compat: alguns templates podem linkar para esta página estática
    return render_template('malharia_info.html')

# --------------------------------------------------------------------
# Entry point local (Render usa gunicorn main:app)
# --------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
