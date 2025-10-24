from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    render_template_string, send_file, jsonify, abort, g
)
# Removido o uso de Flask-Mail; usamos Resend + SMTP com timeout
from flask_sqlalchemy import SQLAlchemy
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
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
from sqlalchemy import inspect, text, or_, func, create_engine
from sqlalchemy.exc import OperationalError
from pathlib import Path
import random
from jinja2 import TemplateNotFound
from urllib.parse import urlparse
from werkzeug.utils import secure_filename
import time
import resend  # biblioteca do Resend
from PIL import Image
import shutil

# SMTP direto (fallback)
import smtplib, ssl
from email.message import EmailMessage

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

# ==== Utils de ambiente (DEFINA ANTES DE USAR em app.config.update) ==========
def _env_bool(name: str, default: bool = False) -> bool:
    """
    Lê variáveis de ambiente como booleano.
    Aceita: 1, true, yes, on (case-insensitive). Qualquer outra coisa vira False.
    """
    v = os.getenv(name)
    if v is None:
        return default
    return str(v).strip().lower() in {"1", "true", "t", "yes", "y", "on"}

# --------------------------------------------------------------------
# E-mail — Config + helpers (Resend + SMTP fallback)
# (apenas UM bloco; removidas duplicações)
# --------------------------------------------------------------------
app.config.update(
    SMTP_HOST=os.getenv("SMTP_HOST", "smtp.gmail.com"),
    SMTP_PORT=int(os.getenv("SMTP_PORT", "465")),  # 465 SSL é mais estável na Render
    SMTP_USER=os.getenv("SMTP_USER", ""),
    SMTP_PASS=os.getenv("SMTP_PASS", ""),
    SMTP_FROM=os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")),
    MAIL_TIMEOUT=int(os.getenv("MAIL_TIMEOUT", "8")),             # segundos
    MAIL_SUPPRESS_SEND=_env_bool("MAIL_SUPPRESS_SEND", False),    # True = NÃO envia (modo teste)
    OTP_DEV_FALLBACK=_env_bool("OTP_DEV_FALLBACK", False),        # True = loga e deixa seguir
)

RESEND_API_KEY = os.getenv("RESEND_API_KEY") or ""
RESEND_DOMAIN  = os.getenv("RESEND_DOMAIN", "achetece.com.br")
EMAIL_FROM     = os.getenv("EMAIL_FROM", f"AcheTece <no-reply@{RESEND_DOMAIN}>")
REPLY_TO       = os.getenv("REPLY_TO", "")

if RESEND_API_KEY:
    resend.api_key = RESEND_API_KEY
else:
    logging.warning("[EMAIL] RESEND_API_KEY não configurada — envio via Resend desativado.")

def _extract_email(addr: str) -> str:
    m = re.search(r"<([^>]+)>", addr or "")
    s = m.group(1) if m else (addr or "")
    return s.strip()

def _domain_of(addr: str) -> str:
    e = _extract_email(addr)
    return e.split("@")[-1].lower() if "@" in e else ""

def _send_via_resend(to: str, subject: str, html: str, text: str | None = None) -> tuple[bool, str]:
    if not RESEND_API_KEY:
        return False, "RESEND_API_KEY ausente"
    try:
        # força FROM no domínio verificado
        from_domain = _domain_of(EMAIL_FROM)
        safe_from = EMAIL_FROM if (RESEND_DOMAIN and from_domain == RESEND_DOMAIN) \
            else f"AcheTece <no-reply@{RESEND_DOMAIN}>"

        payload = {"from": safe_from, "to": [to], "subject": subject, "html": html}
        if text:
            payload["text"] = text
        if REPLY_TO:
            payload["reply_to"] = REPLY_TO

        resp = resend.Emails.send(payload)
        logging.info(f"[EMAIL/RESEND] Enviado para {to}. resp={resp}")
        return True, "ok"
    except Exception as e:
        logging.exception(f"[EMAIL/RESEND] Falha ao enviar para {to}: {e}")
        return False, f"resend_error: {e}"

def _send_via_smtp(to: str, subject: str, html: str, text: str | None = None) -> tuple[bool, str]:
    """Envio direto via SMTP (SSL/TLS) — fallback."""
    host = app.config.get("SMTP_HOST") or "smtp.gmail.com"
    port = int(app.config.get("SMTP_PORT") or 465)
    user = app.config.get("SMTP_USER") or ""
    pwd  = app.config.get("SMTP_PASS") or ""
    sender = app.config.get("SMTP_FROM") or user
    timeout = int(app.config.get("MAIL_TIMEOUT") or 8)

    if not (user and pwd and sender and to):
        return False, "SMTP não configurado."

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = sender
    msg["To"] = to
    if REPLY_TO:
        msg["Reply-To"] = REPLY_TO
    msg.set_content(text or "Veja este e-mail em HTML.")
    msg.add_alternative(html, subtype="html")

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port, timeout=timeout) as s:
                s.login(user, pwd)
                s.send_message(msg)
        else:
            ctx = ssl.create_default_context()
            with smtplib.SMTP(host, port, timeout=timeout) as s:
                s.ehlo()
                s.starttls(context=ctx)
                s.login(user, pwd)
                s.send_message(msg)
        return True, "ok"
    except Exception as e:
        app.logger.exception(f"[EMAIL/SMTP] Falha ao enviar para {to}: {e}")
        return False, f"smtp_error: {e}"

def _smtp_send_direct(to: str, subject: str, html: str, text: str | None = None) -> tuple[bool, str]:
    """
    Envia e-mail preferencialmente via Resend e faz fallback para SMTP.
    Retorna (ok, mensagem).
    """
    if app.config.get("MAIL_SUPPRESS_SEND"):
        app.logger.info(f"[EMAIL SUPPRESSED] to={to} subj={subject}")
        return True, "suppress"

    ok, msg = _send_via_resend(to, subject, html, text)
    if ok:
        return True, "resend_ok"

    ok2, msg2 = _send_via_smtp(to, subject, html, text)
    if ok2:
        return True, "smtp_ok"

    return False, f"{msg} | {msg2}"

def send_email(to: str, subject: str, html: str, text: str | None = None) -> bool:
    ok, _ = _smtp_send_direct(to=to, subject=subject, html=html, text=text)
    return ok

# Mercado Pago (mantido para compat)
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN") or os.getenv("MERCADO_PAGO_TOKEN", "")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
PLAN_MONTHLY = float(os.getenv("PLAN_MONTHLY", "2.00"))
PLAN_YEARLY  = float(os.getenv("PLAN_YEARLY", "2.00"))

# DEMO
DEMO_MODE  = os.getenv("DEMO_MODE", "true").lower() == "true"
DEMO_TOKEN = os.getenv("DEMO_TOKEN", "localdemo")
SEED_TOKEN = os.getenv("SEED_TOKEN", "ACHETECE")

# ===== CONFIG AVATAR (definir uma única vez; sem duplicar BASE_DIR) =====
app.config['MAX_CONTENT_LENGTH'] = 5 * 1024 * 1024  # 5 MB

# Pastas (usar app.root_path para padronizar)
UPLOAD_DIR  = os.path.join(app.root_path, "static", "uploads", "perfil")   # legado (emp_{id}.ext)
AVATAR_DIR  = os.path.join(app.root_path, "static", "uploads", "avatars")  # novo fluxo (uid_timestamp.webp)
os.makedirs(UPLOAD_DIR, exist_ok=True)
os.makedirs(AVATAR_DIR, exist_ok=True)

ALLOWED_EXTENSIONS = {'png', 'jpg', 'jpeg', 'webp', 'gif'}

def _allowed_file(filename: str) -> bool:
    return ('.' in filename) and (filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS)

def _save_square_webp(file_storage, dest_path: str, side: int = 400, quality: int = 85):
    """Recorta para quadrado central, redimensiona e salva em WEBP."""
    img = Image.open(file_storage.stream)
    # converte p/ RGB (remove alpha) antes do WEBP
    if img.mode not in ('RGB', 'L'):
        img = img.convert('RGB')

    # Compat de filtro (Pillow 10+ usa Image.Resampling)
    try:
        _LANCZOS = Image.Resampling.LANCZOS  # type: ignore[attr-defined]
    except Exception:
        _LANCZOS = Image.LANCZOS

    w, h = img.size
    m = min(w, h)
    left = (w - m) // 2
    top = (h - m) // 2
    img = img.crop((left, top, left + m, top + m)).resize((side, side), _LANCZOS)
    img.save(dest_path, 'WEBP', quality=quality, method=6)

# --------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------
def _set_if_has(obj, names, value):
    """Seta no primeiro atributo existente da lista `names`."""
    for n in names:
        if hasattr(obj, n):
            setattr(obj, n, value)
            return True
    return False

def _only_digits(s):
    return re.sub(r"\D", "", s or "")

def _fmt_cep(s):
    d = _only_digits(s)
    if len(d) == 8:
        return f"{d[:5]}-{d[5:]}"
    return (s or "").strip() or None

def _norm(s: str) -> str:
    return normalize('NFKD', s).encode('ASCII', 'ignore').decode('ASCII').strip().lower()

def gerar_token(email):
    return URLSafeTimedSerializer(app.config['SECRET_KEY']).dumps(email, salt='recupera-senha')

def enviar_email_recuperacao(email, nome_empresa=""):
    token = gerar_token(email)
    link = url_for('redefinir_senha', token=token, _external=True)
    html = render_template_string("""
<!doctype html>
<html lang="pt-br">
  <body style="margin:0;padding:0;background:#F7F7FA;font-family:-apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif;color:#1e1b2b;">
    <table role="presentation" width="100%" cellspacing="0" cellpadding="0" style="background:#F7F7FA;padding:24px 0;">
      <tr><td align="center">
        <table role="presentation" width="600" cellspacing="0" cellpadding="0" style="max-width:600px;width:100%;background:#fff;border:1px solid #eee;border-radius:12px;">
          <tr><td style="padding:22px 24px;border-bottom:1px solid #f0f0f0;">
            <h2 style="margin:0;font-size:20px;line-height:1.25;font-weight:800;">Redefinição de Senha</h2>
          </td></tr>
          <tr><td style="padding:22px 24px;">
            <p style="margin:0 0 10px 0;line-height:1.55;">Olá <strong>{{ nome }}</strong>,</p>
            <p style="margin:0 0 16px 0;line-height:1.55;">
              Clique no botão abaixo para criar uma nova senha. Este link é válido por <strong>1 hora</strong>.
            </p>
            <table role="presentation" cellspacing="0" cellpadding="0" style="margin:18px 0 10px 0;">
              <tr><td align="center" bgcolor="#8A00FF" style="border-radius:9999px;">
                <a href="{{ link }}" target="_blank"
                   style="display:inline-block;padding:12px 24px;border-radius:9999px;background:#8A00FF;color:#fff;text-decoration:none;font-weight:800;font-size:16px;line-height:1;">
                  Redefinir senha
                </a>
              </td></tr>
            </table>
            <p style="margin:14px 0 0 0;font-size:13px;color:#6b6b6b;line-height:1.5;">
              Se o botão não funcionar, copie e cole este link no navegador:<br>
              <a href="{{ link }}" target="_blank" style="color:#5b2fff;word-break:break-all;">{{ link }}</a>
            </p>
          </td></tr>
          <tr><td style="padding:16px 24px;border-top:1px solid #f0f0f0;color:#6b6b6b;font-size:12px;">
            Você recebeu este e-mail porque solicitou redefinição de senha no AcheTece.
            Se não foi você, ignore esta mensagem.
          </td></tr>
        </table>
      </td></tr>
    </table>
  </body>
</html>
    """, nome=(nome_empresa or email), link=link)

    ok, _ = _smtp_send_direct(
        to=email,
        subject="Redefinição de Senha - AcheTece",
        html=html,
        text=f"Para redefinir sua senha (válido por 1h), acesse: {link}",
    )
    if not ok:
        raise RuntimeError("Falha ao enviar e-mail de recuperação.")

def login_admin_requerido(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if session.get('admin_email') != 'gestao.achetece@gmail.com':
            flash('Acesso não autorizado.')
            return redirect(url_for('admin_login'))
        return f(*args, **kwargs)
    return decorated_function

# --------------------------------------------------------------------
# DB bootstrap (escolha da URL e engine)
# --------------------------------------------------------------------
ALLOW_SQLITE_FALLBACK = os.getenv("ALLOW_SQLITE_FALLBACK", "0") == "1"

def _normalize_db_url(url: str) -> str:
    if not url:
        return url
    url = url.strip()
    if url.startswith("postgres://"):
        return url.replace("postgres://", "postgresql+psycopg://", 1)
    if url.startswith("postgresql://"):
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    return url

def _try_ping(url: str) -> bool:
    try:
        engine = create_engine(
            url,
            pool_pre_ping=True,
            pool_recycle=280,
            connect_args=({"connect_timeout": 5} if url.startswith("postgresql+psycopg://") else {}),
        )
        with engine.connect() as c:
            c.exec_driver_sql("SELECT 1")
        return True
    except Exception as e:
        app.logger.warning("[DB] ping falhou para %s: %r", url, e)
        return False

def _pick_database_uri() -> str:
    internal = os.getenv("INTERNAL_DATABASE_URL") or os.getenv("DATABASE_URL_INTERNAL") or ""
    primary  = os.getenv("SQLALCHEMY_DATABASE_URI") or os.getenv("DATABASE_URL") or ""
    raw_url  = (internal or primary).strip()
    url = _normalize_db_url(raw_url)

    if url.startswith("postgresql+psycopg://"):
        if _try_ping(url):
            return url
        if ALLOW_SQLITE_FALLBACK:
            app.logger.error("[DB] Postgres indisponível. CAINDO para SQLite (ALLOW_SQLITE_FALLBACK=1).")
            return "sqlite:///achetece.db"
        app.logger.error("[DB] Postgres indisponível e fallback desativado; retornarei 503 até estabilizar.")
        return url
    return url or "sqlite:///achetece.db"

FINAL_DB_URI = _pick_database_uri()
engine_opts = {"pool_pre_ping": True, "pool_recycle": 280, "pool_timeout": 30}
if FINAL_DB_URI.startswith("postgresql+psycopg://"):
    engine_opts["connect_args"] = {"connect_timeout": 5}

db = SQLAlchemy()
app.config['SQLALCHEMY_DATABASE_URI'] = FINAL_DB_URI
app.config['SQLALCHEMY_TRACK_MODIFICATIONS'] = False
app.config['SQLALCHEMY_ENGINE_OPTIONS'] = engine_opts
db.init_app(app)

# --- DB status + offline page (checa DB por request) -------------------------
_DB_READY = None
_DB_LAST_CHECK = 0

def _db_is_up(refresh_every=10):
    """Cacheia o resultado por ~10s para não martelar o banco a cada request."""
    global _DB_READY, _DB_LAST_CHECK
    now = time.time()
    if _DB_READY is None or (now - _DB_LAST_CHECK) > refresh_every:
        _DB_LAST_CHECK = now
        try:
            with db.engine.connect() as c:
                c.exec_driver_sql("SELECT 1")
            _DB_READY = True
        except Exception:
            _DB_READY = False
    return _DB_READY

def _render_offline(status: int | None = None):
    """
    Página offline: devolve 200 na home/rotas públicas e 503 no restante.
    Assim o Render não marca erro e o usuário vê uma página amigável.
    """
    public_ok200 = {"/", "/quem_somos", "/quem-somos", "/fale_conosco", "/suporte", "/termos"}
    if status is None:
        status = 200 if request.path in public_ok200 else 503

    try:
        resp = render_template("offline.html")
    except TemplateNotFound:
        resp = """
<!doctype html><meta charset="utf-8">
<title>AcheTece – temporariamente offline</title>
<style>body{font-family:system-ui,-apple-system,Segoe UI,Roboto,Helvetica,Arial;max-width:720px;margin:8vh auto;padding:0 16px;color:#1e1b2b}
.card{border:1px solid #eee;border-radius:12px;padding:20px}
h1{font-size:24px;margin:0 0 8px}p{line-height:1.55;margin:10px 0}small{color:#888}</style>
<div class="card">
  <h1>Estamos temporariamente offline</h1>
  <p>Nosso banco de dados está indisponível no momento. Enquanto isso, você ainda pode navegar nas páginas públicas.</p>
  <p><small>Este estado é automático e sai assim que o banco voltar a responder.</small></p>
</div>
"""
    headers = {}
    if status == 503:
        headers["Retry-After"] = "10"
    return resp, status, headers

@app.before_request
def _mark_db_status():
    g.db_up = _db_is_up()

@app.before_request
def _offline_guard():
    """Serve página offline amigável quando o DB está fora do ar."""
    if getattr(g, "db_up", True):
        return
    p = request.path or "/"
    if p.startswith("/static/") or p in {"/favicon.ico", "/robots.txt", "/sitemap.xml"}:
        return
    return _render_offline()

# =====================[ ANALYTICS - INÍCIO ]=====================
ALLOWED_EVENTS = {
    'CARD_IMPRESSION',
    'COMPANY_PROFILE_VIEW',
    'CONTACT_CLICK_WHATSAPP',
    'TEAR_DETAIL_VIEW',
}

def track_event(event: str, company_id: int, tear_id: int | None = None, meta: dict | None = None):
    if event not in ALLOWED_EVENTS:
        return
    try:
        with db.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO analytics_events (company_id, tear_id, event, session_id, meta)
                    VALUES (:cid, :tid, :evt, :sid, :meta)
                """),
                {
                    "cid": company_id,
                    "tid": tear_id,
                    "evt": event,
                    "sid": session.get("_sid") or request.cookies.get("session") or "",
                    "meta": json.dumps(meta or {}),
                },
            )
    except Exception:
        app.logger.exception("[analytics] falha ao registrar evento")

def _init_analytics_table():
    dialect = db.engine.url.get_backend_name()
    if dialect == "sqlite":
        pk = "INTEGER PRIMARY KEY AUTOINCREMENT"
        ts_default = "CURRENT_TIMESTAMP"
    else:
        pk = "BIGSERIAL PRIMARY KEY"
        ts_default = "CURRENT_TIMESTAMP"

    ddl = f"""
        CREATE TABLE IF NOT EXISTS analytics_events (
            id {pk},
            ts TIMESTAMP NOT NULL DEFAULT {ts_default},
            company_id INTEGER NOT NULL,
            tear_id INTEGER,
            event TEXT NOT NULL,
            session_id TEXT,
            meta TEXT
        )
    """
    idx1 = "CREATE INDEX IF NOT EXISTS idx_ae_company_ts ON analytics_events(company_id, ts)"
    idx2 = "CREATE INDEX IF NOT EXISTS idx_ae_event_ts   ON analytics_events(event, ts)"

    with db.engine.begin() as conn:
        conn.execute(text(ddl))
        conn.execute(text(idx1))
        conn.execute(text(idx2))

def get_performance(company_id, dt_ini=None, dt_fim=None):
    params = {"cid": company_id}
    where = ["company_id = :cid"]
    if dt_ini:
        where.append("ts >= :dt_ini"); params["dt_ini"] = dt_ini
    if dt_fim:
        where.append("ts < :dt_fim");  params["dt_fim"]  = dt_fim

    sql = f"""
      SELECT DATE(ts) AS d,
             SUM(CASE WHEN event IN ('CARD_IMPRESSION','COMPANY_PROFILE_VIEW','TEAR_DETAIL_VIEW') THEN 1 ELSE 0 END) AS visitas,
             SUM(CASE WHEN event IN ('CONTACT_CLICK_WHATSAPP') THEN 1 ELSE 0 END) AS contatos
        FROM analytics_events
       WHERE {" AND ".join(where)}
       GROUP BY DATE(ts)
       ORDER BY DATE(ts)
    """
    with db.engine.begin() as conn:
        rows = conn.execute(text(sql), params).mappings().all()

    series = [{"data": r["d"], "visitas": r["visitas"], "contatos": r["contatos"]} for r in rows]
    total_visitas  = sum(r["visitas"]  for r in rows)
    total_contatos = sum(r["contatos"] for r in rows)
    return total_visitas, total_contatos, series

# Executa migrações/ajustes e a criação do analytics apenas quando o DB responder
_BOOTSTRAP_DONE   = False
_ANALYTICS_READY  = False

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
    # >>> Novos campos para o endereço completo da empresa <<<
    endereco = db.Column(db.String(240))   # Rua, número, complemento, bairro
    cep      = db.Column(db.String(9))     # 00000-000

class Tear(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    marca = db.Column(db.String(100), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)            # MONO | DUPLA | ...
    finura = db.Column(db.Integer, nullable=False)             # galga
    diametro = db.Column(db.Integer, nullable=False)
    alimentadores = db.Column(db.Integer, nullable=False)
    elastano = db.Column(db.String(10), nullable=False)        # Sim | Não
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

class OtpToken(db.Model):
    __tablename__ = "otp_token"
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), index=True, nullable=False)
    code_hash = db.Column(db.String(255), nullable=False)
    created_at = db.Column(db.DateTime, default=datetime.utcnow, index=True)
    expires_at = db.Column(db.DateTime, index=True)
    used_at = db.Column(db.DateTime, nullable=True)
    attempts = db.Column(db.Integer, default=0)
    last_sent_at = db.Column(db.DateTime, nullable=True)
    ip = db.Column(db.String(64))
    user_agent = db.Column(db.String(255))

# === Helpers de autenticação/empresa =========================================
# flask_login é opcional no projeto; faça import seguro
try:
    from flask_login import current_user  # type: ignore
except Exception:  # noqa: E722
    current_user = None  # fallback silencioso

def _whoami():
    """
    Retorna (user_id, email) do usuário autenticado.
    - Usa flask_login se disponível.
    - Faz fallback para a sessão própria do app.
    """
    uid = None
    email = None
    # flask_login (se disponível)
    try:
        if current_user and getattr(current_user, "is_authenticated", False):
            uid = getattr(current_user, "id", None)
            email = getattr(current_user, "email", None)
    except Exception:
        pass
    # fallback para sessão própria
    if not uid:
        uid = session.get("user_id") or session.get("auth_user_id")
    if not email:
        email = session.get("auth_email") or session.get("login_email")
    return uid, email

def _get_empresa_usuario_da_sessao():
    """
    Caminho feliz:
      1) Usa session['empresa_id'] se existir.
      2) Senão, tenta por user_id (flask_login/sessão) e depois por e-mail.
    Garante:
      - Empresa.usuario (cria/relaciona Usuario se necessário).
      - Empresa.user_id preenchido.
      - session['empresa_id'] e session['empresa_apelido'] atualizados.
    Retorna:
      (empresa, usuario) ou (None, None).
    NÃO redireciona.
    """
    # 1) Por empresa_id na sessão
    emp_id = session.get("empresa_id")
    if emp_id:
        emp = Empresa.query.get(emp_id)
        if emp:
            # Resolve usuário relacionado
            u = emp.usuario or Usuario.query.filter_by(email=emp.email).first()
            if not u:
                # cria Usuario "espelho" da Empresa (compat com legado)
                u = Usuario(email=emp.email, senha_hash=emp.senha, role=None, is_active=True)
                db.session.add(u)
                db.session.flush()
                emp.user_id = u.id
                db.session.commit()
            elif not emp.user_id:
                emp.user_id = u.id
                db.session.commit()
            session["empresa_apelido"] = emp.apelido or emp.nome or (emp.email.split("@")[0] if emp.email else "")
            return emp, u
        else:
            # limpa sessão inválida
            session.pop("empresa_id", None)
            session.pop("empresa_apelido", None)

    # 2) Fallback: por identidade do usuário
    uid, email = _whoami()

    if uid:
        emp = Empresa.query.filter_by(user_id=uid).first()
        if emp:
            session["empresa_id"] = emp.id
            session["empresa_apelido"] = emp.apelido or emp.nome or (emp.email.split("@")[0] if emp.email else "")
            u = emp.usuario or Usuario.query.filter_by(email=emp.email).first()
            return emp, u

    if email:
        emp = Empresa.query.filter(func.lower(Empresa.email) == email.lower()).first()
        if emp:
            session["empresa_id"] = emp.id
            session["empresa_apelido"] = emp.apelido or emp.nome or (emp.email.split("@")[0] if emp.email else "")
            u = emp.usuario or Usuario.query.filter_by(email=emp.email).first()
            # se não houver vínculo user_id e já temos um Usuario, vincule
            if u and not emp.user_id:
                emp.user_id = u.id
                db.session.commit()
            return emp, u

    return None, None

def _pegar_empresa_do_usuario(required=True):
    """
    Retrocompat:
      - Usa _get_empresa_usuario_da_sessao() e retorna **apenas Empresa**.
      - Se required=True e não houver empresa, redireciona para login (mantém contrato antigo).
    """
    emp, _u = _get_empresa_usuario_da_sessao()
    if emp:
        return emp
    if required:
        flash("Faça login para continuar.", "warning")
        return redirect(url_for("login"))
    return None

def assinatura_ativa_requerida(f):
    """
    Decorator que exige empresa em sessão e assinatura ativa (ou DEMO).
    Mantém a mesma lógica que você já vinha usando.
    """
    @wraps(f)
    def wrapper(*args, **kwargs):
        emp, _u = _get_empresa_usuario_da_sessao()
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

# Alias útil para qualquer código legado que espere esse nome
_get_empresa_usuario = _get_empresa_usuario_da_sessao

# --------------------------------------------------------------------
# Migrações leves / Setup inicial (idempotente)
# --------------------------------------------------------------------
def _ensure_auth_layer_and_link():
    # 1) tabela de usuário
    try:
        Usuario.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as e:
        app.logger.warning(f"create usuario table: {e}")

    # 2) garantir coluna user_id em empresa (se ainda não existir)
    try:
        insp = inspect(db.engine)
        cols = {c['name'] for c in insp.get_columns('empresa')}
        if 'user_id' not in cols:
            with db.engine.begin() as conn:
                conn.exec_driver_sql('ALTER TABLE empresa ADD COLUMN user_id INTEGER')
    except Exception as e:
        app.logger.warning(f"add user_id to empresa failed: {e}")

    # 3) backfill SEM carregar o modelo inteiro (evita depender de colunas novas)
    try:
        rows = db.session.execute(
            text("SELECT id, email, user_id FROM empresa")
        ).mappings().all()

        for r in rows:
            if r.get('user_id'):
                continue
            email = (r.get('email') or '').strip()
            if not email:
                continue
            u = Usuario.query.filter_by(email=email).first()
            if not u:
                u = Usuario(email=email, senha_hash=None, role=None, is_active=True)
                db.session.add(u)
                db.session.flush()  # garante u.id

            db.session.execute(
                text("UPDATE empresa SET user_id = :uid WHERE id = :id AND (user_id IS NULL)"),
                {"uid": u.id, "id": r['id']}
            )
        db.session.commit()
    except Exception as e:
        app.logger.warning(f"backfill usuarios from empresas failed: {e}")
        db.session.rollback()

def _ensure_cliente_profile_table():
    try:
        ClienteProfile.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as e:
        app.logger.warning(f"create cliente_profile table: {e}")

def _ensure_empresa_address_columns():
    """
    Garante colunas endereco (varchar 240) e cep (varchar 9) em empresa.
    Idempotente e compatível com SQLite/Postgres. Roda DDL fora da sessão ORM.
    """
    try:
        insp = inspect(db.engine)
        cols = {c['name'] for c in insp.get_columns('empresa')}
        to_add = []
        if 'endereco' not in cols:
            to_add.append("ALTER TABLE empresa ADD COLUMN endereco VARCHAR(240)")
        if 'cep' not in cols:
            to_add.append("ALTER TABLE empresa ADD COLUMN cep VARCHAR(9)")

        if to_add:
            # executa DDL em transação própria (independente da db.session)
            with db.engine.begin() as conn:
                for ddl in to_add:
                    conn.exec_driver_sql(ddl)
    except Exception as e:
        app.logger.warning(f"[BOOT] ensure endereco/cep failed: {e}")

def _run_bootstrap_once():
    """Cria tabelas/migrações leves quando o DB está UP; caso contrário, adia."""
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        return
    if not _db_is_up():
        app.logger.error("[BOOT] adiado: DB indisponível")
        return

    # sempre começe com uma sessão limpa
    try:
        db.session.rollback()
    except Exception:
        pass

    try:
        # 1) cria tabelas base
        db.create_all()

        # 2) GARANTE colunas novas (antes de qualquer SELECT em empresa)
        _ensure_empresa_address_columns()

        # 3) auth + vinculação user_id (usa SELECT minimalista)
        _ensure_auth_layer_and_link()

        # 4) tabela de perfil de cliente
        _ensure_cliente_profile_table()

        _BOOTSTRAP_DONE = True
        app.logger.info("[BOOT] Migrações/ajustes executados.")
    except Exception as e:
        db.session.rollback()
        app.logger.error("[BOOT] adiado: %s", e)

@app.before_request
def _bootstrap_and_analytics_lazy():
    global _ANALYTICS_READY
    if getattr(g, "db_up", False):
        _run_bootstrap_once()
        if not _ANALYTICS_READY:
            try:
                _init_analytics_table()
                _ANALYTICS_READY = True
            except Exception as e:
                app.logger.error("Falha ao garantir tabela de analytics (adiado): %s", e)

# =====================[ ANALYTICS - FIM ]=====================

def parse_bool(val):
    """Normaliza valores vindos de checkbox/select para True/False."""
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in {'1','true','t','on','sim','s','yes','y'}

def _foto_url_runtime(emp_id: int) -> str | None:
    """
    Resolve a URL da foto de perfil para uso imediato no template.
    Prioriza:
      1) session['avatar_url'] (novo fluxo /perfil/foto)
      2) arquivo legado: static/uploads/perfil/emp_{emp_id}.ext
    """
    # 1) Novo fluxo: URL salva na sessão pelo POST /perfil/foto
    sess_url = session.get('avatar_url')
    if sess_url:
        # opcional: cache-buster simples, caso queira
        return sess_url

    # 2) Legado: procurar por emp_{id}.ext na pasta de perfil
    base = os.path.join(app.root_path, "static", "uploads", "perfil")
    for ext in ("png", "jpg", "jpeg", "webp", "gif"):
        fn = f"emp_{emp_id}.{ext}"
        p = os.path.join(base, fn)
        if os.path.exists(p):
            v = int(os.path.getmtime(p))  # cache-buster
            return url_for("static", filename=f"uploads/perfil/{fn}") + f"?v={v}"
    return None

@app.context_processor
def inject_avatar_url():
    """
    Disponibiliza `avatar_url` em TODOS os templates.
    Prioridade:
      1) session['avatar_url'] (atualizado no POST /perfil/foto com cache-buster)
      2) campos da empresa (foto_url / logo_url), se existirem
      3) arquivo estável legado emp_{empresa_id}.ext via _foto_url_runtime
    Adiciona cache-buster quando for arquivo local em /static e não houver ?v=...
    """
    url = None

    # 1) Sessão (após upload nós já salvamos aqui com ?v=timestamp)
    sess_url = session.get('avatar_url')
    if sess_url:
        url = sess_url
    else:
        # 2) Empresa do usuário, se disponível
        try:
            emp, _u = _get_empresa_usuario_da_sessao()
        except Exception:
            emp = None

        if emp:
            # tenta campos (se existirem no modelo)
            for attr in ('foto_url', 'logo_url'):
                try:
                    val = getattr(emp, attr)
                except Exception:
                    val = None
                if val:
                    url = val
                    break

            # 3) Fallback: arquivo legado emp_{id}.*
            if not url:
                try:
                    url = _foto_url_runtime(emp.id)
                except Exception:
                    url = None

    # Cache-buster para arquivos locais quando a URL não tiver querystring
    # (evita cache antigo se estiver lendo do DB/legado)
    if url and url.startswith('/static/') and ('?' not in url):
        try:
            fs_path = os.path.join(app.root_path, url.lstrip('/'))
            v = int(os.path.getmtime(fs_path))
            url = f"{url}?v={v}"
        except Exception:
            pass

    return dict(avatar_url=url)

# --------------------------------------------------------------------
# Fallback de templates (evita 500 se faltar HTML)
# --------------------------------------------------------------------
def _render_or_fallback(name: str, **ctx):
    try:
        return render_template(name, **ctx)
    except TemplateNotFound:
        email = ctx.get("email", "")
        if name == "login_method.html":
            return render_template_string("""
            <div style="max-width:520px;margin:32px auto;font-family:system-ui,Arial">
              <h2>Entrar</h2>
              <p>E-mail: <strong>{{ email }}</strong></p>
              <form method="post" action="{{ url_for('post_login_code') }}" style="margin:16px 0">
                <input type="hidden" name="email" value="{{ email }}">
                <button type="submit">Receber código por e-mail</button>
              </form>
              <form method="get" action="{{ url_for('view_login_password') }}">
                <input type="hidden" name="email" value="{{ email }}">
                <button type="submit">Entrar com senha</button>
              </form>
              <p style="color:#888;margin-top:18px">Tela fallback simples.</p>
            </div>
            """, email=email)

        if name == "login_code.html":
            return render_template_string("""
            <div style="max-width:520px;margin:32px auto;font-family:system-ui,Arial">
              <h2>Digite o código enviado por e-mail</h2>
              <p>E-mail: <strong>{{ email }}</strong></p>
              <form method="post" action="{{ url_for('validate_login_code') }}" style="margin:16px 0">
                <input type="hidden" name="email" value="{{ email }}">
                <div style="display:flex;gap:8px;margin:12px 0">
                  {% for i in range(1,7) %}
                    <input name="d{{i}}" maxlength="1" inputmode="numeric" pattern="[0-9]*"
                           style="width:40px;height:48px;text-align:center;font-size:22px">
                  {% endfor %}
                </div>
                <button type="submit">Validar código</button>
              </form>
              <a href="{{ url_for('resend_login_code', email=email) }}">Reenviar código</a>
              <p style="color:#888;margin-top:18px">Tela fallback simples.</p>
            </div>
            """, email=email)

        if name == "login_password.html":
            return render_template_string("""
            <div style="max-width:520px;margin:32px auto;font-family:system-ui,Arial">
              <h2>Entrar com senha</h2>
              <p>E-mail: <strong>{{ email }}</strong></p>
              <form method="post" action="{{ url_for('post_login_password') }}" style="margin:16px 0">
                <input type="hidden" name="email" value="{{ email }}">
                <input type="password" name="senha" placeholder="Sua senha" required style="width:100%;height:44px">
                <button type="submit" style="margin-top:12px">Entrar</button>
              </form>
              <p style="color:#888;margin-top:18px">Tela fallback simples.</p>
            </div>
            """, email=email)

        return render_template_string("<h2>Página</h2><p>Template '{{name}}' não encontrado.</p>", name=name, **ctx)

def _render_try(candidatos: list[str], **ctx):
    """Tenta renderizar o primeiro template existente na lista.
       Se nenhum existir, cai num HTML mínimo para não 500."""
    for nome in candidatos:
        try:
            return render_template(nome, **ctx)
        except TemplateNotFound:
            continue
    return render_template_string("<h2>Página temporária</h2><p>Conteúdo indisponível.</p>")

def _get_notificacoes(empresa_id):
    # Troque por consulta real quando tiver o banco
    items = []  # ex.: [{"titulo":"Novo contato","mensagem":"João enviou msg"}]
    return len(items), items

# --------------------------------------------------------------------
# INDEX
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

@app.post("/api/track")
def api_track():
    data = request.get_json(silent=True) or {}
    event      = data.get("event")
    company_id = data.get("company_id")
    tear_id    = data.get("tear_id")
    session_id = data.get("session_id") or (session.get("_sid") or request.cookies.get("session") or "")
    meta       = data.get("meta") or {}

    if event not in ALLOWED_EVENTS or not company_id:
        return jsonify({"ok": False, "error": "bad event/company"}), 400

    try:
        with db.engine.begin() as conn:
            conn.execute(
                text("""
                    INSERT INTO analytics_events (company_id, tear_id, event, session_id, meta)
                    VALUES (:cid, :tid, :evt, :sid, :meta)
                """),
                {
                    "cid": int(company_id),
                    "tid": int(tear_id) if tear_id else None,
                    "evt": event,
                    "sid": session_id,
                    "meta": json.dumps(meta),
                },
            )
        return jsonify({"ok": True})
    except Exception as e:
        app.logger.exception("[analytics] falha ao registrar evento: %s", e)
        return jsonify({"ok": False}), 500

# ================================================================

@app.route("/", methods=["GET"])
def index():
    # Se o DB estiver indisponível (marcado no before_request), serve a página offline
    if not getattr(g, "db_up", True):
        return _render_offline()

    try:
        v = request.args
        filtros = {
            "tipo":     (v.get("tipo") or "").strip(),
            "diâmetro": (v.get("diâmetro") or v.get("diametro") or "").strip(),
            "galga":    (v.get("galga") or "").strip(),
            "estado":   (v.get("estado") or "").strip(),
            "cidade":   (v.get("cidade") or "").strip(),
        }

        q_base = Tear.query.outerjoin(Empresa)
        # Se a coluna 'ativo' não existir, ignora silenciosamente
        try:
            q_base = q_base.filter(Tear.ativo.is_(True))
        except Exception:
            pass

        opcoes = {"tipo": [], "diâmetro": [], "galga": [], "estado": [], "cidade": []}
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
        opcoes["cidade"] = sorted(cidades_por_uf.get(filtros["estado"], set())) if filtros["estado"] else []

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

        pagina = max(1, int(request.args.get("pagina", 1) or 1))
        por_pagina = int(request.args.get("pp", 20) or 20)
        por_pagina = max(1, min(100, por_pagina))

        total = q.count()
        q = q.order_by(Tear.id.desc())
        teares_page = q.offset((pagina - 1) * por_pagina).limit(por_pagina).all()
        total_paginas = max(1, (total + por_pagina - 1) // por_pagina)

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

            # pega o valor como estiver no banco; se houver legado em 'kit_elastano', usa como fallback
            raw_elastano = getattr(tear, "elastano", None)
            if raw_elastano is None:
                raw_elastano = getattr(tear, "kit_elastano", None)

            item = {
                "empresa_id": (getattr(emp, "id", None) if emp else None),  # 👈 ID da malharia
                "empresa": apelido,
                "tipo": tear.tipo or "—",
                "galga": tear.finura if tear.finura is not None else "—",
                "diametro": tear.diametro if tear.diametro is not None else "—",
                "alimentadores": getattr(tear, "alimentadores", None) if getattr(tear, "alimentadores", None) is not None else "—",
                "elastano": raw_elastano,          # 👈 agora vai para o template
                "kit_elastano": raw_elastano,      # 👈 alias para compatibilidade
                "uf": (emp.estado if emp and getattr(emp, "estado", None) else "—"),
                "cidade": (emp.cidade if emp and getattr(emp, "cidade", None) else "—"),
                "contato": contato_link,

                # Aliases para CSV antigo (opcional manter)
                "Empresa": apelido,
                "Tipo": tear.tipo or "—",
                "Galga": tear.finura if tear.finura is not None else "—",
                "Diâmetro": tear.diametro if tear.diametro is not None else "—",
                "Alimentadores": getattr(tear, "alimentadores", None) if getattr(tear, "alimentadores", None) is not None else "—",
                "Elastano": raw_elastano,          # 👈 alias CSV
                "UF": (emp.estado if emp and getattr(emp, "estado", None) else "—"),
                "Cidade": (emp.cidade if emp and getattr(emp, "cidade", None) else "—"),
                "Contato": contato_link,
            }
            resultados.append(item)

        app.logger.info({
            "rota": "index",
            "total_encontrado": total,
            "pagina": pagina,
            "pp": por_pagina,
            "filtros": filtros
        })

        return render_template(
            "index.html",
            opcoes=opcoes,
            filtros=filtros,
            resultados=resultados,
            teares=teares_page,
            total=total,
            pagina=pagina,
            por_pagina=por_pagina,
            total_paginas=total_paginas,
            estados=opcoes["estado"],
        )

    except Exception as e:
        # Qualquer falha (inclui OperationalError do Postgres) cai na página offline
        app.logger.exception("[INDEX] falha ao consultar DB: %s", e)
        return _render_offline()

# /login
@app.route("/login", methods=["GET", "POST"], endpoint="login")
def view_login():
    if request.method == "GET":
        email = (request.args.get("email") or "").strip().lower()
        return _render_try(["login.html", "AcheTece/Modelos/login.html"], email=email)

    # POST (clicou Continuar)
    email = (request.form.get("email") or "").strip().lower()
    if not email or "@" not in email:
        return _render_try(["login.html", "AcheTece/Modelos/login.html"], email=email, error="Informe um e-mail válido.")

    existe = Empresa.query.filter(func.lower(Empresa.email) == email).first()
    if not existe:
        return _render_try(["login.html", "AcheTece/Modelos/login.html"], email=email, no_account=True)

    return redirect(url_for("login_method", email=email))

@app.get("/login/")
def view_login_trailing():
    return redirect(url_for("login"), code=301)

# /login/metodo (escolha)
@app.get("/login/metodo", endpoint="login_method")
def view_login_method():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        flash("Informe um e-mail para continuar.", "warning")
        return redirect(url_for("login"))
    return _render_try(["login_method.html", "AcheTece/Modelos/login_method.html"], email=email)

@app.get("/login/método", endpoint="login_method_accent")
def view_login_method_alias_accent():
    return redirect(url_for("login_method", **request.args), code=301)

@app.get("/login/metodo/", endpoint="login_method_alias_trailing")
def view_login_method_alias_trailing():
    return redirect(url_for("login_method", **request.args), code=301)

# Disparar envio do código (POST)
@app.post("/login/codigo", endpoint="post_login_code")
def post_login_code():
    email = (request.form.get("email") or "").strip().lower()
    if not email:
        flash("Informe um e-mail válido.", "warning")
        return redirect(url_for("login"))

    existe = Empresa.query.filter(func.lower(Empresa.email) == email).first()
    if not existe:
        return _render_try(["login.html", "AcheTece/Modelos/login.html"], email=email, no_account=True)

    ok, msg = _otp_send(
        email,
        ip=(request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:64],
        ua=(request.headers.get("User-Agent") or "")[:255],
    )
    flash(msg, "success" if ok else "error")
    return redirect(url_for("login_code", email=email))

# Alias com acento (POST)
@app.post("/login/código", endpoint="post_login_code_accent")
def post_login_code_accent():
    return post_login_code()

# Tela para digitar o código (GET)
@app.get("/login/codigo", endpoint="login_code")
def get_login_code():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return redirect(url_for("login"))
    return _render_try(
        ["login_code.html", "AcheTece/Modelos/login_code.html"],
        email=email
    )

# Reenviar código (GET)
@app.get("/login/codigo/reenviar", endpoint="resend_login_code")
def resend_login_code():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return redirect(url_for("login"))
    ok, msg = _otp_send(
        email,
        ip=(request.headers.get("X-Forwarded-For") or request.remote_addr or "")[:64],
        ua=(request.headers.get("User-Agent") or "")[:255],
    )
    flash(msg, "success" if ok else "error")
    return redirect(url_for("login_code", email=email))

# Validar código (POST)
@app.post("/login/codigo/validar", endpoint="validate_login_code")
def validate_login_code():
    email = (request.form.get("email") or "").strip().lower()
    code = "".join((request.form.get(k, "") for k in ("d1","d2","d3","d4","d5","d6")))
    ok, msg = _otp_validate(email, code)
    if not ok:
        flash(msg, "error")
        return redirect(url_for("login_code", email=email))

    emp = Empresa.query.filter(func.lower(Empresa.email) == email).first()
    if emp:
        session["empresa_id"] = emp.id
        session["empresa_apelido"] = emp.apelido or emp.nome or emp.email.split("@")[0]
        flash("Bem-vindo!", "success")
        return redirect(url_for("painel_malharia"))

    flash("E-mail ainda não cadastrado. Conclua seu cadastro para continuar.", "info")
    return redirect(url_for("cadastro_get", email=email))

# Senha: TELA (GET)
@app.get("/login/senha", endpoint="view_login_password")
def view_login_password():
    email = (request.args.get("email") or "").strip().lower()
    if not email:
        return redirect(url_for("login"))
    return _render_try(
        ["login_senha.html", "AcheTece/Modelos/login_senha.html"],
        email=email
    )

# Senha: AUTENTICAR (POST)
@app.post("/login/senha/entrar", endpoint="post_login_password")
def post_login_password():
    email = (request.form.get("email") or "").strip().lower()
    senha = (request.form.get("senha") or "")
    user = Empresa.query.filter(func.lower(Empresa.email) == email).first()
    GENERIC_FAIL = "E-mail ou senha incorretos. Tente novamente."

    if not user:
        flash(GENERIC_FAIL, "error")
        return redirect(url_for("view_login_password", email=email))

    ok = False
    try:
        ok = check_password_hash(user.senha, senha)
    except Exception as e:
        app.logger.warning(f"[LOGIN WARN] check_password_hash: {e}")

    if not ok:
        flash(GENERIC_FAIL, "error")
        return redirect(url_for("view_login_password", email=email))

    if not DEMO_MODE and (user.status_pagamento or "").lower() not in ("aprovado", "ativo"):
        flash("Pagamento ainda não aprovado.", "warning")
        return redirect(url_for("login_method", email=email))

    session["empresa_id"] = user.id
    session["empresa_apelido"] = user.apelido or user.nome or user.email.split("@")[0]
    return redirect(url_for("painel_malharia"))

@app.get("/oauth/google", endpoint="oauth_google")
def oauth_google_disabled():
    return ("Login com Google está desabilitado no momento.", 501)

@app.route("/logout")
def logout():
    session.pop("empresa_id", None)
    session.pop("empresa_apelido", None)
    return redirect(url_for("index"))

# --------------------------------------------------------------------
# Onboarding helpers + Painel
# --------------------------------------------------------------------
def _empresa_basica_completa(emp: Empresa) -> bool:
    ok_resp = bool((emp.responsavel_nome or "").strip())
    ok_local = bool((emp.cidade or "").strip()) and bool((emp.estado or "").strip())
    ok_tel   = bool((emp.telefone or "").strip())
    return ok_resp, ok_local, ok_tel

def _conta_teares(emp_id: int) -> int:
    try:
        return Tear.query.filter_by(empresa_id=emp_id).count()
    except Exception:
        return 0

def _proximo_step(emp: Empresa) -> str:
    ok_resp, ok_local, ok_tel = _empresa_basica_completa(emp)
    if not (ok_resp and ok_local and ok_tel):
        return "perfil"
    if _conta_teares(emp.id) == 0:
        return "teares"
    return "resumo"

# --- sua rota do painel (substitua a versão atual por esta) ---
@app.route('/painel_malharia', endpoint="painel_malharia")
def painel_malharia():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))

    step = request.args.get("step") or _proximo_step(emp)
    teares = Tear.query.filter_by(empresa_id=emp.id).all()
    is_ativa = (emp.status_pagamento or "pendente") in ("ativo", "aprovado")

    checklist = {
        "perfil_ok": all(_empresa_basica_completa(emp)),
        "teares_ok": _conta_teares(emp.id) > 0,
        "plano_ok": is_ativa or DEMO_MODE,
        "step": step,
    }

    # >>> NOVO: notificações e chat
    notif_count, notif_lista = _get_notificacoes(emp.id)
    chat_nao_lidos = 0  # ajuste aqui se tiver chat real

    # tenta usar o que veio do BD; se vazio, detecta no filesystem
    foto_url = getattr(emp, "foto_url", None) or _foto_url_runtime(emp.id)
    
    return render_template(
        "painel_malharia.html",
        empresa=emp,
        teares=teares,
        assinatura_ativa=is_ativa,
        checklist=checklist,
        step=step,
        notificacoes=notif_count,
        notificacoes_lista=notif_lista,
        chat_nao_lidos=chat_nao_lidos,
        foto_url=foto_url,          # <<<<<<<<<<
    )

from urllib.parse import urlparse, parse_qsl, urlencode, urlunparse

def _with_cb(u: str, ts: int) -> str:
    """Anexa/atualiza _cb=<ts> na URL (usado para bust de cache)."""
    try:
        p = urlparse(u)
        q = dict(parse_qsl(p.query))
        q["_cb"] = str(ts)
        return urlunparse(p._replace(query=urlencode(q)))
    except Exception:
        return u  # em último caso, segue sem mudar

def _back_to_panel(ts: int):
    """Escolhe uma URL de retorno ao painel com cache-buster."""
    ref = request.referrer or ""
    # Preferimos referrer da mesma origem que contenha 'painel' (outra página do app)
    if ref:
        try:
            rp = urlparse(ref)
            # mesma origem?
            if rp.netloc == request.host:
                if "painel" in rp.path or "malharia" in rp.path:
                    return redirect(_with_cb(ref, ts))
        except Exception:
            pass
    # fallback: rota nomeada do painel
    return redirect(url_for('painel_malharia', _cb=ts))

@app.route('/perfil/foto', methods=['POST'])
def perfil_foto_upload():
    uid, email = _whoami()

    # pega o primeiro arquivo válido entre os inputs name="foto"
    f = None
    try:
        for fs in request.files.getlist('foto'):
            if fs and getattr(fs, "filename", ""):
                f = fs
                break
    except Exception:
        f = request.files.get('foto')

    # fallback extra (caso algum input tenha outro 'name')
    if (not f) or (not f.filename):
        for alt in ('fotoInputLib', 'fotoInputCam', 'fotoInputFile'):
            fs = request.files.get(alt)
            if fs and fs.filename:
                f = fs
                break

    ts = int(time.time())

    # se não estiver logado por algum motivo, apenas volte ao painel (não mande pro index)
    if not uid:
        flash('Você precisa estar logado para alterar a foto.', 'warning')
        return _back_to_panel(ts)

    if not f or f.filename == '':
        flash('Nenhum arquivo selecionado.', 'warning')
        return _back_to_panel(ts)

    if not _allowed_file(f.filename):
        flash('Formato não permitido. Use JPG, JPEG, PNG ou WEBP.', 'danger')
        return _back_to_panel(ts)

    filename  = secure_filename(f"{uid}_{ts}.webp")
    dest_path = os.path.join(AVATAR_DIR, filename)
    rel_path  = f"/static/uploads/avatars/{filename}"  # caminho estático
    rel_url   = f"{rel_path}?v={ts}"                   # cache-buster no src da imagem

    try:
        _save_square_webp(f, dest_path, side=400, quality=85)
    except Exception as e:
        app.logger.exception(f"[perfil/foto] Falha ao salvar: {e}")
        flash('Não foi possível processar a imagem. Tente outro arquivo.', 'danger')
        return _back_to_panel(ts)

    # tenta persistir no DB (quando existir campo); sessão garante a exibição imediata
    try:
        updated = False
        emp = _pegar_empresa_do_usuario(required=False)
        if emp is not None:
            if hasattr(emp, 'foto_url'):
                emp.foto_url = rel_path; updated = True   # no DB guardamos sem ?v
            elif hasattr(emp, 'logo_url'):
                emp.logo_url = rel_path; updated = True
        cu = globals().get('current_user')
        if cu is not None:
            if hasattr(cu, 'avatar_url'):
                setattr(cu, 'avatar_url', rel_path); updated = True
            elif hasattr(cu, 'photo_url'):
                setattr(cu, 'photo_url', rel_path); updated = True
        if updated:
            db.session.commit()
    except Exception as e:
        app.logger.warning(f"[perfil/foto] DB não atualizado: {e}")

    # sessão (com cache-buster) garante que o template mostre a nova foto na hora
    session['avatar_url'] = rel_url
    session.modified = True

    flash('Foto atualizada com sucesso!', 'success')
    return _back_to_panel(ts)

# --- CADASTRAR / LISTAR / SALVAR TEARES (SEM GATE DE ASSINATURA) ---
@app.route("/teares/cadastrar", methods=["GET", "POST"], endpoint="cadastrar_teares")
def cadastrar_teares():
    """
    SEM checagem de assinatura. Se o usuário está no painel (tem empresa na sessão),
    pode cadastrar/editar teares à vontade.
    """
    emp, _user = _get_empresa_usuario_da_sessao()
    if not emp:
        flash("Faça login para continuar.", "warning")
        return redirect(url_for("login"))

    if request.method == "POST":
        def _to_int(val):
            try:
                return int(float(str(val).replace(",", ".").strip()))
            except Exception:
                return None

        # O form manda 'Sim'/'Não'; garantimos um valor consistente em string
        elas_raw = (request.form.get("elastano") or "").strip().lower()
        if elas_raw in {"sim", "s", "1", "true", "on"}:
            elastano_str = "Sim"
        elif elas_raw in {"não", "nao", "n", "0", "false", "off"}:
            elastano_str = "Não"
        else:
            # se vier "Sim"/"Não" já normal, mantém
            elastano_str = request.form.get("elastano") or None

        t = Tear(
            empresa_id=emp.id,
            marca=(request.form.get("marca") or None),
            modelo=(request.form.get("modelo") or None),
            tipo=(request.form.get("tipo") or None),
            finura=_to_int(request.form.get("finura")),
            diametro=_to_int(request.form.get("diametro")),
            alimentadores=_to_int(request.form.get("alimentadores")),
            elastano=elastano_str,
        )
        db.session.add(t)

        # Campos extras que podem existir no seu banco (se não existirem no modelo, ignora sem quebrar)
        try:
            v = _to_int(request.form.get("pistas_cilindro"))
            if v is not None: setattr(t, "pistas_cilindro", v)
        except Exception:
            pass
        try:
            v = _to_int(request.form.get("pistas_disco"))
            if v is not None: setattr(t, "pistas_disco", v)
        except Exception:
            pass

        db.session.commit()
        flash("Tear cadastrado com sucesso!")
        # volta para o próprio formulário para permitir múltiplos cadastros em sequência
        return redirect(url_for("teares_form"))

    # GET: lista para apoiar edição/novos cadastros em série
    teares = Tear.query.filter_by(empresa_id=emp.id).order_by(Tear.id.desc()).all()
    return render_template(
        "cadastrar_teares.html",
        empresa=emp,
        teares=teares,
        tear=None,
        assinatura_ativa=(emp.status_pagamento or "pendente") in ("ativo", "aprovado"),
    )

# Alias amigável do painel: /painel/teares
@app.route("/painel/teares", methods=["GET", "POST"], endpoint="teares_form")
def teares_form():
    return cadastrar_teares()

# --------------------------------------------------------------------
# Cadastro
# --------------------------------------------------------------------
@app.get("/cadastro", endpoint="cadastro_get")
def cadastro_get():
    email = (request.args.get("email") or "").strip().lower()
    try:
        return render_template("cadastro.html", email=email)
    except TemplateNotFound:
        pass
    try:
        return render_template("AcheTece/Modelos/cadastro.html", email=email)
    except TemplateNotFound:
        return render_template(
            "cadastrar_empresa.html",
            estados=[
                'AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
                'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO'
            ],
            email=email
        )

@app.post("/cadastro", endpoint="cadastro_post")
def cadastro_post():
    tipo = (request.form.get("tipo_pessoa") or "pf").lower()
    cpf_cnpj = (request.form.get("cpf_cnpj") or "").strip()
    nome_completo = (request.form.get("nome") or "").strip()
    apelido = (request.form.get("apelido") or "").strip()
    nascimento = (request.form.get("nascimento") or "").strip()
    telefone = re.sub(r"\D+", "", request.form.get("telefone", "") or "")
    email = (request.form.get("email") or "").strip().lower()
    senha = (request.form.get("senha") or "")

    erros = {}
    if not email:
        erros["email"] = "Informe um e-mail válido."
    elif Empresa.query.filter(func.lower(Empresa.email) == email).first():
        erros["email"] = "Este e-mail já está cadastrado."
    if len(nome_completo) < 2:
        erros["nome"] = "Informe seu nome completo."
    if len(senha) < 6:
        erros["senha"] = "Crie uma senha com pelo menos 6 caracteres."

    if erros:
        try:
            return render_template(
                "cadastro.html",
                erros=erros, email=email, nome=nome_completo, apelido=apelido,
                telefone=telefone, cpf_cnpj=cpf_cnpj, tipo_pessoa=tipo,
                nascimento=nascimento
            )
        except TemplateNotFound:
            flash(next(iter(erros.values())), "error")
            return redirect(url_for("cadastro_get", email=email))

    partes = nome_completo.split()
    responsavel_nome = partes[0]
    responsavel_sobrenome = " ".join(partes[1:]) if len(partes) > 1 else None

    nova = Empresa(
        nome=apelido or nome_completo,
        apelido=apelido or None,
        email=email,
        senha=generate_password_hash(senha),
        cidade=None,
        estado=None,
        telefone=telefone or None,
        status_pagamento="pendente",
        responsavel_nome=responsavel_nome,
        responsavel_sobrenome=responsavel_sobrenome
    )
    db.session.add(nova)
    db.session.flush()

    u = Usuario.query.filter_by(email=email).first()
    if not u:
        u = Usuario(email=email, senha_hash=nova.senha, role=None, is_active=True)
        db.session.add(u)
        db.session.flush()
    nova.user_id = u.id
    db.session.commit()

    session["empresa_id"] = nova.id
    session["empresa_apelido"] = nova.apelido or nova.nome or email.split("@")[0]
    flash("Conta criada! Complete os dados da sua empresa para continuar.", "success")
    return redirect(url_for("editar_empresa"))

@app.route("/editar_tear/<int:id>", methods=["GET", "POST"])
def editar_tear(id):
    # usa sessão; não há gate de pagamento
    emp, _user = _get_empresa_usuario_da_sessao()
    if not emp:
        flash("Faça login para continuar.", "warning")
        return redirect(url_for("login"))

    tear = Tear.query.get_or_404(id)
    if tear.empresa_id != emp.id:
        abort(403)

    if request.method == "POST":
        def _to_int(val):
            try:
                return int(float(str(val).replace(",", ".").strip()))
            except Exception:
                return None

        tear.marca         = request.form.get("marca") or None
        tear.modelo        = request.form.get("modelo") or None
        tear.tipo          = request.form.get("tipo") or None
        tear.finura        = _to_int(request.form.get("finura"))
        tear.diametro      = _to_int(request.form.get("diametro"))
        tear.alimentadores = _to_int(request.form.get("alimentadores"))

        elas_raw = (request.form.get("elastano") or "").strip().lower()
        if elas_raw in {"sim","s","1","true","on"}:
            tear.elastano = "Sim"
        elif elas_raw in {"não","nao","n","0","false","off"}:
            tear.elastano = "Não"
        else:
            tear.elastano = request.form.get("elastano") or None

        db.session.commit()
        flash("Tear atualizado com sucesso!")
        return redirect(url_for("teares_form"))

    # GET: página dedicada de edição
    teares = Tear.query.filter_by(empresa_id=emp.id).order_by(Tear.id.desc()).all()
    return render_template("editar_tear.html", empresa=emp, tear=tear, teares=teares)

@app.post("/tear/<int:id>/excluir")
def excluir_tear(id):
    empresa = _pegar_empresa_do_usuario(required=True)
    if not isinstance(empresa, Empresa):
        return empresa
    tear = Tear.query.get_or_404(id)
    if tear.empresa_id != empresa.id:
        abort(403)

    db.session.delete(tear)
    db.session.commit()
    flash("Tear excluído com sucesso!", "success")

    next_url = request.args.get("next") or request.form.get("next")
    if next_url:
        try:
            # evita open redirect
            if urlparse(next_url).netloc in ("", request.host):
                return redirect(next_url)
        except Exception:
            pass
    return redirect(url_for("painel_malharia"))

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
        nome  = (request.form['nome'] or '').strip()
        apelido = (request.form.get('apelido') or '').strip()
        email = (request.form['email'] or '').lower().strip()
        senha = (request.form['senha'] or '').strip()
        cidade = (request.form['cidade'] or '').strip()
        estado = (request.form['estado'] or '').strip()
        telefone = _only_digits(request.form.get('telefone',''))
        responsavel_nome = (request.form.get('responsavel_nome') or '').strip()
        responsavel_sobrenome = (request.form.get('responsavel_sobrenome') or '').strip()

        # NOVOS CAMPOS
        endereco_full = (request.form.get('endereco') or '').strip()
        cep_raw = (request.form.get('cep') or '').strip()
        cep = _fmt_cep(cep_raw)

        erros = {}
        if len(telefone) < 10 or len(telefone) > 13:
            erros['telefone'] = 'Telefone inválido.'
        if Empresa.query.filter_by(nome=nome).first():
            erros['nome'] = 'Nome já existe.'
        if apelido and Empresa.query.filter_by(apelido=apelido).first():
            erros['apelido'] = 'Apelido em uso.'
        if Empresa.query.filter_by(email=email).first():
            erros['email'] = 'E-mail já cadastrado.'
        if estado not in estados:
            erros['estado'] = 'Estado inválido.'
        if not responsavel_nome or len(re.sub(r'[^A-Za-zÀ-ÿ]', '', responsavel_nome)) < 2:
            erros['responsavel_nome'] = 'Informe o nome do responsável.'
        # validações obrigatórias dos novos campos
        if not endereco_full:
            erros['endereco'] = 'Informe o endereço completo.'
        if not cep:
            erros['cep'] = 'Informe um CEP válido (00000-000).'

        if erros:
            return render_template(
                'cadastrar_empresa.html',
                erro='Corrija os campos.', erros=erros, estados=estados,
                nome=nome, apelido=apelido, email=email,
                cidade=cidade, estado=estado, telefone=telefone,
                responsavel_nome=responsavel_nome, responsavel_sobrenome=responsavel_sobrenome,
                endereco=endereco_full, cep=cep_raw
            )

        nova_empresa = Empresa(
            nome=nome,
            apelido=apelido or None,
            email=email,
            senha=generate_password_hash(senha),
            cidade=cidade,
            estado=estado,
            telefone=telefone,
            status_pagamento='pendente',
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=responsavel_sobrenome or None
        )
        # grava Endereço completo e CEP em colunas compatíveis
        _set_if_has(nova_empresa, ["endereco","logradouro","endereco_completo"], endereco_full)
        _set_if_has(nova_empresa, ["cep","CEP"], cep)

        db.session.add(nova_empresa)
        db.session.commit()

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
        session.clear()
        return redirect(url_for('login'))

    estados = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT','PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']

    if request.method == 'GET':
        # tenta montar valores atuais de endereço/CEP, independente do nome da coluna
        endereco_atual = getattr(empresa, 'endereco', None) or getattr(empresa, 'logradouro', None) or getattr(empresa, 'endereco_completo', '')
        cep_atual = getattr(empresa, 'cep', None) or getattr(empresa, 'CEP', '')

        # lista de cidades (se você tiver helper; se não, deixamos vazio e o JS carrega)
        try:
            cidades = lista_cidades_por_uf(empresa.estado) if getattr(empresa, "estado", None) else []
        except Exception:
            cidades = []

        return render_template(
            'editar_empresa.html',
            estados=estados,
            nome=empresa.nome or '',
            apelido=empresa.apelido or '',
            email=empresa.email or '',
            cidade=empresa.cidade or '',
            estado=empresa.estado or '',
            telefone=empresa.telefone or '',
            responsavel_nome=(empresa.responsavel_nome or ''),
            responsavel_sobrenome=(empresa.responsavel_sobrenome or ''),
            endereco=endereco_atual or '',
            cep=cep_atual or '',
            cidades=cidades
        )

    # POST
    nome  = (request.form.get('nome','') or '').strip()
    apelido = (request.form.get('apelido','') or '').strip()
    email = (request.form.get('email','') or '').strip().lower()
    senha = (request.form.get('senha','') or '').strip()
    cidade = (request.form.get('cidade','') or '').strip()
    estado = (request.form.get('estado','') or '').strip()
    telefone = _only_digits(request.form.get('telefone',''))
    responsavel_nome = (request.form.get('responsavel_nome') or '').strip()
    responsavel_sobrenome = (request.form.get('responsavel_sobrenome') or '').strip()

    # NOVOS CAMPOS
    endereco_full = (request.form.get('endereco') or '').strip()
    cep_raw = (request.form.get('cep') or '').strip()
    cep = _fmt_cep(cep_raw)

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
    # endereço/CEP obrigatórios na edição
    if not endereco_full:
        erros['endereco'] = 'Informe o endereço completo.'
    if not cep:
        erros['cep'] = 'Informe um CEP válido (00000-000).'

    if erros:
        try:
            cidades = lista_cidades_por_uf(estado) if estado else []
        except Exception:
            cidades = []
        return render_template(
            'editar_empresa.html',
            erro='Corrija os campos.', erros=erros, estados=estados,
            nome=nome or empresa.nome, apelido=apelido or empresa.apelido,
            email=email or empresa.email, cidade=cidade or empresa.cidade,
            estado=estado or empresa.estado, telefone=telefone or empresa.telefone,
            responsavel_nome=responsavel_nome or (empresa.responsavel_nome or ''),
            responsavel_sobrenome=responsavel_sobrenome or (empresa.responsavel_sobrenome or ''),
            endereco=endereco_full or (getattr(empresa,'endereco', None) or getattr(empresa,'logradouro', None) or getattr(empresa,'endereco_completo','')),
            cep=cep_raw or (getattr(empresa,'cep', None) or getattr(empresa,'CEP', '')),
            cidades=cidades
        )

    # aplica alterações
    empresa.nome = nome or empresa.nome
    empresa.apelido = apelido or empresa.apelido
    empresa.email = email or empresa.email
    empresa.cidade = cidade or empresa.cidade
    empresa.estado = estado or empresa.estado
    empresa.telefone = telefone or empresa.telefone
    empresa.responsavel_nome = responsavel_nome or empresa.responsavel_nome
    empresa.responsavel_sobrenome = responsavel_sobrenome or None

    # grava Endereço completo e CEP (com nomes alternativos de coluna)
    _set_if_has(empresa, ["endereco","logradouro","endereco_completo"], endereco_full)
    _set_if_has(empresa, ["cep","CEP"], cep)

    if senha:
        empresa.senha = generate_password_hash(senha)

    db.session.commit()
    session['empresa_apelido'] = empresa.apelido or empresa.nome or empresa.email.split('@')[0]
    return redirect(url_for('editar_empresa', ok=1))

# --- ROTA DA PERFORMANCE (substituir este bloco) ---
@app.route('/performance', methods=['GET'], endpoint='performance_acesso')
def performance_acesso():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))

    # Usa o agregador de analytics (A.1) já adicionado acima
    total_visitas, total_contatos, series = get_performance(emp.id)

    return render_template(
        'performance_acesso.html',
        empresa=emp,
        series=series,
        total_visitas=total_visitas,
        total_contatos=total_contatos
    )

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

@app.route('/admin/empresa_excluir/<int:empresa_id>', methods=['POST'])
@login_admin_requerido
def empresa_excluir(empresa_id):
    if session.get('admin_email') != 'gestao.achetece@gmail.com':
        flash('Acesso não autorizado.')
        return redirect(url_for('login'))
    empresa = Empresa.query.get_or_404(empresa_id)
    db.session.delete(empresa); db.session.commit()
    flash(f'Empresa "{empresa.nome}" excluída com sucesso!')
    return redirect(url_for('admin_empresas'))

# --- EXCLUIR EMPRESA (usuário logado; com parâmetro) ---
@app.post("/empresa/<int:empresa_id>/excluir")
def empresa_excluir_by_id(empresa_id):
    empresa = _pegar_empresa_do_usuario(required=True)
    if not isinstance(empresa, Empresa):
        return empresa

    if empresa.id != empresa_id:
        from flask import abort
        abort(403)

    # Se não tiver cascade no relacionamento, elimine os teares antes:
    try:
        Tear.query.filter_by(empresa_id=empresa.id).delete()
    except Exception:
        pass

    db.session.delete(empresa)
    db.session.commit()

    # limpar sessão básica
    for k in ("auth_user_id", "user_id", "login_email", "auth_email"):
        session.pop(k, None)

    flash("Conta da malharia excluída.")
    return redirect(url_for("index"))

# --------------------------------------------------------------------
# Admin: seed/impersonação
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
# Rota de teste de e-mail (manual)
# --------------------------------------------------------------------
@app.get("/admin/test-email")
def admin_test_email():
    if not _seed_ok():
        return "Não autorizado", 403
    to_addr = (request.args.get("to") or os.getenv("CONTACT_TO") or os.getenv("EMAIL_FROM") or os.getenv("SMTP_FROM") or "").strip()
    if not to_addr:
        return "Informe ?to=destinatario@dominio", 400
    html = "<h3>Teste de e-mail AcheTece</h3><p>Se você recebeu isto, o envio está funcionando.</p>"
    ok, msg = _smtp_send_direct(to_addr, "Teste AcheTece", html, "Teste AcheTece")
    return (f"OK: {msg}", 200) if ok else (f"ERRO: {msg}", 500)

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
    return render_template('pagamento_aprovado.html')

@app.route('/pagamento_erro')
def pagamento_erro():
    return render_template('pagamento_erro.html')

@app.route('/pagamento_pendente')
def pagamento_pendente():
    return render_template('pagamento_pendente.html')

@app.route("/webhook", methods=["GET", "POST"])
def webhook():
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
                html = render_template_string("""
                <p>Nome: <strong>{{nome}}</strong></p>
                <p>E-mail: <strong>{{email}}</strong></p>
                <hr>
                <p>{{mensagem}}</p>
                """, nome=nome, email=email, mensagem=mensagem)

                # destino do formulário de contato (defina CONTACT_TO no Render)
                contato_to = os.getenv("CONTACT_TO") or os.getenv("EMAIL_FROM") or ""
                if not contato_to:
                    raise RuntimeError("CONTACT_TO/EMAIL_FROM não configurado no ambiente.")

                ok = send_email(
                    to=contato_to,
                    subject=f"[AcheTece] Novo contato — {nome}",
                    html=html,
                    text=f"Nome: {nome}\nE-mail: {email}\n\nMensagem:\n{mensagem}"
                )
                enviado = ok
                if not ok:
                    erro = "Falha ao enviar. Tente novamente."
            except Exception as e:
                erro = f"Falha ao enviar: {e}"
    return render_template("fale_conosco.html", enviado=enviado, erro=erro)

@app.route("/quem_somos", endpoint="quem_somos")
@app.route("/quem_somos/")
@app.route("/quem-somos")
@app.route("/quem-somos/")
def view_quem_somos():
    return render_template("quem_somos.html")

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

@app.get("/static/icone_whatsapp.png")
def static_alias_whatsapp():
    return redirect(url_for('static', filename='ícone_whatsapp.png'), code=302)

@app.route('/malharia_info')
def malharia_info():
    return render_template('malharia_info.html')

# --- Perfil público da empresa ---
from flask import render_template, abort, redirect, url_for
# ajuste os imports dos seus modelos conforme seu projeto:
# from models import Empresa, Tear
# ou: from app.models import Empresa, Tear

@app.get("/empresa/<int:empresa_id>")
def empresa_perfil(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    teares = Tear.query.filter_by(empresa_id=empresa_id).order_by(Tear.tipo.asc()).all()

    # registra analytics de visita ao perfil público
    try:
        track_event("COMPANY_PROFILE_VIEW", company_id=empresa_id)
    except Exception:
        app.logger.exception("[analytics] falha ao registrar COMPANY_PROFILE_VIEW")

    return render_template("empresa_perfil.html", empresa=empresa, teares=teares)

# (opcional) compatibilidade com URLs antigas /empresas/<id>
@app.get("/empresas/<int:empresa_id>")
def empresas_redirect(empresa_id):
    return redirect(url_for("empresa_perfil", empresa_id=empresa_id), code=301)

# --------------------------------------------------------------------
# Entry point local
# --------------------------------------------------------------------
if __name__ == '__main__':
    app.run(debug=True, host='0.0.0.0', port=5000)
