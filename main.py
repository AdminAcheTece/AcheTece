from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    render_template_string, send_file, jsonify, abort, g
)
# Removido o uso de Flask-Mail; usamos Resend + SMTP com timeout
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import CSRFProtect, CSRFError
from flask_limiter import Limiter
from flask_limiter.util import get_remote_address
from billing_release import (verify_authorized_invoice, BillingError, ProviderError, MercadoPagoClient, money_cents,
                             price_cents_from_environment, next_expiration, fmt_brl,
                             verify_webhook_signature, check_verified_payment, credential_environment_allowed,
                             is_legacy_ipn_payment_hint, hml_legacy_ipn_hint_enabled)
from itsdangerous import URLSafeTimedSerializer, SignatureExpired, BadSignature
from werkzeug.security import generate_password_hash, check_password_hash
from functools import wraps
from datetime import datetime, timedelta
import mercadopago
import hmac
import os
import csv
import io
import re
import uuid
import secrets
import logging
import json
import requests
from datetime import timezone
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
from datetime import datetime, timedelta
from flask import current_app, request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid, formataddr
from authlib.integrations.flask_client import OAuth
from training_catalog import TRAINING_CATALOG, get_module, get_lesson
from sqlalchemy import UniqueConstraint
from flask import render_template, abort, send_from_directory

# SMTP direto (fallback)
import smtplib, ssl
from email.message import EmailMessage

# === Configurações/Constantes do AcheTece ===
# Dica: você pode ajustar pelo ambiente do Render: ASSIN_TOLERANCIA_DIAS=1..3
TOLERANCIA_DIAS = int(os.getenv("ASSIN_TOLERANCIA_DIAS", "1"))

# Se existir a linha antiga, deixe comentada para não confundir:
# ASSINATURA_GRACA_DIAS = 35  # (obsoleto; não usamos mais)

# --------------------------------------------------------------------
# Configuração básica
# --------------------------------------------------------------------
app = Flask(__name__)
app.logger.setLevel(logging.INFO)
_release_secret = (os.getenv('SECRET_KEY') or '').strip()
if len(_release_secret) < 32 or _release_secret == 'dev-key-unsafe':
    raise RuntimeError('SECRET_KEY forte e obrigatoria (minimo 32 caracteres)')
app.config['SECRET_KEY'] = _release_secret
app.config['PREFERRED_URL_SCHEME'] = 'https'
app.config['WTF_CSRF_CHECK_DEFAULT'] = True
app.config['WTF_CSRF_TIME_LIMIT'] = 4 * 60 * 60
csrf = CSRFProtect(app)
# Limite por IP; em multiplas instancias, configurar armazenamento Redis compartilhado.
limiter = Limiter(get_remote_address, app=app, default_limits=[],
                  storage_uri=os.getenv('RATELIMIT_STORAGE_URI', 'memory://'))

@app.errorhandler(CSRFError)
def _release_csrf_error(error):
    app.logger.warning('[SECURITY] CSRF rejeitado path=%s', request.path)
    return 'Requisicao nao autorizada. Atualize a pagina e tente novamente.', 400

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
# (apenas UM bloco; sem duplicações)
# --------------------------------------------------------------------
import os, re, json, ssl, logging
from typing import Tuple, Optional
from email.message import EmailMessage
from email.utils import make_msgid
import smtplib

# Config (mantém suas chaves atuais)
app.config.update(
    SMTP_HOST=os.getenv("SMTP_HOST", "smtp.gmail.com"),
    SMTP_PORT=int(os.getenv("SMTP_PORT", "465")),
    SMTP_USER=os.getenv("SMTP_USER", ""),
    SMTP_PASS=os.getenv("SMTP_PASS", ""),
    SMTP_FROM=os.getenv("SMTP_FROM", os.getenv("SMTP_USER", "")),
    MAIL_TIMEOUT=int(os.getenv("MAIL_TIMEOUT", "8")),
    MAIL_SUPPRESS_SEND=_env_bool("MAIL_SUPPRESS_SEND", False),
    OTP_DEV_FALLBACK=_env_bool("OTP_DEV_FALLBACK", False),

    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_SAMESITE="Lax",
    # Em homologacao no onrender.com, o cookie deve pertencer ao proprio host.
    # Em producao, preserva o dominio atual.
    SESSION_COOKIE_DOMAIN=(os.getenv('SESSION_COOKIE_DOMAIN') or None)
)

RESEND_API_KEY = os.getenv("RESEND_API_KEY") or ""
RESEND_DOMAIN  = os.getenv("RESEND_DOMAIN", "achetece.com.br")
EMAIL_FROM     = os.getenv("EMAIL_FROM", f"AcheTece <no-reply@{RESEND_DOMAIN}>")
REPLY_TO       = os.getenv("REPLY_TO", "")
SITE_URL       = os.getenv("SITE_URL", "https://www.achetece.com.br")

# === Google OAuth (Authlib) — registro do provedor ===
GOOGLE_CLIENT_ID = os.getenv("GOOGLE_CLIENT_ID", "")
GOOGLE_CLIENT_SECRET = os.getenv("GOOGLE_CLIENT_SECRET", "")

# Instancia o Authlib e registra o provedor Google usando OIDC
oauth = OAuth(app)
oauth.register(
    name="google",
    client_id=GOOGLE_CLIENT_ID,
    client_secret=GOOGLE_CLIENT_SECRET,
    server_metadata_url="https://accounts.google.com/.well-known/openid-configuration",
    client_kwargs={"scope": "openid email profile"}
)

def _extract_email(addr: str) -> str:
    m = re.search(r"<([^>]+)>", addr or "")
    s = m.group(1) if m else (addr or "")
    return s.strip()

def _domain_of(addr: str) -> str:
    e = _extract_email(addr)
    return e.split("@")[-1].lower() if "@" in e else ""

def _fallback_text(html: Optional[str], text: Optional[str]) -> str:
    if text:
        return text
    if not html:
        return "Verifique este e-mail em um cliente compatível com HTML."
    return re.sub(r"<[^>]+>", "", html).strip() or "Verifique este e-mail em um cliente compatível com HTML."

def _safe_from_address() -> str:
    # garante From dentro do domínio verificado do Resend
    from_domain = _domain_of(EMAIL_FROM)
    if RESEND_DOMAIN and from_domain == RESEND_DOMAIN:
        return EMAIL_FROM
    return f"AcheTece <no-reply@{RESEND_DOMAIN}>"

def _send_via_resend(to: str, subject: str, html: str, text: Optional[str] = None) -> Tuple[bool, str]:
    """
    Envio via Resend HTTP (estável e sem duplicação).
    Variáveis:
      RESEND_API_KEY (obrigatória)
      EMAIL_FROM / RESEND_DOMAIN / REPLY_TO
    """
    api = RESEND_API_KEY
    if not api:
        return False, "RESEND_API_KEY ausente"

    try:
        import requests
        payload = {
            "from": _safe_from_address(),
            "to": [to],
            "subject": subject,
            "html": html or "",
            "text": _fallback_text(html, text),
        }
        if REPLY_TO:
            payload["reply_to"] = REPLY_TO

        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": f"Bearer {api}", "Content-Type": "application/json"},
            json=payload,
            timeout=int(app.config.get("MAIL_TIMEOUT") or 8),
        )
        if r.status_code in (200, 201, 202):
            logging.info(f"[EMAIL/RESEND] Enviado para {to}. status={r.status_code}")
            return True, "OK"
        return False, f"Resend {r.status_code}: {r.text[:200]}"
    except Exception as e:
        logging.exception(f"[EMAIL/RESEND] Falha ao enviar para {to}: {e}")
        return False, f"Resend erro: {e!s}"

def _send_via_mailgun(to: str, subject: str, html: str, text: Optional[str] = None) -> Tuple[bool, str]:
    domain = os.getenv("MAILGUN_DOMAIN")
    key = os.getenv("MAILGUN_API_KEY")
    if not (domain and key):
        return False, "MAILGUN_DOMAIN/API_KEY ausentes"

    sender = os.getenv("MAILGUN_FROM") or f"AcheTece <no-reply@{domain}>"
    try:
        import requests
        url = f"https://api.mailgun.net/v3/{domain}/messages"
        data = {
            "from": sender,
            "to": to,
            "subject": subject,
            "text": _fallback_text(html, text),
            "html": html or "",
        }
        # Reply-To via header Mailgun
        if REPLY_TO:
            data["h:Reply-To"] = REPLY_TO

        r = requests.post(url, auth=("api", key), data=data, timeout=int(app.config.get("MAIL_TIMEOUT") or 8))
        if r.status_code in (200, 201, 202):
            return True, "OK"
        return False, f"Mailgun {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"Mailgun erro: {e!s}"

def _send_via_sendgrid(to: str, subject: str, html: str, text: Optional[str] = None) -> Tuple[bool, str]:
    key = os.getenv("SENDGRID_API_KEY")
    if not key:
        return False, "SENDGRID_API_KEY ausente"

    sender = os.getenv("SENDGRID_FROM") or _extract_email(_safe_from_address()) or "no-reply@achetece.com.br"
    try:
        import requests
        url = "https://api.sendgrid.com/v3/mail/send"
        payload = {
            "personalizations": [{"to": [{"email": to}], "subject": subject}],
            "from": {"email": sender},
            "content": [
                {"type": "text/plain", "value": _fallback_text(html, text)},
                {"type": "text/html", "value": html or ""},
            ],
        }
        if REPLY_TO:
            payload["reply_to"] = {"email": REPLY_TO}

        r = requests.post(
            url,
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            data=json.dumps(payload),
            timeout=int(app.config.get("MAIL_TIMEOUT") or 8),
        )
        if r.status_code == 202:
            return True, "OK"
        return False, f"SendGrid {r.status_code}: {r.text[:200]}"
    except Exception as e:
        return False, f"SendGrid erro: {e!s}"

def _send_via_smtp(to: str, subject: str, html: str, text: Optional[str] = None) -> Tuple[bool, str]:
    """Fallback via SMTP (SSL/TLS) — agora será usado de verdade."""
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
    msg["Message-ID"] = make_msgid(domain="achetece.com.br")
    if REPLY_TO:
        msg["Reply-To"] = REPLY_TO

    msg.set_content(_fallback_text(html, text))
    msg.add_alternative(html or "", subtype="html")

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
                s.ehlo()
                s.login(user, pwd)
                s.send_message(msg)
        return True, "OK"
    except Exception as e:
        app.logger.exception(f"[EMAIL/SMTP] Falha ao enviar para {to}: {e}")
        return False, f"smtp_error: {e!s}"

def send_email(to: str, subject: str, html: str, text: Optional[str] = None) -> bool:
    from flask import current_app

    if current_app.config.get("MAIL_SUPPRESS_SEND"):
        current_app.logger.info(f"[send_email] MAIL_SUPPRESS_SEND=True — suprimido. to={to} subject={subject}")
        return True

    # 1) Tenta Flask-Mail (se existir)
    try:
        mail_ext = (getattr(current_app, "extensions", {}) or {}).get("mail")
        if mail_ext:
            from flask_mail import Message
            msg = Message(
                subject=subject,
                recipients=[to],
                sender=current_app.config.get("MAIL_DEFAULT_SENDER") or EMAIL_FROM,
            )
            msg.body = _fallback_text(html, text)
            msg.html = html
            msg.extra_headers = {
                "Content-Language": "pt-BR",
                "Message-ID": make_msgid(domain="achetece.com.br"),
            }
            if REPLY_TO:
                msg.reply_to = REPLY_TO
            mail_ext.send(msg)
            current_app.logger.info("[send_email] via Flask-Mail")
            return True
    except Exception:
        current_app.logger.exception("[send_email] Flask-Mail falhou")

    # 2) Provedores HTTP
    ok, why = _send_via_resend(to, subject, html, text)
    if ok:
        return True

    ok, why = _send_via_mailgun(to, subject, html, text)
    if ok:
        return True

    ok, why = _send_via_sendgrid(to, subject, html, text)
    if ok:
        return True

    # 3) Fallback SMTP (AGORA SIM)
    ok, why2 = _send_via_smtp(to, subject, html, text)
    if ok:
        current_app.logger.info("[send_email] via SMTP fallback")
        return True

    current_app.logger.error(f"[send_email] nenhum backend aceitou. http_last={why} smtp_last={why2}")
    return False

def _plano_label(p: str | None) -> str:
    p = (p or "").strip().lower()
    if p in ("anual","annual","ano","yearly","12m"):
        return "Anual"
    return "Mensal"

app.jinja_env.filters["plano_label"] = _plano_label

# --------------------------------------------------------------------
# E-mail transacional: Pagamento confirmado (AcheTece)
# --------------------------------------------------------------------
def send_payment_confirmation_email(to_email: str, nome_empresa: str, plano: str) -> bool:
    import html as _html

    # 1) Normaliza BASE URL (evita //login)
    base = (SITE_URL or "https://www.achetece.com.br").rstrip("/")

    # 2) Normaliza plano
    plano_norm = (plano or "mensal").strip().lower()
    if plano_norm not in ("mensal", "anual"):
        plano_norm = "mensal"

    plano_label = "Plano Mensal" if plano_norm == "mensal" else "Plano Anual (15% OFF)"

    # 3) Links
    login_url = f"{base}/login"
    painel_url = f"{base}/painel_malharia"

    # 4) Assunto (neutro e bom p/ entrega)
    subject = "Pagamento confirmado — AcheTece"

    # 5) Texto puro (fallback)
    nome_txt = (nome_empresa or "Sua malharia").strip()
    text_body = f"""Olá, {nome_txt}!

Seu pagamento foi confirmado e seu acesso ao AcheTece foi liberado.

Plano: {plano_label}

Login:
{login_url}

Painel:
{painel_url}

Se precisar de suporte, responda este e-mail.
"""

    # 6) HTML (sanitiza nome)
    nome_html = _html.escape(nome_txt)

    html_body = f"""
    <div style="font-family:Inter,Arial,sans-serif;background:#f5f5f4;padding:24px;">
      <div style="max-width:560px;margin:0 auto;background:#ffffff;border:1px solid #e5e7eb;border-radius:16px;overflow:hidden;">
        <div style="padding:18px 18px 10px 18px;">
          <h2 style="margin:0;color:#111;font-size:20px;">Pagamento confirmado ✅</h2>

          <p style="margin:10px 0 0 0;color:#333;line-height:1.5;">
            Olá, <strong>{nome_html}</strong>!<br>
            Seu pagamento foi confirmado e seu acesso ao <strong>AcheTece</strong> foi liberado.
          </p>

          <div style="margin:14px 0;padding:12px;border-radius:12px;background:#f1f2e8;border:1px solid #bfbfa8;">
            <div style="font-weight:800;color:#111;">{plano_label}</div>
            <div style="color:#333;font-size:13px;margin-top:4px;">Você já pode acessar normalmente.</div>
          </div>

          <a href="{login_url}"
             style="display:inline-block;background:#000;color:#b6f34d;text-decoration:none;font-weight:800;
                    padding:12px 16px;border-radius:999px;margin-top:6px;">
             Fazer login
          </a>

          <p style="margin:14px 0 0 0;color:#666;font-size:13px;line-height:1.5;">
            Se o botão não abrir, copie e cole:<br>
            <span style="color:#111;">{login_url}</span>
          </p>

          <p style="margin:10px 0 0 0;color:#666;font-size:13px;">
            Ir para o painel: <a href="{painel_url}" style="color:#7B7424;font-weight:800;text-decoration:none;">{painel_url}</a>
          </p>
        </div>

        <div style="border-top:1px solid #eee;padding:12px 18px;color:#666;font-size:12px;">
          Se precisar de suporte, responda este e-mail.
        </div>
      </div>
    </div>
    """

    return send_email(to_email, subject, html_body, text_body)

def _otp_validate(email: str, codigo: str):
    """
    Valida o OTP de login considerando os dois formatos possíveis:
      A) session['otp_login'] = { '<email>': { code, exp(timestamp), attempts, ... } }
      B) session['otp']       = { 'email':..., 'code':..., 'expires': iso, 'attempts': ... }
    Retorna (ok: bool, msg: str).
    """
    email = (email or "").strip().lower()
    codigo = (codigo or "").strip()

    # --- Formato A: otp_login por e-mail ------------------------------------
    otp_login = session.get("otp_login")
    if isinstance(otp_login, dict) and email in otp_login and isinstance(otp_login[email], dict):
        rec = otp_login[email]

        # Tentativas
        rec["attempts"] = int(rec.get("attempts", 0)) + 1
        # Persistir contador
        otp_login[email] = rec
        session["otp_login"] = otp_login
        session.modified = True

        # Expiração (timestamp UTC)
        try:
            exp_ts = float(rec.get("exp", 0))
        except Exception:
            exp_ts = 0.0
        if exp_ts and datetime.utcnow().timestamp() > exp_ts:
            # Limpa apenas este e-mail
            try:
                del otp_login[email]
            except Exception:
                pass
            session["otp_login"] = otp_login
            session.modified = True
            return False, "Código expirado. Solicite um novo."

        # Comparação
        if str(rec.get("code", "")).strip() != str(codigo):
            if rec["attempts"] > 5:
                # Muitas tentativas -> invalida este OTP
                try:
                    del otp_login[email]
                except Exception:
                    pass
                session["otp_login"] = otp_login
                session.modified = True
                return False, "Muitas tentativas. Solicite um novo código."
            return False, "Código incorreto. Tente novamente."

        # Sucesso -> limpar OTP deste e-mail
        try:
            del otp_login[email]
        except Exception:
            pass
        session["otp_login"] = otp_login
        session.modified = True
        return True, "OK"

    # --- Formato B: otp único com 'email'/'expires' ISO ---------------------
    otp_blob = session.get("otp") or {}
    if isinstance(otp_blob, dict):
        rec = None
        if otp_blob.get("email") == email:
            rec = otp_blob
        elif email in otp_blob and isinstance(otp_blob[email], dict):
            rec = otp_blob[email]

        if rec:
            rec["attempts"] = int(rec.get("attempts", 0)) + 1
            session["otp"] = otp_blob
            session.modified = True

            expires_iso = rec.get("expires")
            if expires_iso:
                try:
                    exp_dt = datetime.fromisoformat(expires_iso)
                    if datetime.utcnow() > exp_dt:
                        session.pop("otp", None)
                        session.modified = True
                        return False, "Código expirado. Solicite um novo."
                except Exception:
                    session.pop("otp", None)
                    session.modified = True
                    return False, "Código inválido. Solicite um novo."

            if str(rec.get("code", "")).strip() != str(codigo):
                if rec["attempts"] > 5:
                    session.pop("otp", None)
                    session.modified = True
                    return False, "Muitas tentativas. Solicite um novo código."
                return False, "Código incorreto. Tente novamente."

            session.pop("otp", None)
            session.modified = True
            return True, "OK"

    return False, "Código não encontrado para este e-mail. Reenvie o código."

# Mercado Pago (mantido para compat)
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN") or os.getenv("MERCADO_PAGO_TOKEN", "")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
# Preco canônico da release: o checkout e a vitrine usam a mesma fonte.
# Sem variaveis explicitas de preco, a liberacao financeira falha fechada.
def _release_price_cents(plano: str) -> int:
    return price_cents_from_environment(os.environ, plano)

def _release_billing_ready() -> bool:
    if not _env_bool('ACHETECE_BILLING_V1_ENABLED', False):
        return False
    try:
        mensal_cents = _release_price_cents('mensal')
        anual_cents = _release_price_cents('anual')
        if anual_cents >= mensal_cents * 12:
            return False
        for key in ('MP_ACCESS_TOKEN', 'MP_WEBHOOK_SECRET', 'MP_COLLECTOR_ID',
                    'ACHETECE_BILLING_CALLBACK_BASE_URL'):
            if not (os.environ.get(key) or '').strip():
                return False
        token = os.environ['MP_ACCESS_TOKEN'].strip()
        modo = (os.getenv('ACHETECE_BILLING_ENV') or '').strip().lower()
        # Mercado Pago tambem usa APP_USR para credenciais de TESTE.
        # Com esse prefixo no sandbox, exigir verificacao humana explicita
        # na aba Teste do painel, pois o prefixo NAO distingue producao.
        if not credential_environment_allowed(
            token, modo,
            test_credential_confirmed=_env_bool('ACHETECE_MP_TEST_TOKEN_CONFIRMED', False),
        ):
            return False
        base = os.environ['ACHETECE_BILLING_CALLBACK_BASE_URL'].rstrip('/')
        parsed = urlparse(base)
        if parsed.scheme != 'https' or not parsed.netloc or parsed.path not in ('','/') or parsed.query:
            return False
        return True
    except BillingError:
        return False

PLAN_MONTHLY = 0.0  # legado: na release nunca usar como fonte de cobranca
PLAN_YEARLY = 0.0


# DEMO
DEMO_MODE  = os.getenv("DEMO_MODE", "false").lower() == "true"
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
# === VENCIMENTO MENSAL BR (próximo dia útil) ================================
from datetime import date, datetime, timedelta
from calendar import monthrange
try:
    from zoneinfo import ZoneInfo
except Exception:
    ZoneInfo = None

def _easter_date(year: int) -> date:  # Domingo de Páscoa
    a = year % 19
    b = year // 100
    c = year % 100
    d = b // 4
    e = b % 4
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19*a + b - d - g + 15) % 30
    i = c // 4
    k = c % 4
    L = (32 + 2*e + 2*i - h - k) % 7
    m = (a + 11*h + 22*L) // 451
    month = (h + L - 7*m + 114) // 31
    day = ((h + L - 7*m + 114) % 31) + 1
    return date(year, month, day)

def _br_feriados_nacionais(year: int) -> set[date]:
    # Feriados nacionais oficiais (fixos) + Sexta-feira Santa (móvel)
    easter = _easter_date(year)
    sexta_santa = easter - timedelta(days=2)
    return {
        date(year, 1, 1),   # Confraternização Universal
        date(year, 4, 21),  # Tiradentes
        date(year, 5, 1),   # Dia do Trabalho
        date(year, 9, 7),   # Independência
        date(year,10,12),   # N. Sra. Aparecida
        date(year,11, 2),   # Finados
        date(year,11,15),   # Proclamação da República
        date(year,12,25),   # Natal
        sexta_santa,        # Paixão de Cristo (nacional)
    }

def _ultimo_dia_mes(y: int, m: int) -> int:
    return monthrange(y, m)[1]

def _add_meses(d: date, n: int = 1) -> date:
    m = d.month - 1 + n
    y = d.year + m // 12
    m = m % 12 + 1
    return date(y, m, min(d.day, _ultimo_dia_mes(y, m)))

def _proximo_dia_util_br(d: date) -> date:
    # Considera fim de semana e feriados nacionais
    fer = set()
    for y in (d.year - 1, d.year, d.year + 1):
        fer |= _br_feriados_nacionais(y)
    while d.weekday() >= 5 or d in fer:  # 5=sáb, 6=dom
        d += timedelta(days=1)
    return d

def calc_vencimento_mensal_br(empresa, last_paid_at: datetime | date | None = None):
    """Retorna (due_date: date, dias_restantes: int). 
       Âncora do ciclo = dia do last_paid_at (ou data de início/criação)."""
    hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date() if ZoneInfo else date.today()

    def _to_date(v):
        if not v: return None
        return v.date() if isinstance(v, datetime) else v

    base = _to_date(last_paid_at) \
        or _to_date(getattr(empresa, "assin_ultimo_pagamento", None)) \
        or _to_date(getattr(empresa, "assin_data_inicio", None)) \
        or _to_date(getattr(empresa, "created_at", None)) \
        or hoje

    # Próximo “nominal” é +1 mês mantendo o dia; depois ajusta p/ dia útil
    nominal = _add_meses(base, 1)
    while nominal <= hoje:
        nominal = _add_meses(nominal, 1)

    due = _proximo_dia_util_br(nominal)
    return due, (due - hoje).days
# ===========================================================================

def _public_base_url() -> str:
    """
    Retorna a base pública do site para construir callbacks do Mercado Pago.
    Prioriza config/variável de ambiente e, por fim, força www.achetece.com.br.
    """
    forced = (
        current_app.config.get("PUBLIC_BASE_URL")
        or os.getenv("PUBLIC_BASE_URL")
    )
    if forced:
        return forced.rstrip("/")
    # último recurso: força o host oficial em HTTPS
    return "https://www.achetece.com.br"
    
from sqlalchemy import inspect, text

def _ensure_teares_pistas_cols():
    """Adiciona pistas_cilindro e pistas_disco se ainda não existirem."""
    tbl = Tear.__table__.name                      # geralmente "tear"
    insp = inspect(db.engine)
    existentes = {c["name"] for c in insp.get_columns(tbl)}
    stmts = []
    if "pistas_cilindro" not in existentes:
        stmts.append(text(f'ALTER TABLE {tbl} ADD COLUMN pistas_cilindro INTEGER'))
    if "pistas_disco" not in existentes:
        stmts.append(text(f'ALTER TABLE {tbl} ADD COLUMN pistas_disco INTEGER'))
    if stmts:
        with db.engine.begin() as conn:
            for s in stmts:
                conn.execute(s)

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

# Administrador: e-mail e hash de senha são configurados SOMENTE no Render.
# Sem variáveis, o acesso administrativo permanece fechado (fail closed).
ADMIN_EMAIL = (os.getenv("ADMIN_EMAIL") or "").strip().lower()
ADMIN_PASSWORD_HASH = (os.getenv("ADMIN_PASSWORD_HASH") or "").strip()


def _admin_autenticado() -> bool:
    return bool(ADMIN_EMAIL and ADMIN_PASSWORD_HASH
                and session.get("admin_auth_version") == "v2"
                and hmac.compare_digest(session.get("admin_email", ""), ADMIN_EMAIL))


def login_admin_requerido(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not _admin_autenticado():
            flash('Acesso não autorizado.', 'error')
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
        app.logger.warning("[DB] conexao indisponivel; detalhes privados omitidos; tipo=%s", type(e).__name__)
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
    if not url:
        raise RuntimeError('DATABASE_URL ou INTERNAL_DATABASE_URL obrigatoria na release')
    return url

FINAL_DB_URI = _pick_database_uri()

# Homologacao financeira: confirmar o banco ANTES de criar qualquer objeto.
# Nao habilitado na producao, somente com variavel explicita na HML.
_hml_db_expected = (os.getenv('ACHETECE_HML_EXPECTED_DB_NAME') or '').strip()
if _hml_db_expected:
    if _hml_db_expected != 'achetece_release_v1_test':
        raise RuntimeError('HML: identificador esperado de banco invalido')
    if not FINAL_DB_URI.startswith('postgresql+psycopg://'):
        raise RuntimeError('HML: PostgreSQL exigido')
    try:
        _hml_probe = create_engine(FINAL_DB_URI, connect_args={'connect_timeout': 5})
        try:
            with _hml_probe.connect() as _hml_conn:
                _hml_db_actual = _hml_conn.execute(text('SELECT current_database()')).scalar_one()
        finally:
            _hml_probe.dispose()
    except Exception:
        raise RuntimeError('HML: nao foi possivel validar banco isolado') from None
    if _hml_db_actual != _hml_db_expected:
        raise RuntimeError('HML: conexao bloqueada; banco de destino incorreto')
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
# --- IMPORTS necessários no topo do main.py ---
from datetime import datetime, timedelta
from sqlalchemy.ext.hybrid import hybrid_property
from sqlalchemy import and_, or_, func, text
# ----------------------------------------------

# Prazo de validade por plano (dias)
ASSINATURA_DIAS_MENSAL = 35          # sua janela atual
ASSINATURA_DIAS_ANUAL  = 370         # 365 + 5 dias de folga (ajuste se quiser)

STATUS_ATIVO_EQUIV = {"ativo", "aprovado", "approved", "paid", "active", "trial"}
STATUS_PENDENTE_EQUIV = {"pendente", "pending", "in_process", "inprocess"}

class Usuario(db.Model):
    __tablename__ = 'usuario'
    id = db.Column(db.Integer, primary_key=True)
    email = db.Column(db.String(255), unique=True, nullable=False)
    senha_hash = db.Column(db.String(255))
    google_id = db.Column(db.String(255))
    role = db.Column(db.String(20), index=True, nullable=True)  # 'cliente' | 'malharia' | 'admin'
    is_active = db.Column(db.Boolean, default=True)
    created_at = db.Column(db.DateTime, default=datetime.utcnow)

# Prazo de validade por plano (dias)
ASSINATURA_DIAS_MENSAL = 35          # sua janela atual
ASSINATURA_DIAS_ANUAL  = 370         # 365 + 5 dias de folga (ajuste se quiser)

STATUS_ATIVO_EQUIV = {"ativo", "aprovado", "approved", "paid", "active", "trial"}
STATUS_PENDENTE_EQUIV = {"pendente", "pending", "in_process", "inprocess"}

class Empresa(db.Model):
    id = db.Column(db.Integer, primary_key=True)
    user_id = db.Column(db.Integer, db.ForeignKey('usuario.id'), unique=True)
    usuario = db.relationship('Usuario', backref=db.backref('empresa', uselist=False))

    foto_url = db.Column(db.String(255))
    plano = db.Column(db.String(20), default="mensal", index=True)  # mensal | anual

    nome = db.Column(db.String(100), nullable=False, unique=True)
    apelido = db.Column(db.String(50), unique=True)
    email = db.Column(db.String(100), nullable=False, unique=True)
    senha = db.Column(db.String(200), nullable=False)

    cidade = db.Column(db.String(100))
    estado = db.Column(db.String(2))
    telefone = db.Column(db.String(20))

    # ✅ plano escolhido pela malharia: "mensal" ou "anual"
    # Se você já tem essa coluna em outro lugar, mantenha apenas 1.
    plano = db.Column(db.String(10), default="mensal", nullable=False, index=True)

    status_pagamento = db.Column(db.String(20), default='pendente', index=True)
    data_pagamento = db.Column(db.DateTime)  # ultimo pagamento aprovado (UTC)
    assinatura_ate = db.Column(db.DateTime)  # vigencia explicita (UTC), release V1

    teares = db.relationship('Tear', backref='empresa', lazy=True, cascade="all, delete-orphan")

    responsavel_nome = db.Column(db.String(120))
    responsavel_sobrenome = db.Column(db.String(120))
    endereco = db.Column(db.String(240))
    cep      = db.Column(db.String(9))

    # ---------- helpers ----------
    @staticmethod
    def _plano_norm(plano: str) -> str:
        p = (plano or "").strip().lower()
        return "anual" if p == "anual" else "mensal"

    @staticmethod
    def _dias_por_plano(plano: str) -> int:
        p = Empresa._plano_norm(plano)
        return ASSINATURA_DIAS_ANUAL if p == "anual" else ASSINATURA_DIAS_MENSAL

    @property
    def status_pagamento_norm(self) -> str:
        s = (self.status_pagamento or "").strip().lower()
        if s in STATUS_ATIVO_EQUIV:
            return "ativo"
        if s in STATUS_PENDENTE_EQUIV:
            return "pendente"
        # fallback seguro
        return "pendente"

    @hybrid_property
    def assinatura_ativa(self) -> bool:
        if (self.status_pagamento or '').strip().lower() != 'ativo':
            return False
        if self.assinatura_ate is not None:
            return self.assinatura_ate > datetime.utcnow()
        # Contas anteriores a release preservam a vigencia ED01.
        return bool(self.data_pagamento and (
            self.data_pagamento + timedelta(days=self._dias_por_plano(self.plano))
        ) > datetime.utcnow())

    @assinatura_ativa.expression
    def assinatura_ativa(cls):
        from sqlalchemy import case
        legacy_expiration = case(
            (func.lower(func.coalesce(cls.plano, 'mensal')) == 'anual',
             cls.data_pagamento + timedelta(days=ASSINATURA_DIAS_ANUAL)),
            else_=cls.data_pagamento + timedelta(days=ASSINATURA_DIAS_MENSAL),
        )
        return and_(
            func.lower(func.coalesce(cls.status_pagamento, '')) == 'ativo',
            func.coalesce(cls.assinatura_ate, legacy_expiration) > func.now(),
        )

    @property
    def assinatura_expira_em(self):
        if self.assinatura_ate is not None:
            return self.assinatura_ate
        if self.data_pagamento is None:
            return None
        return self.data_pagamento + timedelta(days=self._dias_por_plano(self.plano))


class BillingIntent(db.Model):
    __tablename__ = 'billing_intent'
    id = db.Column(db.Integer, primary_key=True)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False, index=True)
    external_reference = db.Column(db.String(100), unique=True, nullable=False)
    plano = db.Column(db.String(10), nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default='BRL')
    status = db.Column(db.String(30), nullable=False, default='created')
    provider_resource_id = db.Column(db.String(160), unique=True)
    checkout_url = db.Column(db.String(600))
    cancel_at_period_end = db.Column(db.Boolean, nullable=False, default=False)
    created_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)
    updated_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)


class BillingPayment(db.Model):
    __tablename__ = 'billing_payment'
    id = db.Column(db.Integer, primary_key=True)
    intent_id = db.Column(db.Integer, db.ForeignKey('billing_intent.id'), nullable=False)
    provider_payment_id = db.Column(db.String(60), unique=True, nullable=False)
    amount_cents = db.Column(db.Integer, nullable=False)
    currency = db.Column(db.String(3), nullable=False, default='BRL')
    status = db.Column(db.String(24), nullable=False)
    period_start = db.Column(db.DateTime, nullable=False)
    period_end = db.Column(db.DateTime, nullable=False)
    processed_at = db.Column(db.DateTime, nullable=False, default=datetime.utcnow)


class Tear(db.Model):
    # __tablename__ = 'tear'  # opcional (SQLAlchemy infere 'tear')
    id = db.Column(db.Integer, primary_key=True)
    marca = db.Column(db.String(100), nullable=False)
    modelo = db.Column(db.String(100), nullable=False)
    tipo = db.Column(db.String(20), nullable=False)
    finura = db.Column(db.Integer, nullable=False)
    diametro = db.Column(db.Integer, nullable=False)
    alimentadores = db.Column(db.Integer, nullable=False)
    # novo
    pistas_cilindro = db.Column(db.Integer, nullable=True)
    pistas_disco    = db.Column(db.Integer, nullable=True)
    # você usa string para elastano (Sim/Não) — mantenha:
    elastano = db.Column(db.String(10), nullable=False)
    empresa_id = db.Column(db.Integer, db.ForeignKey('empresa.id'), nullable=False)
    # (Opcional) se existir flag de tear
    # ativo = db.Column(db.Boolean, default=True, index=True)

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

class TrainingProgress(db.Model):
    __tablename__ = "training_progress"

    id = db.Column(db.Integer, primary_key=True)
    company_id = db.Column(db.Integer, db.ForeignKey("empresa.id"), index=True, nullable=False)

    module_key = db.Column(db.String(32), index=True, nullable=False)
    lesson_key = db.Column(db.String(32), index=True, nullable=False)

    status = db.Column(db.String(16), default="not_started", nullable=False)  # not_started | in_progress | done
    score = db.Column(db.Integer, nullable=True)

    updated_at = db.Column(db.DateTime, default=datetime.utcnow, onupdate=datetime.utcnow)
    completed_at = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        UniqueConstraint("company_id", "module_key", "lesson_key", name="uq_training_progress"),
    )

class ProgressoAula(db.Model):
    __tablename__ = "progresso_aula"
    id = db.Column(db.Integer, primary_key=True)

    empresa_id = db.Column(db.Integer, db.ForeignKey("empresa.id"), nullable=False, index=True)
    modulo = db.Column(db.String(20), nullable=False, index=True)  # ex: "m0"
    aula = db.Column(db.String(20), nullable=False, index=True)    # ex: "a1"

    concluido_em = db.Column(db.DateTime, nullable=True)

    __table_args__ = (
        db.UniqueConstraint("empresa_id", "modulo", "aula", name="uq_prog_aula_empresa_modulo_aula"),
    )

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

def _ensure_pagamento_cols():
    # cria as colunas se não existirem (PostgreSQL)
    sql = """
    ALTER TABLE empresa
      ADD COLUMN IF NOT EXISTS assinatura_status VARCHAR(20) DEFAULT 'pending',
      ADD COLUMN IF NOT EXISTS assinatura_expira_em TIMESTAMPTZ NULL;
    """
    try:
        with db.engine.begin() as con:
            con.exec_driver_sql(sql)
        app.logger.info("[BOOT] Pagamento: colunas OK")
    except Exception as e:
        app.logger.error(f"[BOOT] Falha ao garantir colunas de pagamento: {e}")

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

def _ensure_empresa_plano_column():
    try:
        with db.engine.connect() as con:
            con.exec_driver_sql(
                "ALTER TABLE empresa ADD COLUMN IF NOT EXISTS plano VARCHAR(16) DEFAULT 'mensal'"
            )
            con.exec_driver_sql(
                "UPDATE empresa SET plano='mensal' WHERE plano IS NULL OR TRIM(plano)=''"
            )
        app.logger.info("[BOOT] coluna empresa.plano garantida.")
    except Exception as e:
        app.logger.warning(f"[BOOT] coluna plano: {e}")
     

def _ensure_empresa_foto_column():
    """
    Garante que a tabela 'empresa' tenha a coluna foto_url (VARCHAR(255)).
    Executa um ALTER TABLE IF NOT EXISTS, seguro para rodar mais de uma vez.
    """
    try:
        with db.engine.begin() as conn:
            conn.execute(text("""
                ALTER TABLE empresa
                ADD COLUMN IF NOT EXISTS foto_url VARCHAR(255)
            """))
        app.logger.info("[] coluna empresa.foto_url OK")
    except Exception as e:
        app.logger.warning(f"[BOOT] não foi possível garantir empresa.foto_url: {e}")

from sqlalchemy import inspect, text

def _boot_ensure_empresa_plano_column():
    """
    Garante que a coluna empresa.plano exista.
    - Compatível com Postgres (Render) e não quebra dev local.
    - Executa DDL via engine (não depende do ORM carregar Empresa).
    """
    try:
        insp = inspect(db.engine)
        cols = {c["name"] for c in insp.get_columns("empresa")}

        with db.engine.begin() as conn:
            if "plano" not in cols:
                conn.exec_driver_sql("ALTER TABLE empresa ADD COLUMN plano VARCHAR(20)")
                # tenta colocar default no Postgres (se falhar, segue)
                try:
                    conn.exec_driver_sql("ALTER TABLE empresa ALTER COLUMN plano SET DEFAULT 'mensal'")
                except Exception:
                    pass

            # normaliza registros antigos
            conn.execute(text("UPDATE empresa SET plano='mensal' WHERE plano IS NULL OR plano=''"))

        app.logger.info("[BOOT] coluna empresa.plano OK")
    except Exception as e:
        app.logger.warning(f"[BOOT] erro garantindo empresa.plano: {e}")

def _ensure_release_billing_schema():
    """Fail closed until DBA applies release V1 migration in an isolated DB."""
    cols = {c['name'] for c in inspect(db.engine).get_columns('empresa')}
    if 'assinatura_ate' not in cols:
        raise RuntimeError('Migração release V1 não aplicada: empresa.assinatura_ate')
    for table in ('billing_intent', 'billing_payment'):
        if not inspect(db.engine).has_table(table):
            raise RuntimeError(f'Migração release V1 não aplicada: {table}')

def _run_bootstrap_once():
    """Cria tabelas/migrações leves quando o DB está UP; caso contrário, adia."""
    global _BOOTSTRAP_DONE
    if _BOOTSTRAP_DONE:
        return

    if not _db_is_up():
        app.logger.error("[BOOT] adiado: DB indisponível")
        return

    # sempre comece com uma sessão limpa
    try:
        db.session.rollback()
    except Exception:
        pass

    try:
        # 1) cria tabelas base
        if not _env_bool('ACHETECE_RELEASE_SCHEMA_READY', False):
            # Antes de configurar a release: nao iniciar migrations financeiras.
            # O bootstrap legado continua para compatibilidade de paginas publicas.
            db.metadata.create_all(bind=db.engine,
                tables=[table for table in db.metadata.sorted_tables
                        if table.name not in ('billing_intent', 'billing_payment')])
        else:
            _ensure_release_billing_schema()
        _ensure_empresa_plano_column()

        # 2) GARANTE colunas críticas ANTES de qualquer query em Empresa
        _ensure_pagamento_cols()
        _boot_ensure_empresa_plano_column()
        _ensure_empresa_address_columns()
        _ensure_empresa_foto_column()
        _ensure_teares_pistas_cols()

        # 3) auth + vinculação user_id (pode fazer SELECT minimalista)
        _ensure_auth_layer_and_link()

        # 4) tabela de perfil de cliente
        _ensure_cliente_profile_table()

        _BOOTSTRAP_DONE = True
        app.logger.info("[BOOT] Migrações/ajustes executados.")
    except Exception as e:
        db.session.rollback()
        app.logger.error("[BOOT] adiado: %s", e)

    try:
        TrainingProgress.__table__.create(bind=db.engine, checkfirst=True)
    except Exception as e:
        app.logger.warning(f"[BOOT] create training_progress failed: {e}")

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

@app.after_request
def _no_cache_on_panel(resp):
    """
    Evita que o navegador exiba versão em cache do painel após trocar a foto.
    Não mexe em estáticos; atua só nas páginas/redirects do painel.
    """
    try:
        # mais robusto: usa o endpoint quando disponível
        ep = (request.endpoint or "").lower()
        p  = request.path or "/"

        # páginas do painel (ajuste a lista se seu endpoint tiver outro nome)
        panel_endpoints = {""}
        # também forçamos no-store no POST de upload (o response é um redirect 302)
        upload_endpoints = {"perfil_foto_upload"}

        if ep in panel_endpoints or ep in upload_endpoints or p.endswith("/"):
            resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
            resp.headers["Pragma"] = "no-cache"
            resp.headers["Expires"] = "0"
    except Exception:
        pass
    return resp

# =====================[ ANALYTICS - FIM ]=====================

def parse_bool(val):
    """Normaliza valores vindos de checkbox/select para True/False."""
    if isinstance(val, bool):
        return val
    if val is None:
        return False
    s = str(val).strip().lower()
    return s in {'1','true','t','on','sim','s','yes','y'}

def _foto_url_runtime(empresa_id: int | None):
    """
    Devolve a URL da foto da empresa com base em arquivos na pasta static/avatars.
    Não depende de coluna no banco. Se não houver arquivo, retorna None.
    """
    if not empresa_id:
        return None

    try:
        base_name = f"empresa_{empresa_id}"
        avatars_dir = os.path.join(app.static_folder, "avatars")

        # verifica se existe algum arquivo empresa_<id>.ext
        for ext in (".jpg", ".jpeg", ".png", ".webp"):
            rel_path = f"avatars/{base_name}{ext}"
            abs_path = os.path.join(app.static_folder, rel_path)
            if os.path.exists(abs_path):
                # encontrou o arquivo: monta a URL pública
                return url_for("static", filename=rel_path)
    except Exception as e:
        app.logger.warning(f"[avatar] _foto_url_runtime erro: {e}")

    # nenhum arquivo encontrado -> deixa o template usar o avatar padrão
    return None

@app.context_processor
def inject_avatar_url():
    """
    Disponibiliza `avatar_url` em TODOS os templates.
    Prioridade:
      1) session['avatar_url'] (com ?v=timestamp)
      2) Empresa: foto_url / logo_url
      3) Arquivo legado emp_{empresa_id}.ext (_foto_url_runtime)
      4) current_user: avatar_url / photo_url (flask_login)
    Adiciona cache-buster quando for /static e não houver ?v.
    """
    url = session.get('avatar_url')  # 1) sessão já tem ?v

    if not url:
        # 2) Empresa do usuário
        emp = None
        try:
            emp, _u = _get_empresa_usuario_da_sessao()
        except Exception:
            emp = None

        if emp:
            for attr in ('foto_url', 'logo_url'):
                val = getattr(emp, attr, None)
                if val:
                    url = val
                    break

            # 3) Fallback legado (arquivo emp_{id}.*)
            if not url:
                try:
                    url = _foto_url_runtime(emp.id)
                except Exception:
                    url = None

        # 4) current_user (se usar flask_login)
        if not url:
            cu = globals().get('current_user')
            if cu is not None:
                url = getattr(cu, 'avatar_url', None) or getattr(cu, 'photo_url', None)

    # Cache-buster para arquivos locais sem querystring
    if url and url.startswith('/static/') and ('?' not in url):
        try:
            fs_path = os.path.join(app.root_path, url.lstrip('/'))
            url = f"{url}?v={int(os.path.getmtime(fs_path))}"
        except Exception:
            pass

    return {'avatar_url': url}

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
@csrf.exempt
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

        q_base = Tear.query.join(Empresa, Tear.empresa_id == Empresa.id)
        # Se a coluna 'ativo' não existir, ignora silenciosamente
        try:
            q_base = q_base.filter(Tear.ativo.is_(True))
        except Exception:
            pass

        # ED01: filtro OBRIGATÓRIO, sem fallback permissivo.
        # Em caso de falha de DB/SQL, o tratamento da rota não publica resultados.
        q_base = q_base.filter(Empresa.assinatura_ativa)

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

# --- OTP / E-mail helpers (força HTML) --------------------------------------
import random
from datetime import datetime, timedelta
from flask import current_app, session

def _email_send_html_first(to_email: str, subject: str, text: str, html: str | None) -> bool:
    """
    Envia priorizando HTML:
      1) Flask-Mail (via current_app.extensions['mail'] ou 'mail' global);
      2) SMTP multipart/alternative (env vars);
      3) Helpers do projeto (último recurso; podem degradar para texto).
    """
    # 1) Flask-Mail via registry (funciona mesmo sem 'mail' global)
    try:
        mail_ext = (getattr(current_app, "extensions", {}) or {}).get("mail")
        if (mail_ext or "mail" in globals()) and html:
            from flask_mail import Message
            sender = current_app.config.get("MAIL_DEFAULT_SENDER")
            msg = Message(subject=subject, recipients=[to_email], sender=sender)
            msg.body = text or ""
            msg.html = html
            msg.extra_headers = {"Content-Language": "pt-BR"}
            (mail_ext or mail).send(msg)  # type: ignore[name-defined]
            current_app.logger.info("[MAIL_PATH] flask-mail-html")
            return True
    except Exception:
        current_app.logger.exception("[MAIL] Flask-Mail falhou")

    # 2) SMTP multipart/alternative
    try:
        import os, smtplib, ssl
        from email.mime.multipart import MIMEMultipart
        from email.mime.text import MIMEText

        host = os.environ.get("SMTP_HOST")
        port = int(os.environ.get("SMTP_PORT", "587"))
        user = os.environ.get("SMTP_USER")
        pwd  = os.environ.get("SMTP_PASS")
        sender = os.environ.get("SMTP_SENDER") or current_app.config.get("MAIL_DEFAULT_SENDER") or "no-reply@achetece.com.br"
        use_tls = os.environ.get("SMTP_TLS", "1") not in ("0","false","False")

        if host and sender and os.environ.get("ALLOW_SMTP", "0").lower() in ("1","true","yes"):
            msg = MIMEMultipart("alternative")
            msg["Subject"] = subject
            msg["From"] = sender
            msg["To"] = to_email
            msg["Content-Language"] = "pt-BR"
            msg.attach(MIMEText(text or "", "plain", "utf-8"))
            if html:
                msg.attach(MIMEText(html, "html", "utf-8"))

            with smtplib.SMTP(host, port, timeout=20) as s:
                if use_tls: s.starttls(context=ssl.create_default_context())
                if user:    s.login(user, pwd or "")
                s.sendmail(sender, [to_email], msg.as_string())
            current_app.logger.info("[MAIL_PATH] smtp-html")
            return True
    except Exception:
        current_app.logger.exception("[MAIL] SMTP falhou")

    # 3) Helpers do projeto (agora checando retorno!)
    try:
        for fname in ("send_email", "enviar_email", "mail_send", "send_mail"):
            if fname in globals():
                f = globals()[fname]
                # use kwargs corretos:
                res = f(to=to_email, subject=subject, html=html, text=text)
    
                ok = True
                if isinstance(res, bool):
                    ok = res
                elif res is None:
                    ok = True  # muitos helpers não retornam nada; consideramos OK
                else:
                    # se retornar tupla (ok, msg) etc.
                    try:
                        ok = bool(res[0])
                    except Exception:
                        ok = True
    
                if ok:
                    current_app.logger.info(f"[MAIL_PATH] helper:{fname} (html enviado)")
                    return True
                else:
                    current_app.logger.warning(f"[MAIL_PATH] helper:{fname} retornou False")
    
    except Exception:
        current_app.logger.exception("[MAIL] helper falhou")

def _otp_email_html(dest_email: str, code: str, minutes: int = 30) -> str:
    brand = "AcheTece • Portal de Malharias"
    primary = "#4B2AC7"
    chip_bg = "#F5F0FF"
    chip_bd = "#D9CCFF"
    text = (
        f"Seu código para acessar a sua conta\n\n"
        f"Recebemos uma solicitação de acesso ao AcheTece para: {dest_email}\n\n"
        f"{code}\n\n"
        f"Código válido por {minutes} minutos e de uso único.\n"
        f"Se você não fez esta solicitação, ignore este e-mail.\n\n{brand}"
    )
    return f"""<!doctype html>
<html lang="pt-br">
<head>
  <meta http-equiv="Content-Type" content="text/html; charset=UTF-8" />
  <meta name="color-scheme" content="light only">
  <meta name="supported-color-schemes" content="light">
  <title>Código de acesso</title>
  <style>@media screen {{ .code-chip {{ letter-spacing: 6px; }} }}</style>
</head>
<body style="margin:0;padding:0;background:#F7F7FA;">
  <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="background:#F7F7FA;">
    <tr>
      <td align="center" style="padding:24px 12px;">
        <table role="presentation" cellpadding="0" cellspacing="0" width="100%" style="max-width:640px;background:#FFFFFF;border:1px solid #EEE;border-radius:12px;">
          <tr>
            <td style="padding:24px 24px 8px 24px;font-family:Inter,Segoe UI,Arial,Helvetica,sans-serif;">
              <h1 style="margin:0 0 6px 0;font-size:22px;line-height:1.3;color:#1E1B2B;">Seu código para acessar a sua conta</h1>
              <p style="margin:0 0 14px 0;color:#444;font-size:14px;">
                Recebemos uma solicitação de acesso ao AcheTece para:<br>
                <a href="mailto:{dest_email}" style="color:#1E3A8A;text-decoration:underline;">{dest_email}</a>
              </p>
            </td>
          </tr>
          <tr>
            <td align="center" style="padding:6px 24px 2px 24px;">
              <div style="display:inline-block;padding:16px 28px;border-radius:14px;background:{chip_bg};border:2px dotted {chip_bd};">
                <div class="code-chip" style="font-family:Inter,Segoe UI,Arial,Helvetica,sans-serif;font-size:36px;font-weight:800;color:{primary};letter-spacing:6px;">{code}</div>
              </div>
            </td>
          </tr>
          <tr>
            <td style="padding:14px 24px 20px 24px;font-family:Inter,Segoe UI,Arial,Helvetica,sans-serif;color:#555;">
              <p style="margin:0 0 8px 0;font-size:14px;">Código válido por <strong>{minutes} minutos</strong> e de uso único.</p>
              <p style="margin:0 0 2px 0;font-size:13px;color:#666;">Se você não fez esta solicitação, ignore este e-mail.</p>
            </td>
          </tr>
          <tr>
            <td style="padding:10px 24px 22px 24px;">
              <hr style="border:none;border-top:1px solid #EEE;margin:4px 0 12px 0;">
              <p style="margin:0;color:#777;font-family:Inter,Segoe UI,Arial,Helvetica,sans-serif;font-size:12px;">{brand}</p>
            </td>
          </tr>
        </table>
        <div style="display:none;max-height:0;overflow:hidden;color:transparent;">{text}</div>
      </td>
    </tr>
  </table>
</body>
</html>"""

def _otp_send(to_email: str, ip: str = "", ua: str = ""):
    """Gera OTP, salva expiração e envia e-mail HTML (30 min)."""
    try:
        code = f"{random.randint(0, 999999):06d}"
        minutes = 30

        data = session.get("otp_login", {})
        data[to_email] = {
            "code": code,
            "exp": (datetime.utcnow() + timedelta(minutes=minutes)).timestamp(),
            "ip": ip[:64],
            "ua": ua[:255],
            "attempts": 0,
        }
        session["otp_login"] = data

        subject = "Seu código de acesso – AcheTece"
        text    = f"Seu código é {code}. Ele expira em {minutes} minutos."
        html    = _otp_email_html(to_email, code, minutes)

        if _email_send_html_first(to_email, subject, text, html):
            current_app.logger.info("[OTP] HTML enviado com sucesso")
            return True, "Enviamos um código para o seu e-mail."
        else:
            current_app.logger.error("[OTP] Falha ao enviar HTML (nenhum backend aceitou)")
            return False, "Não foi possível enviar o código agora. Tente novamente."
    except Exception:
        current_app.logger.exception("Falha ao enviar OTP de login")
        return False, "Não foi possível enviar o código agora. Tente novamente."

# Mantém seu _otp_validate como estava (com guard ou não, tanto faz)
# ----------------------------------------------------------------------

# /login
@app.route("/login", methods=["GET", "POST"], endpoint="login")
@limiter.limit("10 per minute", methods=["POST"])
def view_login():
    if request.method == "GET":
        email = (request.args.get("email") or "").strip().lower()
        return _render_try(["login.html", "AcheTece/Modelos/login.html"], email=email)

    # POST (clicou Continuar)
    email = (request.form.get("email") or request.args.get("email") or "").strip().lower()
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
    email = (request.form.get("email") or request.args.get("email") or "").strip().lower()
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
    email = (request.form.get("email") or request.args.get("email") or "").strip().lower()
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
@app.post("/login/codigo/validar")
def validate_login_code():
    email = (request.form.get("email") or request.args.get("email") or "").strip().lower()
    codigo = (request.form.get("codigo") or request.form.get("code") or "").strip()

    ok, msg = _otp_validate(email, codigo)
    if not ok:
        flash(msg, "danger")
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
    email = (request.form.get("email") or request.args.get("email") or "").strip().lower()
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

    # ED01: login privado independente do pagamento. A vigencia controla
    # exclusivamente a exposicao publica da malharia e de seus teares.
    session["empresa_id"] = user.id
    session["empresa_apelido"] = user.apelido or user.nome or user.email.split("@")[0]
    return redirect(url_for("painel_malharia"))

from flask import request, session, redirect, url_for, flash

@app.get("/oauth/google")
def oauth_google():
    # contexto padrão "empresa" e preserva redirecionamento
    ctx = request.args.get("ctx", "empresa")
    nxt = request.args.get("next") or url_for("painel_malharia")

    # guarda em sessão para usar no callback
    session["oauth_ctx"] = ctx
    session["oauth_next"] = nxt

    if not GOOGLE_CLIENT_ID or not GOOGLE_CLIENT_SECRET:
        # Só bloqueia se realmente faltar credencial
        return "Login com Google está desabilitado no momento (credenciais ausentes).", 503

    redirect_uri = url_for("oauth_google_callback", _external=True, _scheme="https")
    ua = (request.user_agent.string or "").lower()
    # detecta navegadores móveis do iOS/Android
    _mobile_markers = ("iphone","ipad","ipod","android","crios","fxios","edgios","safari")
    is_mobile = any(m in ua for m in _mobile_markers)
    
    # permite forçar via querystring também (?force_login=1)
    force_login = request.args.get("force_login") == "1"
    
    prompt = "login" if (is_mobile or force_login) else "select_account"
    
    return oauth.google.authorize_redirect(
        redirect_uri,
        prompt=prompt,       # usa o valor calculado acima (login | select_account)
        max_age=0,
        hl="pt-BR"
    )

@app.get("/oauth/google/callback")
def oauth_google_callback():
    try:
        token = oauth.google.authorize_access_token()
        # Em vez de parsear o id_token (que exige nonce), use o /userinfo:
        userinfo = oauth.google.get("https://openidconnect.googleapis.com/v1/userinfo").json()
    except Exception as e:
        current_app.logger.exception(f"Falha no callback do Google: {e}")
        flash("Não foi possível concluir o login com o Google.", "danger")
        return redirect(url_for("login"))

    email = (userinfo.get("email") or "").strip().lower()
    nome  = userinfo.get("name") or ""
    foto  = userinfo.get("picture")

    ctx = session.pop("oauth_ctx", "empresa")
    nxt = session.pop("oauth_next", None) or url_for("painel_malharia")

    if not email:
        flash("Não foi possível obter o e-mail do Google.", "danger")
        return redirect(url_for("login"))

    # Cadastro de cliente: identidade confirmada pelo Google OAuth.
    # Preserva integralmente o fluxo existente para contas de malharia.
    if ctx == "cliente":
        if userinfo.get("email_verified") is not True:
            flash("Não foi possível confirmar o e-mail da sua conta Google.", "error")
            return redirect(url_for("index"))
        emp_existente = Empresa.query.filter(func.lower(Empresa.email) == email).first()
        if emp_existente:
            flash("Este e-mail já pertence a uma malharia. Acesse sua conta de malharia.", "info")
            return redirect(url_for("login", email=email))
        usuario = Usuario.query.filter(func.lower(Usuario.email) == email).first()
        if usuario and (usuario.role not in (None, "cliente") or Empresa.query.filter_by(user_id=usuario.id).first()):
            flash("Este e-mail já está vinculado a outro tipo de conta.", "warning")
            return redirect(url_for("login"))
        if not usuario:
            usuario = Usuario(email=email, google_id=str(userinfo.get("sub") or "") or None,
                              role="cliente", is_active=True)
            db.session.add(usuario)
            db.session.flush()
        else:
            if usuario.is_active is False:
                abort(403)
            if usuario.google_id and usuario.google_id != str(userinfo.get("sub") or ""):
                abort(403)
            usuario.google_id = str(userinfo.get("sub") or "") or usuario.google_id
            usuario.role = "cliente"
        db.session.commit()
        session.clear()
        session["cliente_user_id"] = usuario.id
        session["cliente_nome_google"] = nome
        return redirect(url_for("criar_conta_cliente"))

    # ===== seu login existente (mesmo fluxo do login por e-mail) =====
    try:
        emp = Empresa.query.filter_by(email=email).first()
    except Exception:
        emp = None

    if not emp:
        flash("Não encontramos uma conta para este e-mail. Faça o cadastro para continuar.", "warning")
        return redirect(url_for("cadastrar_empresa", email=email))

    session.clear()
    session["empresa_id"] = emp.id
    session["empresa_nome"] = getattr(emp, "nome", getattr(emp, "razao_social", ""))
    if foto:
        session["avatar_url"] = foto

    return redirect(nxt)

# Cadastro de cliente: acesso somente após confirmação de identidade Google.
@app.route("/criar_conta_cliente", methods=["GET", "POST"], endpoint="criar_conta_cliente")
def criar_conta_cliente():
    uid = session.get("cliente_user_id")
    if not uid:
        if request.method == "POST":
            abort(403)
        return redirect(url_for("oauth_google", ctx="cliente"))

    usuario = db.session.get(Usuario, uid)
    if not usuario or usuario.role != "cliente" or usuario.is_active is False:
        session.pop("cliente_user_id", None)
        abort(403)
    if Empresa.query.filter(func.lower(Empresa.email) == usuario.email.lower()).first():
        abort(403)

    estados = ['AC','AL','AM','AP','BA','CE','DF','ES','GO','MA','MG','MS','MT',
               'PA','PB','PE','PI','PR','RJ','RN','RO','RR','RS','SC','SE','SP','TO']
    perfil = ClienteProfile.query.filter_by(user_id=usuario.id).first()
    if not session.get("cliente_csrf"):
        session["cliente_csrf"] = secrets.token_urlsafe(32)

    dados = {
        "nome": (perfil.nome if perfil else None) or session.get("cliente_nome_google") or "",
        "empresa": (perfil.empresa if perfil else None) or "",
        "whatsapp": (perfil.whatsapp if perfil else None) or "",
        "cidade": (perfil.cidade if perfil else None) or "",
        "estado": (perfil.estado if perfil else None) or "",
    }
    erros = {}
    if request.method == "POST":
        if not secrets.compare_digest(request.form.get("csrf_token", ""), session["cliente_csrf"]):
            abort(400)
        dados = {k: (request.form.get(k) or "").strip() for k in dados}
        if len(dados["nome"]) < 2 or len(dados["nome"]) > 120:
            erros["nome"] = "Informe seu nome (até 120 caracteres)."
        if len(dados["empresa"]) > 160:
            erros["empresa"] = "Informe até 160 caracteres."
        if dados["estado"] and dados["estado"] not in estados:
            erros["estado"] = "UF inválida."
        if len(dados["cidade"]) > 100:
            erros["cidade"] = "Informe até 100 caracteres."
        whatsapp = re.sub(r"\D", "", dados["whatsapp"])
        if whatsapp and not (10 <= len(whatsapp) <= 13):
            erros["whatsapp"] = "Informe o WhatsApp com DDD."
        if not erros:
            if not perfil:
                perfil = ClienteProfile(user_id=usuario.id)
                db.session.add(perfil)
            perfil.nome = dados["nome"]
            perfil.empresa = dados["empresa"] or None
            perfil.estado = dados["estado"] or None
            perfil.cidade = dados["cidade"] or None
            perfil.whatsapp = whatsapp or None
            db.session.commit()
            session["cliente_csrf"] = secrets.token_urlsafe(32)
            flash("Dados de cliente salvos com sucesso.", "success")
            return redirect(url_for("index"))

    return render_template("criar_conta_cliente.html", estados=estados, erros=erros,
                           csrf_token=session["cliente_csrf"], **dados)


@app.route("/logout")
def logout():
    session.pop("cliente_user_id", None)
    session.pop("cliente_csrf", None)
    session.pop("empresa_id", None)
    session.pop("empresa_apelido", None)
    return redirect(url_for('login'))  # <- agora vai para a tela de login

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

from flask import make_response

# --------------------------------------------------------------------
# AcheTece 2.0 - Minhas Oportunidades da Malharia
# --------------------------------------------------------------------

@app.get(
    "/malharia/oportunidades",
    endpoint="minhas_oportunidades"
)
def minhas_oportunidades():

    # --------------------------------------------------------------
    # Autenticação da malharia
    # --------------------------------------------------------------

    empresa_id = session.get(
        "empresa_id"
    )

    if not empresa_id:

        return redirect(
            url_for("login")
        )

    try:

        empresa = db.session.get(
            Empresa,
            int(empresa_id)
        )

    except Exception:

        empresa = None

    if not empresa:

        session.clear()

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Oportunidades pertencentes SOMENTE a esta malharia
    # --------------------------------------------------------------

    oportunidades = (
        Opportunity.query
        .filter(
            Opportunity.empresa_id
            == empresa.id,
            Opportunity.status
            != "inativa"
        )
        .order_by(
            Opportunity.created_at.desc(),
            Opportunity.best_score.desc()
        )
        .all()
    )

    # --------------------------------------------------------------
    # Indicadores
    # --------------------------------------------------------------

    total_oportunidades = len(
        oportunidades
    )

    total_novas = sum(
        1
        for oportunidade
        in oportunidades
        if oportunidade.status == "nova"
    )

    total_interessadas = sum(
        1
        for oportunidade
        in oportunidades
        if oportunidade.status == "interessada"
    )

    total_recusadas = sum(
        1
        for oportunidade
        in oportunidades
        if oportunidade.status == "recusada"
    )

    return render_template(
        "minhas_oportunidades.html",
        empresa=empresa,
        oportunidades=oportunidades,
        total_oportunidades=total_oportunidades,
        total_novas=total_novas,
        total_interessadas=total_interessadas,
        total_recusadas=total_recusadas,
    )

# --- Rota do Painel (vencimento por plano + ajuste p/ próximo dia útil BR) ---
@app.route('/painel_malharia', endpoint="painel_malharia")
def painel_malharia():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for('login'))

    # Evita objetos “velhos” ficarem presos na identity map
    try:
        db.session.expire_all()
    except Exception:
        pass

    # ✅ Recarrega "fresco" após expire_all (garante status/plan/data atualizados)
    try:
        emp_id = emp.id
        emp = Empresa.query.get(emp_id)
        if not emp:
            return redirect(url_for("login"))
    except Exception:
        pass

    step = request.args.get("step") or _proximo_step(emp)

    # Reconsulta FRESCA os teares e ordena (mais recente primeiro)
    teares = (
        Tear.query
            .filter_by(empresa_id=emp.id)
            .order_by(Tear.id.desc())
            .all()
    )

    # ED01: painel e resultados públicos usam o mesmo status/validade.
    # Exibir o painel jamais altera status ou data de pagamento no banco.
    is_ativa = bool(emp.assinatura_ativa)
    vencimento_proximo = emp.assinatura_expira_em.date() if emp.assinatura_expira_em else None
    hoje = datetime.utcnow().date()
    dias_restantes = ((vencimento_proximo - hoje).days
                      if vencimento_proximo is not None else None)

    # ✅ CTA de pagamento no painel (pendente/vencida ou vencendo em breve)
    # Você pode usar isso no template para mostrar o banner/botões.
    mostrar_pagamento = (not is_ativa)
    if (is_ativa is True) and (dias_restantes is not None) and (dias_restantes <= 7):
        mostrar_pagamento = True

    checklist = {
        "perfil_ok": all(_empresa_basica_completa(emp)),
        "teares_ok": _conta_teares(emp.id) > 0,
        "plano_ok": is_ativa or DEMO_MODE,  # <--- aqui é "or"
        "step": step,
    }

    # Notificações / chat (mantidos)
    notif_count, notif_lista = _get_notificacoes(emp.id)
    chat_nao_lidos = 0  # ajuste aqui se tiver chat real

    # Foto: resolve sempre via helper (banco + arquivos)
    foto_url = _empresa_avatar_url(emp)

    app.logger.info({
        "rota": "painel_malharia",
        "empresa_id": emp.id,
        "status_pagamento": getattr(emp, "status_pagamento", None),
        "plano": getattr(emp, "plano", None),
        "vencimento_proximo": str(vencimento_proximo) if vencimento_proximo else None,
        "dias_restantes": dias_restantes,
        "assinatura_ativa": is_ativa,
        "foto_url_resolvida": foto_url,
    })

    # Render + evita cache para ver dados sempre atualizados
    resp = make_response(render_template(
        "painel_malharia.html",
        empresa=emp,
        teares=teares,
        assinatura_ativa=is_ativa,
        checklist=checklist,
        step=step,
        notificacoes=notif_count,
        notificacoes_lista=notif_lista,
        chat_nao_lidos=chat_nao_lidos,
        foto_url=foto_url,

        # ✅ dados de vencimento (para chip)
        vencimento_proximo=vencimento_proximo,
        dias_restantes=dias_restantes,

        # ✅ opcional (para banner/botões de pagar/renovar)
        mostrar_pagamento=mostrar_pagamento,
        preco_mensal=_release_price_cents('mensal') / 100 if os.getenv('ACHETECE_PRICE_MONTHLY_BRL') else 47,
        preco_anual=_release_price_cents('anual') / 100 if os.getenv('ACHETECE_PRICE_YEARLY_BRL') else 479.40,
        desconto_percentual=round((1 - (_release_price_cents('anual') / (12 * _release_price_cents('mensal')))) * 100) if (os.getenv('ACHETECE_PRICE_MONTHLY_BRL') and os.getenv('ACHETECE_PRICE_YEARLY_BRL')) else 15,
        pode_cancelar_recorrencia=bool(BillingIntent.query.filter_by(empresa_id=emp.id, plano='mensal', status='active', cancel_at_period_end=False).first()) if _env_bool('ACHETECE_RELEASE_SCHEMA_READY', False) else False,
    ))
    resp.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    resp.headers["Pragma"] = "no-cache"
    resp.headers["Expires"] = "0"
    return resp


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
    if ref:
        try:
            rp = urlparse(ref)
            if rp.netloc == request.host:
                if "painel" in rp.path or "malharia" in rp.path:
                    return redirect(_with_cb(ref, ts))
        except Exception:
            pass
    return redirect(url_for('painel_malharia', _cb=ts))

def _empresa_avatar_url(emp) -> str | None:
    """
    Resolve a URL de foto para a empresa.

    Ordem:
    1) Se emp.foto_url estiver preenchido, usa.
    2) Procura arquivo físico nas pastas usuais:
       - static/uploads/avatars/empresa_<id>.(jpg|jpeg|png|webp)
       - static/uploads/perfil/emp_<id>.(jpg|jpeg|png|webp)
    3) Se achar, monta a URL, grava em emp.foto_url e commit.
    4) Caso nada exista, retorna None (template mostra avatar padrão).
    """
    if not emp:
        return None

    # 1) Já tem foto gravada no banco
    url = getattr(emp, "foto_url", None)
    if url:
        return url

    # 2) Procura arquivos físicos (compat com seus diretórios)
    try:
        candidates = [
            (f"uploads/avatars/empresa_{emp.id}", (".webp", ".jpg", ".jpeg", ".png")),
            (f"uploads/perfil/emp_{emp.id}",      (".webp", ".jpg", ".jpeg", ".png")),
        ]

        for base_rel, exts in candidates:
            for ext in exts:
                rel_path = f"{base_rel}{ext}"
                abs_path = os.path.join(app.static_folder, rel_path)
                if os.path.exists(abs_path):
                    url = url_for("static", filename=rel_path)

                    # grava no banco para próximas vezes
                    try:
                        emp.foto_url = url
                        db.session.commit()
                    except Exception:
                        db.session.rollback()

                    return url

    except Exception as e:
        app.logger.warning(f"[avatar] _empresa_avatar_url erro: {e}")

    return None

from flask import send_from_directory

TRAINING_FILES_DIR = os.path.join(app.root_path, "training_files")


def _training_progress_map(company_id: int) -> dict:
    """
    Retorna dict:
      {(module_key, lesson_key): {"status":..., "score":..., "completed_at":...}}
    """
    mp = {}
    try:
        rows = TrainingProgress.query.filter_by(company_id=company_id).all()
        for r in rows:
            mp[(r.module_key, r.lesson_key)] = {
                "status": r.status,
                "score": r.score,
                "completed_at": r.completed_at,
                "updated_at": r.updated_at,
            }
    except Exception:
        pass
    return mp


def _training_upsert(company_id: int, module_key: str, lesson_key: str, status: str, score: int | None = None):
    module_key = (module_key or "").strip().lower()
    lesson_key = (lesson_key or "").strip().lower()
    status = (status or "not_started").strip().lower()

    if status not in ("not_started", "in_progress", "done"):
        status = "in_progress"

    row = TrainingProgress.query.filter_by(
        company_id=company_id, module_key=module_key, lesson_key=lesson_key
    ).first()

    now = datetime.utcnow()

    if not row:
        row = TrainingProgress(
            company_id=company_id,
            module_key=module_key,
            lesson_key=lesson_key,
            status=status,
            score=score,
            updated_at=now,
            completed_at=(now if status == "done" else None),
        )
        db.session.add(row)
    else:
        row.status = status
        if score is not None:
            row.score = score
        row.updated_at = now
        if status == "done" and not row.completed_at:
            row.completed_at = now

    db.session.commit()


def _training_percent_for_module(module: dict, progress_map: dict) -> int:
    lessons = module.get("lessons") or []
    if not lessons:
        return 0
    done = 0
    for a in lessons:
        k = (module.get("key"), a.get("key"))
        st = (progress_map.get(k) or {}).get("status")
        if st == "done":
            done += 1
    return int(round((done / max(1, len(lessons))) * 100))


def _training_global_percent(progress_map: dict) -> int:
    total = 0
    done = 0
    for m in TRAINING_CATALOG:
        for a in (m.get("lessons") or []):
            total += 1
            st = (progress_map.get((m.get("key"), a.get("key"))) or {}).get("status")
            if st == "done":
                done += 1
    if total == 0:
        return 0
    return int(round((done / total) * 100))


# -----------------------------
# Arquivos do treinamento (PROTEGIDOS)
# -----------------------------
@app.get("/painel/treinamento/arquivo/<path:filename>", endpoint="treinamento_file")
def treinamento_file(filename):
    emp, _u = _get_empresa_usuario_da_sessao()
    if not emp:
        return redirect(url_for("login"))

    # (opcional) se quiser travar por assinatura ativa, troque para:
    # if not emp.assinatura_ativa and not DEMO_MODE: return redirect(url_for("painel_malharia"))

    safe_name = os.path.basename(filename)  # evita path traversal
    abs_path = os.path.join(TRAINING_FILES_DIR, safe_name)
    if not os.path.exists(abs_path):
        abort(404)

    return send_from_directory(TRAINING_FILES_DIR, safe_name, as_attachment=False)


# -----------------------------
# Home do Treinamento
# -----------------------------
@app.get("/painel/treinamento", endpoint="treinamento_home")
def treinamento_home():
    emp, _u = _get_empresa_usuario_da_sessao()
    if not emp:
        return redirect(url_for("login"))

    progress = _training_progress_map(emp.id)
    modules_view = []
    for m in TRAINING_CATALOG:
        modules_view.append({
            "key": m.get("key"),
            "title": m.get("title"),
            "desc": m.get("desc"),
            "percent": _training_percent_for_module(m, progress),
            "lessons_count": len(m.get("lessons") or []),
        })

    return render_template(
        "treinamento_home.html",
        empresa=emp,
        modules=modules_view,
        global_percent=_training_global_percent(progress),
    )


ALLOWED_MATERIALS = ("apostila", "apresentacao")

def _lesson_files(aula: dict) -> dict:
    """
    Normaliza a estrutura de arquivos da aula:
    - Novo padrão: aula["files"] = {"apostila": "...", "apresentacao": "..."}
    - Legado: aula["file"] = "....pdf" -> vira {"apostila": "....pdf"}
    """
    if not aula:
        return {}

    files = aula.get("files")
    if isinstance(files, dict) and files:
        # filtra apenas chaves permitidas e com nome de arquivo válido
        cleaned = {}
        for k in ALLOWED_MATERIALS:
            v = files.get(k)
            if isinstance(v, str) and v.strip():
                cleaned[k] = v.strip()
        return cleaned

    legacy = aula.get("file")
    if isinstance(legacy, str) and legacy.strip():
        return {"apostila": legacy.strip()}

    return {}

ALLOWED_MATERIALS = ("apostila", "apresentacao")

def _lesson_files(aula: dict) -> dict:
    """
    Normaliza a estrutura de arquivos da aula:
    - Novo padrão: aula["files"] = {"apostila": "...", "apresentacao": "..."}
    - Legado: aula["file"] = "....pdf" -> vira {"apostila": "....pdf"}
    """
    if not aula:
        return {}

    files = aula.get("files")
    if isinstance(files, dict) and files:
        # filtra apenas chaves permitidas e com nome de arquivo válido
        cleaned = {}
        for k in ALLOWED_MATERIALS:
            v = files.get(k)
            if isinstance(v, str) and v.strip():
                cleaned[k] = v.strip()
        return cleaned

    legacy = aula.get("file")
    if isinstance(legacy, str) and legacy.strip():
        return {"apostila": legacy.strip()}

    return {}

# -----------------------------
# Página do Módulo (lista de aulas)
# -----------------------------
@app.get("/painel/treinamento/<module_key>", endpoint="treinamento_modulo")
def treinamento_modulo(module_key):
    emp, _u = _get_empresa_usuario_da_sessao()
    if not emp:
        return redirect(url_for("login"))

    mod = get_module(module_key)
    if not mod:
        abort(404)

    progress = _training_progress_map(emp.id)

    lessons_view = []
    for a in (mod.get("lessons") or []):
        st = (progress.get((mod.get("key"), a.get("key"))) or {}).get("status") or "not_started"
        lessons_view.append({
            "key": a.get("key"),
            "title": a.get("title"),
            "minutes": a.get("minutes"),
            "summary": a.get("summary"),
            "status": st,
        })

    return render_template(
        "treinamento_modulo.html",
        empresa=emp,
        module=mod,
        lessons=lessons_view,
        percent=_training_percent_for_module(mod, progress),
    )


# -----------------------------
# Página da Aula (portal + PDF + quiz + marcar concluída)
# -----------------------------
@app.get("/painel/treinamento/<module_key>/<lesson_key>", endpoint="treinamento_aula")
def treinamento_aula(module_key, lesson_key):
    emp, _u = _get_empresa_usuario_da_sessao()
    if not emp:
        return redirect(url_for("login"))

    mod = get_module(module_key)
    aula = get_lesson(module_key, lesson_key)
    if not mod or not aula:
        abort(404)

    progress = _training_progress_map(emp.id)
    st = (progress.get((mod.get("key"), aula.get("key"))) or {}).get("status") or "not_started"

    # Ao entrar na aula, marca como "in_progress" se ainda não iniciou
    # (exceto quando acabamos de desmarcar e voltamos via redirect)
    skip_autostart = session.pop("_skip_autostart", False)

    if st == "not_started" and not skip_autostart:
        try:
            _training_upsert(emp.id, mod.get("key"), aula.get("key"), "in_progress")
            st = "in_progress"
        except Exception:
            pass

    # ✅ NOVO: suporte a 2 arquivos via ?material=
    files = _lesson_files(aula)

    requested = (request.args.get("material") or "apostila").strip().lower()
    if requested not in ALLOWED_MATERIALS:
        requested = "apostila"

    # fallback: se pediu um material que não existe, cai para apostila ou para o primeiro disponível
    if requested not in files:
        if "apostila" in files:
            requested = "apostila"
        elif files:
            requested = next(iter(files.keys()))
        else:
            requested = "apostila"  # não tem arquivo; mantém padrão

    active_file_name = files.get(requested)
    file_url = url_for("treinamento_file", filename=active_file_name) if active_file_name else None

    # lista para o template montar botões/cards
    materials = []
    if files.get("apostila"):
        materials.append({
            "key": "apostila",
            "label": "Apostila (PDF)",
            "file_url": url_for("treinamento_file", filename=files["apostila"]),
        })
    if files.get("apresentacao"):
        materials.append({
            "key": "apresentacao",
            "label": "Apresentação (PDF)",
            "file_url": url_for("treinamento_file", filename=files["apresentacao"]),
        })

    return render_template(
        "treinamento_aula.html",
        empresa=emp,
        module=mod,
        lesson=aula,
        status=st,

        # ✅ mantém compatibilidade com seu HTML atual:
        file_url=file_url,

        # ✅ novos (para montar os 2 botões bem visíveis):
        materials=materials,
        active_material=requested,
    )
    
from datetime import datetime
from flask import current_app, abort, redirect, url_for

@app.post("/treinamento/<module_key>/<lesson_key>/concluir")
def treinamento_concluir(module_key, lesson_key):
    emp, _u = _get_empresa_usuario_da_sessao()
    if not emp:
        return redirect(url_for("login"))

    mod = get_module(module_key)
    aula = get_lesson(module_key, lesson_key)
    if not mod or not aula:
        abort(404)

    progress = _training_progress_map(emp.id)
    st = (progress.get((mod.get("key"), aula.get("key"))) or {}).get("status") or "not_started"

    # TOGGLE baseado no MESMO sistema que a tela usa
    if st == "done":
        # DESMARCAR -> volta para não iniciada
        try:
            _training_upsert(emp.id, mod.get("key"), aula.get("key"), "not_started", score=None)
        except TypeError:
            _training_upsert(emp.id, mod.get("key"), aula.get("key"), "not_started")

        # evita que o GET auto-marque in_progress ao recarregar
        session["_skip_autostart"] = True

        current_app.logger.info(f"[TOGGLE] DESMARCOU via _training_upsert: emp={emp.id} mod={mod.get('key')} aula={aula.get('key')}")
    else:
        # MARCAR -> concluída
        try:
            _training_upsert(emp.id, mod.get("key"), aula.get("key"), "done")
        except Exception:
            _training_upsert(emp.id, mod.get("key"), aula.get("key"), "done")

        current_app.logger.info(f"[TOGGLE] MARCOU via _training_upsert: emp={emp.id} mod={mod.get('key')} aula={aula.get('key')}")

    return redirect(url_for("treinamento_aula", module_key=mod.get("key"), lesson_key=aula.get("key")))

@app.post("/painel/treinamento/<module_key>/<lesson_key>/quiz", endpoint="treinamento_quiz")
def treinamento_quiz(module_key, lesson_key):
    emp, _u = _get_empresa_usuario_da_sessao()
    if not emp:
        return redirect(url_for("login"))

    mod = get_module(module_key)
    aula = get_lesson(module_key, lesson_key)
    if not mod or not aula:
        abort(404)

    quiz = aula.get("quiz") or []
    if not quiz:
        flash("Esta aula não possui quiz.", "info")
        return redirect(url_for("treinamento_aula", module_key=mod.get("key"), lesson_key=aula.get("key")))

    total = len(quiz)
    acertos = 0

    for i, q in enumerate(quiz):
        ans = q.get("answer")
        picked = request.form.get(f"q{i}")
        try:
            picked_i = int(picked) if picked is not None else -1
        except Exception:
            picked_i = -1
        if picked_i == ans:
            acertos += 1

    score = int(round((acertos / max(1, total)) * 100))
    _training_upsert(emp.id, mod.get("key"), aula.get("key"), "in_progress", score=score)

    flash(f"Quiz registrado: {score}% ✅", "success")
    return redirect(url_for("treinamento_aula", module_key=mod.get("key"), lesson_key=aula.get("key")))

@app.route("/perfil/foto_upload", methods=["POST"], endpoint="perfil_foto_upload")
def perfil_foto_upload():
    emp, u = _get_empresa_usuario_da_sessao()
    if not emp or not u:
        return redirect(url_for("login"))

    # Existem até 3 inputs <input type="file" name="foto"> (lib, cam, file).
    # Precisamos pegar o primeiro que REALMENTE tenha arquivo.
    file = None
    try:
        candidatos = request.files.getlist("foto")
    except Exception:
        candidatos = [request.files.get("foto")]

    for f in candidatos:
        if f and getattr(f, "filename", "").strip():
            file = f
            break

    if not file or not file.filename.strip():
        flash("Nenhuma foto selecionada.", "erro")
        app.logger.info({
            "rota": "perfil_foto_upload",
            "empresa_id": emp.id,
            "motivo": "sem_arquivo",
            "candidatos": [getattr(f, "filename", None) for f in candidatos],
        })
        return _back_to_panel(int(datetime.utcnow().timestamp()))

    # extensão do arquivo original
    filename_orig = secure_filename(file.filename)
    _, ext = os.path.splitext(filename_orig)
    ext = (ext or "").lower()

    # se quiser ser bem permissivo, aceita tudo como .jpg
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        # fallback: trata como .jpg mesmo assim
        ext = ".jpg"

    # Pasta alvo: static/avatars
    avatars_dir = os.path.join(app.static_folder, "avatars")
    try:
        os.makedirs(avatars_dir, exist_ok=True)
    except Exception as e:
        app.logger.error(f"[avatar] erro ao criar pasta avatars: {e}")
        flash("Erro ao preparar pasta de imagens.", "erro")
        return _back_to_panel(int(datetime.utcnow().timestamp()))

    # Nome fixo por empresa (sobrescreve qualquer anterior)
    base_name = f"empresa_{emp.id}"
    filename = base_name + ext
    filepath = os.path.join(avatars_dir, filename)

    # Remove versões antigas com outras extensões
    for old_ext in (".jpg", ".jpeg", ".png", ".webp"):
        old_path = os.path.join(avatars_dir, base_name + old_ext)
        if old_path != filepath and os.path.exists(old_path):
            try:
                os.remove(old_path)
            except OSError:
                pass

    try:
        file.save(filepath)
    except Exception as e:
        app.logger.error(f"[avatar] erro ao salvar arquivo: {e}")
        flash("Erro ao salvar a imagem enviada.", "erro")
        return _back_to_panel(int(datetime.utcnow().timestamp()))

    # Monta URL pública
    rel_path = f"avatars/{filename}"
    novo_url = url_for("static", filename=rel_path)

    # Atualiza empresa + sessão
    emp.foto_url = novo_url
    try:
        db.session.commit()
    except Exception as e:
        db.session.rollback()
        app.logger.error(f"[avatar] erro ao gravar foto_url no banco: {e}")
        flash("Erro ao salvar a imagem no cadastro.", "erro")
        return _back_to_panel(int(datetime.utcnow().timestamp()))

    session["avatar_url"] = novo_url

    app.logger.info({
        "rota": "perfil_foto_upload",
        "empresa_id": emp.id,
        "foto_url_salva": novo_url,
    })

    ts = int(datetime.utcnow().timestamp())
    return _back_to_panel(ts)

@app.context_processor
def inject_avatar_url():
    url = None
    try:
        uid, _ = _whoami()
        # 1) Arquivo físico salvo como <uid>.webp
        if uid:
            filename  = f"{uid}.webp"
            dest_path = os.path.join(AVATAR_DIR, filename)
            if os.path.exists(dest_path):
                v = int(os.path.getmtime(dest_path))
                url = url_for('static', filename=f'uploads/avatars/{filename}') + f'?v={v}'

        # 2) Fallback: caminho salvo no DB (sem ?v)
        if not url:
            emp = _pegar_empresa_do_usuario(required=False)
            rel = None
            if emp is not None:
                rel = getattr(emp, 'foto_url', None) or getattr(emp, 'logo_url', None)
            if rel:
                if rel.startswith('http://') or rel.startswith('https://'):
                    url = rel
                else:
                    # normaliza quando o DB guarda "/static/..."
                    rel_clean = rel.replace('/static/', '', 1) if rel.startswith('/static/') else rel.lstrip('/')
                    url = url_for('static', filename=rel_clean)
    except Exception:
        pass

    # mantém sessão em sincronia (útil para o template atual que consulta a sessão)
    if url:
        session['avatar_url'] = url
    return {'avatar_url': url}

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
                if val is None:
                    return None
                s = str(val).strip().replace(",", ".")
                return int(float(s))
            except Exception:
                return None

        # Texto
        tear.marca  = (request.form.get("marca")  or "").strip() or None
        tear.modelo = (request.form.get("modelo") or "").strip() or None

        # Tipo normalizado
        tipo = (request.form.get("tipo") or "").strip().upper()
        tear.tipo = tipo if tipo in {"MONO","DUPLA"} else (tipo or None)

        # Numéricos
        finura        = _to_int(request.form.get("finura"))
        diametro      = _to_int(request.form.get("diametro"))
        alimentadores = _to_int(request.form.get("alimentadores"))
        pistas_cil    = _to_int(request.form.get("pistas_cilindro"))
        pistas_dis    = _to_int(request.form.get("pistas_disco"))

        if hasattr(tear, "finura"):           tear.finura = finura
        if hasattr(tear, "galga"):            tear.galga  = finura         # espelho
        if hasattr(tear, "diametro"):         tear.diametro = diametro
        if hasattr(tear, "alimentadores"):    tear.alimentadores = alimentadores
        if hasattr(tear, "pistas_cilindro"):  tear.pistas_cilindro = pistas_cil
        if hasattr(tear, "pistas_disco"):     tear.pistas_disco    = pistas_dis

        # Elastano (compatível com bool e "Sim/Não")
        elas_raw = (request.form.get("elastano") or "").strip().lower()
        el_bool = True  if elas_raw in {"sim","s","1","true","on","yes","y","com","tem"} else \
                  False if elas_raw in {"não","nao","n","0","false","off","no","sem"} else None

        if el_bool is not None:
            if hasattr(tear, "elastano"):
                cur = getattr(tear, "elastano")
                tear.elastano = (el_bool if isinstance(cur, bool) else ("Sim" if el_bool else "Não"))
            if hasattr(tear, "kit_elastano"):
                tear.kit_elastano = "Sim" if el_bool else "Não"

        db.session.add(tear)
        db.session.commit()
        flash("Tear atualizado com sucesso!", "success")
        return redirect(url_for("painel_malharia"))

    # GET
    return render_template("editar_tear.html", empresa=emp, tear=tear)

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
    # ED01: a exportação pública deve seguir a mesma regra da Home.
    query = Tear.query.join(Empresa).filter(Empresa.assinatura_ativa)
    if filtros_raw['tipo']:
        query = query.filter(Tear.tipo == filtros_raw['tipo'])
    if diametro is not None:
        query = query.filter(Tear.diametro == diametro)
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

        # Normaliza CEP para somente dígitos (ex.: '00000-000' -> '00000000')
        import re
        cep_digits = re.sub(r'\D', '', cep_raw)

        erros = {}

        # Telefone
        if len(telefone) < 10 or len(telefone) > 13:
            erros['telefone'] = 'Telefone inválido.'

        # Nome (duplicidade)
        if Empresa.query.filter_by(nome=nome).first():
            erros['nome'] = 'Nome já existe.'

        # Apelido (opcional, mas único se informado)
        if apelido and Empresa.query.filter_by(apelido=apelido).first():
            erros['apelido'] = 'Apelido em uso.'

        # E-mail (duplicidade)
        if Empresa.query.filter_by(email=email).first():
            erros['email'] = 'E-mail já cadastrado.'

        # UF válida
        if estado not in estados:
            erros['estado'] = 'Estado inválido.'

        # Cidade obrigatória
        if not cidade:
            erros['cidade'] = 'Selecione a cidade.'

        # Nome responsável (mínimo 2 letras, desconsiderando acentos e espaços)
        if not responsavel_nome or len(re.sub(r'[^A-Za-zÀ-ÿ]', '', responsavel_nome)) < 2:
            erros['responsavel_nome'] = 'Informe o nome do responsável.'

        # Endereço completo obrigatório
        if not endereco_full:
            erros['endereco'] = 'Informe o endereço completo.'

        # CEP: precisa ter 8 dígitos após normalização
        if not re.fullmatch(r'\d{8}', cep_digits or ''):
            erros['cep'] = 'Informe um CEP válido (00000-000 ou 00000000).'

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

        # Grava Endereço completo e CEP (armazenando CEP apenas com dígitos)
        _set_if_has(nova_empresa, ["endereco","logradouro","endereco_completo"], endereco_full)
        _set_if_has(nova_empresa, ["cep","CEP"], cep_digits)

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

    # Normaliza CEP para apenas dígitos (ex.: '00000-000' -> '00000000')
    import re
    cep_digits = re.sub(r'\D', '', cep_raw)

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
    if not re.fullmatch(r'\d{8}', cep_digits or ''):
        erros['cep'] = 'Informe um CEP válido (00000-000 ou 00000000).'

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
    # Armazena CEP somente com dígitos (padrão unificado no banco)
    _set_if_has(empresa, ["cep","CEP"], cep_digits)

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
@limiter.limit("5 per 15 minutes", methods=["POST"])
def admin_login():
    if request.method == 'POST':
        email = (request.form.get('email') or '').strip().lower()
        senha = request.form.get('senha') or ''
        valid = False
        if ADMIN_EMAIL and ADMIN_PASSWORD_HASH and hmac.compare_digest(email, ADMIN_EMAIL):
            try:
                valid = check_password_hash(ADMIN_PASSWORD_HASH, senha)
            except (ValueError, TypeError):
                app.logger.error('[ADMIN] ADMIN_PASSWORD_HASH inválido')
        if valid:
            # Elimina dados antigos e impede que outro perfil seja herdado.
            session.clear()
            session['admin_email'] = ADMIN_EMAIL
            session['admin_auth_version'] = 'v2'
            flash('Login de administrador realizado.', 'success')
            return redirect(url_for('admin_empresas'))
        session.pop('admin_email', None)
        session.pop('admin_auth_version', None)
        flash('E-mail ou senha incorretos.', 'error')
        return redirect(url_for('admin_login'))
    return render_template('admin_login.html')


@app.get('/admin_login')
def admin_login_compat():
    return redirect(url_for('admin_login'), code=302)

@app.route('/admin/logout', methods=['POST'])
def admin_logout():
    session.pop('admin_email', None)
    flash('Você saiu do painel administrativo.')
    return redirect(url_for('index'))

from datetime import datetime, timedelta

from datetime import datetime, timedelta
from sqlalchemy import func  # <- garante o import

@app.route('/admin/empresas', methods=['GET', 'POST'])
@login_admin_requerido
def admin_empresas():
    pagina = int(request.args.get('pagina', 1))
    por_pagina = 10

    STATUS_VALIDOS = {"ativo", "pendente"}

    # valores padrão
    status = ''
    data_inicio = ''
    data_fim = ''
    f_plano = 'todos'  # <- NOVO: filtro de plano (mensal|anual|todos)

    query = Empresa.query

    # ----- POST: lê filtros do formulário e redireciona para GET com querystring
    if request.method == 'POST':
        status      = (request.form.get('status', '') or '').strip().lower()
        data_inicio = (request.form.get('data_inicio', '') or '').strip()
        data_fim    = (request.form.get('data_fim', '') or '').strip()
        f_plano     = (request.form.get('plano', 'todos') or 'todos').strip().lower()  # <- NOVO

        return redirect(url_for(
            'admin_empresas',
            pagina=1,
            status=status,
            data_inicio=data_inicio,
            data_fim=data_fim,
            plano=f_plano,               # <- NOVO
        ))

    # ----- GET: aplica filtros
    status      = (request.args.get('status', '') or '').strip().lower()
    data_inicio = (request.args.get('data_inicio', '') or '').strip()
    data_fim    = (request.args.get('data_fim', '') or '').strip()
    f_plano     = (request.args.get('plano', 'todos') or 'todos').strip().lower()  # <- NOVO

    # status
    if status in STATUS_VALIDOS:
        query = query.filter(Empresa.status_pagamento == status)

    # datas (inclusive no fim do dia)
    if data_inicio:
        try:
            dt_ini = datetime.strptime(data_inicio, "%Y-%m-%d")
            query = query.filter(Empresa.data_pagamento >= dt_ini)
        except ValueError:
            pass

    if data_fim:
        try:
            dt_fim = datetime.strptime(data_fim, "%Y-%m-%d") + timedelta(days=1)
            query = query.filter(Empresa.data_pagamento < dt_fim)
        except ValueError:
            pass

    # ----- NOVO: filtro por PLANO (Mensal/Anual)
    # normaliza possíveis valores já gravados
    if f_plano == 'mensal':
        query = query.filter(
            func.lower(func.coalesce(Empresa.plano, 'mensal')).in_(
                ['mensal', 'monthly', '1m', '']
            )
        )
    elif f_plano == 'anual':
        query = query.filter(
            func.lower(func.coalesce(Empresa.plano, 'mensal')).in_(
                ['anual', 'annual', 'yearly', '12m', 'ano']
            )
        )
    # 'todos' não filtra

    total = query.count()
    empresas = (query
               .order_by(Empresa.nome)
               .offset((pagina - 1) * por_pagina)
               .limit(por_pagina)
               .all())

    total_paginas = (total + por_pagina - 1) // por_pagina

    return render_template(
        'admin_empresas.html',
        empresas=empresas,
        pagina=pagina,
        total_paginas=total_paginas,
        status=status,
        data_inicio=data_inicio,
        data_fim=data_fim,
        plano=f_plano,                 # <- NOVO: devolve pro template manter seleção
    )

@app.route('/admin/editar_status/<int:empresa_id>', methods=['POST'])
@login_admin_requerido
def admin_editar_status(empresa_id):
    abort(403)  # release V1: alteracoes de status somente pelo reconciliador financeiro
    empresa = Empresa.query.get_or_404(empresa_id)
    # Com o faturamento V1, o administrador pode suspender; somente um
    # pagamento conciliado e aprovado pode reativar a conta.
    if (empresa.status_pagamento or '').strip().lower() != 'ativo':
        flash('Ativacao manual bloqueada. Somente pagamento confirmado ativa malharia.', 'warning')
        return redirect(url_for('admin_empresas'))

    STATUS_VALIDOS = {'pendente'}

    status_anterior = (empresa.status_pagamento or '').strip().lower()
    empresa.status_pagamento = 'pendente'
    empresa.assinatura_ate = None
    empresa.data_pagamento = None
    db.session.commit()

    flash(f'Malharia "{empresa.apelido or empresa.nome}" suspensa.', 'success')

    return redirect(url_for('admin_empresas',
                            pagina=request.args.get('pagina', 1),
                            status=request.args.get('status', ''),
                            data_inicio=request.args.get('data_inicio', ''),
                            data_fim=request.args.get('data_fim', '')))

@app.route('/admin/empresa_excluir/<int:empresa_id>', methods=['POST'])
@login_admin_requerido
def empresa_excluir(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)
    if BillingIntent.query.filter_by(empresa_id=empresa.id).first():
        return 'Conta com historico financeiro: solicitar exclusao assistida ao suporte.', 409
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
    if BillingIntent.query.filter_by(empresa_id=empresa.id).first():
        flash('Sua conta possui historico de cobrancas. Contate o suporte para solicitar a exclusao segura.', 'warning')
        return redirect(url_for('painel_malharia'))

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
    # Rotas de teste/impersonação desligadas em produção por padrão.
    # Exigem sessão administrativa, habilitação explícita e token não padrão.
    configured = (os.getenv('SEED_TOKEN') or '').strip()
    return bool(
        _admin_autenticado()
        and _env_bool('ENABLE_ADMIN_TEST_ROUTES', False)
        and configured
        and configured != 'ACHETECE'
        and hmac.compare_digest(request.args.get('token') or '', configured)
    )

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
    abort(404)  # release: utilitario desabilitado
    if not _seed_ok(): return "Não autorizado", 403
    empresa_id = request.args.get("empresa_id", type=int)
    n = request.args.get("n", default=5, type=int)
    if not empresa_id: return "Informe empresa_id", 400
    emp = Empresa.query.get_or_404(empresa_id)
    qtd = _cria_teares_fake(emp, n)
    return f"OK: +{qtd} teares em {emp.apelido or emp.nome or getattr(emp, 'nome_fantasia', emp.id)} (id={emp.id})."

@app.route("/admin/seed_teares_all")
def admin_seed_teares_all():
    abort(404)  # release: utilitario desabilitado
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
            q = q.filter(Empresa.status_pagamento == "ativo")
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
    # ED01: rota sem autenticação também não pode divulgar empresas vencidas.
    q = Empresa.query.filter(Empresa.assinatura_ativa)
    if escopo == "demo":
        q = q.filter(DEMO_FILTER)
    elif escopo == "pagantes":
        pass  # a condição de pagamento válido já foi aplicada obrigatoriamente
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
    abort(404)  # release: impersonacao desabilitada
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
    abort(404)  # release: impersonacao desabilitada
    session.pop("admin_impersonando", None)
    session.pop("perfil", None)
    session.pop("empresa_id", None)
    return redirect(url_for("index"))

# --------------------------------------------------------------------
# Rota de teste de e-mail (manual)
# --------------------------------------------------------------------
@app.get("/admin/test-email")
def admin_test_email():
    abort(404)  # release: utilitario desabilitado
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

# --- Checkout Mercado Pago ---------------------------------------------------
def _mp_sdk():
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("MP_ACCESS_TOKEN não definido.")
    return mercadopago.SDK(token)

def _extract_payment_id(req):
    """
    MP pode mandar o payment_id no JSON OU na querystring.
    A tua tela mostra action=payment.created, mas o id pode vir em args.
    """
    payload = req.get_json(silent=True) or {}

    # JSON: {"data":{"id":...}}
    if isinstance(payload, dict):
        data = payload.get("data") or {}
        if isinstance(data, dict) and data.get("id"):
            return str(data["id"]), payload

    # Querystring: ?type=payment&data.id=123
    if req.args.get("type") == "payment" and req.args.get("data.id"):
        return str(req.args.get("data.id")), payload

    # Querystring: ?topic=payment&id=123
    if req.args.get("topic") == "payment" and req.args.get("id"):
        return str(req.args.get("id")), payload

    return None, payload

def _mp_get_payment(payment_id: str) -> dict:
    sdk = _mp_sdk()
    resp = sdk.payment().get(payment_id)
    payment = (resp or {}).get("response") or {}
    if not payment:
        raise RuntimeError(f"Não consegui obter payment.response. Resp={resp}")
    return payment

def _parse_empresa_id_from_external_reference(ext_ref: str):
    # teu ext_ref = "achetece:{empresa.id}:{uuid}"
    if not ext_ref:
        return None
    parts = str(ext_ref).split(":")
    if len(parts) >= 2 and parts[0] == "achetece":
        try:
            return int(parts[1])
        except:
            return None
    return None

def _send_email(to_email: str, subject: str, text_body: str, html_body: str):
    # Aceita tanto MAIL_* quanto SMTP_*
    host = os.environ.get("MAIL_HOST") or os.environ.get("SMTP_HOST")
    port = int(os.environ.get("MAIL_PORT") or os.environ.get("SMTP_PORT") or "587")
    user = os.environ.get("MAIL_USER") or os.environ.get("SMTP_USER")
    pwd  = os.environ.get("MAIL_PASS") or os.environ.get("SMTP_PASS")

    mail_from = os.environ.get("MAIL_FROM") or user
    from_name = os.environ.get("MAIL_FROM_NAME", "AcheTece")

    reply_to = os.environ.get("MAIL_REPLY_TO") or "gestao.achetece@gmail.com"  # ajuste se quiser

    if not (host and user and pwd and mail_from and to_email):
        app.logger.warning("[EMAIL] Config incompleta (host/user/pass/from) ou destinatário vazio.")
        return

    # multipart: texto + html
    msg = MIMEMultipart("alternative")
    msg["Subject"] = subject
    msg["From"] = f"{from_name} <{mail_from}>"
    msg["To"] = to_email

    # Headers de entregabilidade
    msg["Reply-To"] = reply_to
    msg["Message-ID"] = make_msgid(domain="achetece.com.br")  # pode manter mesmo usando gmail
    msg["X-Entity-Ref-ID"] = str(uuid.uuid4())

    # Partes
    msg.attach(MIMEText(text_body or "", "plain", "utf-8"))
    msg.attach(MIMEText(html_body or "", "html", "utf-8"))

    try:
        if port == 465:
            with smtplib.SMTP_SSL(host, port) as smtp:
                smtp.login(user, pwd)
                smtp.sendmail(mail_from, [to_email], msg.as_string())
        else:
            with smtplib.SMTP(host, port) as smtp:
                smtp.ehlo()
                smtp.starttls()
                smtp.ehlo()
                smtp.login(user, pwd)
                smtp.sendmail(mail_from, [to_email], msg.as_string())

        app.logger.info(f"[EMAIL] Enviado para {to_email}")

    except Exception as e:
        app.logger.exception(f"[EMAIL] Falha ao enviar: {e}")

def _email_ativacao_html(empresa, magic_link: str) -> str:
    nome_malharia = (empresa.apelido or empresa.nome or "sua malharia").strip()

    return f"""
    <div style="font-family:Arial,sans-serif;max-width:640px;line-height:1.5">
      <h2>Pagamento aprovado ✅</h2>
      <p>Olá, <strong>{nome_malharia}</strong>!</p>
      <p>Sua conta no <strong>AcheTece</strong> está ativa.</p>

      <p style="margin:20px 0">
        <a href="{magic_link}" style="background:#111;color:#fff;padding:12px 16px;border-radius:10px;text-decoration:none;">
          Entrar no AcheTece
        </a>
      </p>

      <p style="color:#666;font-size:12px">
        Se você não solicitou isso, ignore esta mensagem.
      </p>
    </div>
    """

def _serializer():
    salt = os.environ.get("MAGIC_LINK_SALT", "achetece-magic")
    return URLSafeTimedSerializer(app.secret_key, salt=salt)

def _make_magic_link(empresa_id: int) -> str:
    token = _serializer().dumps({"empresa_id": empresa_id})
    base = _release_base_url() if _release_billing_ready() else _public_base_url()
    return f"{base}/magic/{token}"

def _release_mp_client():
    return MercadoPagoClient((os.environ.get('MP_ACCESS_TOKEN') or '').strip())


def _release_base_url():
    if not _release_billing_ready():
        raise RuntimeError('Ambiente de cobranca nao configurado')
    return os.environ['ACHETECE_BILLING_CALLBACK_BASE_URL'].rstrip('/')


def _processar_pagamento(payment_id: str, *, invoice_id: str | None = None):
    """Conciliação servidor-servidor, atomicidade + idempotência do provedor."""
    if not _release_billing_ready() or not _env_bool('ACHETECE_RELEASE_SCHEMA_READY', False):
        raise RuntimeError('Faturamento da release ainda nao habilitado')
    client = _release_mp_client()
    payment = client.get_payment(payment_id)
    ref = str(payment.get('external_reference') or '')
    verified_invoice_reference = None
    if invoice_id is not None:
        invoice = client.get_authorized_payment(invoice_id)
        subscription_id = str(invoice.get('preapproval_id') or '')
        intent = BillingIntent.query.filter_by(provider_resource_id=subscription_id, plano='mensal').first()
        if intent is None:
            raise BillingError('Assinatura da fatura nao localizada')
        verify_authorized_invoice(
            invoice, invoice_id=invoice_id, subscription_id=subscription_id,
            reference=intent.external_reference, payment_id=payment_id,
            cents=intent.amount_cents, currency=intent.currency)
        # Confere tambem a assinatura junto ao provedor, sem confiar na fatura
        # como unica prova do vinculo com o recebedor.
        subscription = client.get_subscription(subscription_id)
        if (str(subscription.get('id') or '') != subscription_id or
                str(subscription.get('external_reference') or '') != intent.external_reference or
                str(subscription.get('collector_id') or '') != os.environ['MP_COLLECTOR_ID']):
            raise BillingError('Assinatura/recebedor divergente')
        verified_invoice_reference = intent.external_reference
        if ref and ref != verified_invoice_reference:
            raise BillingError('Referencia de pagamento divergente da fatura')
    else:
        intent = BillingIntent.query.filter_by(external_reference=ref).first()
        if intent is None:
            raise BillingError('Contratacao nao localizada')

    # Excecao apenas em HML: comprador TESTUSER identificado no painel
    # oficial. Sem as tres chaves concordantes, mantem live_mode estrito.
    hml_test_buyer = None
    if (os.getenv('ACHETECE_BILLING_ENV') == 'sandbox'
            and os.getenv('ACHETECE_HML_EXPECTED_DB_NAME') == 'achetece_release_v1_test'
            and _env_bool('ACHETECE_MP_TEST_TOKEN_CONFIRMED', False)):
        hml_test_buyer = (os.getenv('ACHETECE_HML_TEST_BUYER_USER_ID') or '').strip()
    status = check_verified_payment(
        payment, expected_payment_id=payment_id, expected_reference=intent.external_reference,
        expected_cents=intent.amount_cents, expected_collector=os.environ['MP_COLLECTOR_ID'],
        expected_currency=intent.currency,
        expected_subscription=(intent.provider_resource_id if intent.plano == 'mensal' else None),
        expected_live_mode=(os.getenv('ACHETECE_BILLING_ENV') == 'production'),
        expected_hml_test_buyer_id=hml_test_buyer,
        verified_invoice_reference=verified_invoice_reference,
    )
    # Serialize payments for the same intent even during concurrent webhooks.
    intent = (BillingIntent.query.filter_by(id=intent.id)
              .with_for_update().one())
    prev = BillingPayment.query.filter_by(provider_payment_id=str(payment_id)).first()
    if prev is not None and prev.intent_id != intent.id:
        raise BillingError('Pagamento ja associado a outra contratacao')

    if status in ('refunded', 'charged_back'):
        # Reverter apenas o pagamento estornado; preservar direitos de outros
        # pagamentos aprovados validos da mesma empresa, se existirem.
        if prev and prev.status == 'approved':
            prev.status = status
            company = Empresa.query.filter_by(id=intent.empresa_id).with_for_update().one()
            active_payment = (BillingPayment.query.join(BillingIntent,
                               BillingPayment.intent_id == BillingIntent.id)
                              .filter(BillingIntent.empresa_id == company.id,
                                      BillingPayment.status == 'approved')
                              .order_by(BillingPayment.period_end.desc()).first())
            now = datetime.utcnow()
            if active_payment and active_payment.period_end > now:
                remaining_contract = BillingIntent.query.get(active_payment.intent_id)
                company.plano = remaining_contract.plano
                company.status_pagamento = 'ativo'
                company.assinatura_ate = active_payment.period_end
            else:
                company.status_pagamento = 'pendente'
                company.assinatura_ate = now
            db.session.commit()
        return {'ok': True, 'aplicado': False, 'estornado': bool(prev)}

    if status != 'approved':
        # Nunca desativar assinatura vigente por cobrança ainda pendente.
        return {'ok': True, 'aplicado': False, 'status': status}
    if prev is not None:
        return {'ok': True, 'aplicado': False, 'duplicado': True}
    if intent.plano == 'mensal' and not intent.provider_resource_id:
        raise BillingError('Assinatura recorrente nao vinculada')

    empresa = Empresa.query.filter_by(id=intent.empresa_id).with_for_update().one()
    agora = datetime.utcnow()
    origem = max(agora, empresa.assinatura_ate) if empresa.assinatura_ate else agora
    # Não sobrepor mensal e anual simultaneamente. Exigir cancelamento prévio.
    if (empresa.assinatura_ativa and empresa.plano != intent.plano):
        raise BillingError('Mudanca de plano exige conciliacao manual')
    vencimento = next_expiration(origem, intent.plano)

    db.session.add(BillingPayment(
        intent_id=intent.id, provider_payment_id=str(payment_id),
        amount_cents=intent.amount_cents, currency=intent.currency,
        status='approved', period_start=origem, period_end=vencimento,
    ))
    primeira_ativacao = not empresa.assinatura_ativa
    empresa.plano = intent.plano
    empresa.status_pagamento = 'ativo'
    empresa.data_pagamento = agora
    empresa.assinatura_ate = vencimento
    intent.status = ('paid' if intent.plano == 'anual' else
                     ('canceled' if intent.cancel_at_period_end else 'active'))
    db.session.commit()

    # E-mails não participam da transação financeira.
    if primeira_ativacao:
        try:
            link = _make_magic_link(empresa.id)
            if not _env_bool('MAIL_SUPPRESS_SEND', False):
                _send_email(empresa.email, 'Pagamento aprovado - AcheTece',
                            'Sua assinatura AcheTece foi ativada. Entre em '+link,
                            _email_ativacao_html(empresa, link))
        except Exception:
            app.logger.warning('[BILLING] Notificacao por e-mail nao concluida')
    return {'ok': True, 'aplicado': True, 'duplicado': False}


@app.route('/checkout', methods=['GET', 'POST'])
def checkout():
    if not _release_billing_ready() or not _env_bool('ACHETECE_RELEASE_SCHEMA_READY', False):
        return 'A assinatura ainda esta em preparacao. Tente novamente mais tarde.', 503

    empresa_id = session.get('empresa_id')
    if not empresa_id:
        return redirect(url_for('login'))
    empresa = Empresa.query.get(empresa_id)
    if not empresa:
        session.clear()
        return redirect(url_for('login'))
    plano = ((request.form.get('plano') if request.method == 'POST'
              else request.args.get('plano')) or 'mensal').lower()
    if plano not in ('mensal', 'anual'):
        abort(400)
    cents = _release_price_cents(plano)
    if request.method == 'GET':
        return render_template('checkout_confirmacao.html', plano=plano,
                               preco=fmt_brl(cents))

    # Nao permitir nova assinatura recorrente se ja houver uma ativa.
    open_recurring = (BillingIntent.query.filter_by(empresa_id=empresa.id, plano='mensal')
                      .filter(BillingIntent.status.in_(['active','paid','authorized']))
                      .order_by(BillingIntent.id.desc()).first())
    if open_recurring and open_recurring.provider_resource_id and not open_recurring.cancel_at_period_end:
        flash('Sua assinatura mensal ja esta configurada. Contate o suporte para alterar o plano.', 'warning')
        return redirect(url_for('painel_malharia'))
    if empresa.assinatura_ativa and empresa.plano != plano:
        flash('Para trocar de plano, contate o suporte.', 'warning')
        return redirect(url_for('painel_malharia'))

    recent_pending = (BillingIntent.query.filter_by(empresa_id=empresa.id, plano=plano)
                      .filter(BillingIntent.status.in_(['created','pending']))
                      .order_by(BillingIntent.id.desc()).first())
    if recent_pending:
        if recent_pending.checkout_url and recent_pending.checkout_url.startswith('https://'):
            return redirect(recent_pending.checkout_url, code=303)
        # Uma contratacao ja foi iniciada: nao criar outra concorrente.
        return 'Uma contratacao ja esta em processamento. Contate o suporte.', 409

    reference = 'achetece-v1-' + uuid.uuid4().hex
    intent = BillingIntent(empresa_id=empresa.id, external_reference=reference,
                           plano=plano, amount_cents=cents, currency='BRL', status='created')
    db.session.add(intent)
    db.session.commit()
    try:
        client = _release_mp_client()
        if plano == 'mensal':
            result = client.create_monthly_subscription(
                email=empresa.email, reference=reference, cents=cents,
                return_url=_release_base_url() + '/pagamento_pendente')
        else:
            sdk_local = _mp_sdk()
            preference_response = sdk_local.preference().create({
                'items': [{'title':'AcheTece - assinatura anual', 'quantity':1,
                           'currency_id':'BRL', 'unit_price': cents / 100}],
                'payer':{'email': empresa.email},
                'external_reference':reference,
                # A URL de notificacoes fica configurada no painel do
                # Mercado Pago (Webhooks, assinatura HMAC). Nao sobrescrever
                # por preferencia, pois isso tem prioridade sobre o painel.
                'back_urls':{'success':_release_base_url() + '/pagamento_aprovado',
                             'pending':_release_base_url() + '/pagamento_pendente',
                             'failure':_release_base_url() + '/pagamento_erro'},
                'auto_return':'approved',
                'statement_descriptor':'AcheTece',
            })
            result = preference_response.get('response') or {}
        url = result.get('init_point')
        resource_id = result.get('id')
        if not url or not resource_id or not url.startswith('https://'):
            raise ProviderError('Checkout nao confirmado pelo provedor')
        intent.provider_resource_id = str(resource_id)
        intent.checkout_url = url
        intent.status = 'pending'
        db.session.commit()
        return redirect(url, code=303)
    except Exception as exc:
        db.session.rollback()
        intent = BillingIntent.query.filter_by(external_reference=reference).first()
        if intent and intent.status == 'created':
            intent.status = 'failed'
            db.session.commit()
        # Codes only: never log raw error messages, MP response, email or tokens.
        if isinstance(exc, ProviderError):
            # V1.7: prova limitada de acesso a API de Assinaturas pelo MESMO
            # token; nunca executar em producao nem publicar dados da resposta.
            diag_hml = (
                os.getenv('ACHETECE_BILLING_ENV') == 'sandbox'
                and os.getenv('ACHETECE_HML_EXPECTED_DB_NAME') == 'achetece_release_v1_test'
                and os.getenv('ACHETECE_MP_TEST_TOKEN_CONFIRMED') == '1'
                and _env_bool('ACHETECE_BILLING_V1_ENABLED', False)
            )
            sub_api_http = 'nao_consultada'
            if plano == 'mensal' and diag_hml and isinstance(locals().get('client'), MercadoPagoClient):
                sub_api_http = client.subscription_api_access_status()
            if diag_hml:
                app.logger.warning(
                    '[BILLING] Checkout HML falhou plano=%s tipo=ProviderError '
                    'http=%s mp_error=%s mp_cause=%s mensagem_filtrada="%s" '
                    'causa_filtrada="%s" resposta_formato=%s '
                    'assinaturas_api_get_http=%s',
                    plano, exc.http_status, exc.provider_code or 'unspecified',
                    exc.cause_code or 'unspecified',
                    exc.message_hint, exc.cause_hint, exc.response_kind,
                    sub_api_http)
            else:
                app.logger.warning(
                    '[BILLING] Checkout falhou plano=%s tipo=ProviderError http=%s',
                    plano, exc.http_status)
        else:
            app.logger.warning('[BILLING] Checkout falhou plano=%s tipo=%s',
                               plano, type(exc).__name__)
        return 'Nao foi possivel iniciar o pagamento. Contate o suporte.', 503


@app.route('/assinatura/cancelar', methods=['POST'])
def cancelar_assinatura():
    empresa_id = session.get('empresa_id')
    if not empresa_id:
        return redirect(url_for('login'))
    if not _release_billing_ready():
        return 'Assinatura indisponivel', 503
    intent = (BillingIntent.query.filter_by(empresa_id=empresa_id, plano='mensal')
              .filter(BillingIntent.status.in_(['active','paid','authorized']))
              .order_by(BillingIntent.id.desc()).first())
    if not intent or not intent.provider_resource_id:
        return 'Nenhuma assinatura mensal recorrente encontrada', 404
    try:
        provider = _release_mp_client().cancel_subscription(intent.provider_resource_id)
    except (BillingError, ProviderError):
        return 'Nao foi possivel cancelar. Contate o suporte.', 503
    if provider.get('status') != 'canceled':
        return 'Cancelamento pendente de confirmacao', 503
    intent.status = 'canceled'
    intent.cancel_at_period_end = True
    db.session.commit()
    flash('Renovacao automatica cancelada. O acesso pago permanece ate o vencimento.', 'success')
    return redirect(url_for('painel_malharia'))


@app.route('/api/pagamento_status', methods=['GET'])
def api_pagamento_status():
    """Consulta apenas o estado salvo; jamais concede acesso via GET."""
    empresa_id = session.get('empresa_id')
    if not empresa_id:
        resp = jsonify({'status': 'nao_autenticado'})
        resp.status_code = 401
    else:
        empresa = db.session.get(Empresa, empresa_id)
        if empresa is None:
            resp = jsonify({'status': 'nao_encontrado'})
            resp.status_code = 404
        else:
            resp = jsonify({'status': 'ativo' if empresa.assinatura_ativa else 'pendente'})
    resp.headers['Cache-Control'] = 'no-store'
    return resp


@app.route('/assinatura/conciliar', methods=['GET', 'POST'])
@limiter.limit('10 per hour')
def conciliar_pagamento_hml():
    """Recupera pagamento existente apenas pelo titular, via API autenticada.

    Rotina de contingencia restrita ao banco HML. Nunca confia no status do
    retorno do navegador nem aceita identificador de outra malharia.
    """
    if (os.getenv('ACHETECE_HML_EXPECTED_DB_NAME') or '') != 'achetece_release_v1_test':
        abort(404)
    empresa_id = session.get('empresa_id')
    if not empresa_id:
        return redirect(url_for('login'))
    empresa = db.session.get(Empresa, empresa_id)
    if empresa is None:
        return redirect(url_for('login'))
    if not _release_billing_ready() or not _env_bool('ACHETECE_RELEASE_SCHEMA_READY', False):
        return 'Integracao financeira temporariamente indisponivel', 503
    if request.method == 'GET':
        return render_template('conciliacao_hml.html', empresa=empresa)

    payment_id = (request.form.get('payment_id') or '').strip()
    if not re.fullmatch(r'[0-9]{1,32}', payment_id):
        return 'ID do pagamento invalido', 400
    try:
        payment = _release_mp_client().get_payment(payment_id)
        ref = str(payment.get('external_reference') or '')
        intent = BillingIntent.query.filter_by(
            empresa_id=empresa.id, external_reference=ref).first()
        if intent is None or intent.plano != 'anual':
            return 'Pagamento nao pertence a esta malharia ou plano', 403
        result = _processar_pagamento(payment_id)
    except BillingError as exc:
        db.session.rollback()
        app.logger.warning('[BILLING] HML conciliacao recusada: %s', str(exc))
        return 'Nao foi possivel validar a cobranca; confira a notificacao no painel.', 422
    except ProviderError:
        db.session.rollback()
        return 'Mercado Pago indisponivel para verificacao; nao repita a compra.', 503
    except Exception:
        db.session.rollback()
        app.logger.exception('[BILLING] Falha HML na conciliacao manual')
        return 'Falha temporaria na conciliacao; nao repita a compra.', 503
    if result.get('aplicado') or result.get('duplicado'):
        return redirect(url_for('painel_malharia'), code=303)
    return 'Pagamento ainda nao confirmado pela API oficial; nenhuma liberacao feita.', 409


@app.route('/pagamento_aprovado')
def pagamento_aprovado():
    # O retorno do navegador nao comprova o pagamento.
    # Somente webhook assinado + consulta ao provedor ativam o cadastro.
    return render_template('pagamento_pendente.html')

@app.route('/pagamento_sucesso')
def pagamento_sucesso():
    # A URL publica nao e comprovante de liquidacao.
    return render_template('pagamento_pendente.html')

@app.route('/pagamento_erro')
def pagamento_erro():
    return render_template('pagamento_erro.html')

@app.route('/pagamento_pendente')
def pagamento_pendente():
    return render_template('pagamento_pendente.html')


@app.route('/webhook', methods=['POST'])
@csrf.exempt
@limiter.limit('120 per minute', methods=['POST'])
def webhook():
    """Webhook HMAC canonical; optional HML-only IPN as untrusted API hint.

    Never grant entitlement from request parameters, user agent or redirect.
    Only _processar_pagamento() can grant rights, after authenticated MP GET,
    exact intent/collector/amount/currency/payer checks and DB idempotency.
    """
    if not _release_billing_ready() or not _env_bool('ACHETECE_RELEASE_SCHEMA_READY', False):
        return jsonify({'ok': False, 'error': 'billing_offline'}), 503

    payload = request.get_json(silent=True) or {}
    if not isinstance(payload, dict):
        return jsonify({'ok': False, 'error': 'invalid_event'}), 400

    legacy_id = str(request.args.get('id') or '').strip()
    legacy_topic = str(request.args.get('topic') or '').strip().lower()
    # Checkout Pro's old merchant_order IPN is not itself a payment and must
    # never authorize an entitlement. Acknowledge only its known legacy shape.
    if legacy_topic == 'merchant_order' and re.fullmatch(r'[0-9]{1,32}', legacy_id) and not request.args.get('data.id'):
        app.logger.info('[BILLING] Aviso de ordem IPN recebido, sem ativacao')
        return jsonify({'ok': True, 'ignored': True}), 200

    channel = 'signed'
    if is_legacy_ipn_payment_hint(request.args, payload):
        # The legacy IPN signature is not verifiable by HMAC even if an
        # x-signature header happens to exist. This is a HML-only compatibility
        # path for previously created Checkout Pro preferences; NEVER prod.
        if not hml_legacy_ipn_hint_enabled(os.environ):
            app.logger.warning('[SECURITY] IPN nao autenticavel, fora da HML permitida')
            return jsonify({'ok':False,'error':'unauthorized'}),401
        topic = 'payment'
        event_id = legacy_id
        channel = 'legacy_hml_api_hint'
    else:
        # Modern signed Webhook. Never fall back to legacy upon HMAC failure.
        query_id = str(request.args.get('data.id') or '')
        body_data = payload.get('data')
        body_id = str(body_data.get('id') or '') if isinstance(body_data, dict) else ''
        if query_id and body_id and query_id.lower() != body_id.lower():
            app.logger.warning('[SECURITY] Webhook ids divergentes')
            return jsonify({'ok':False,'error':'unauthorized'}),401
        event_id = query_id or body_id
        topic = str(request.args.get('type') or payload.get('type') or '').strip().lower()
        body_type = str(payload.get('type') or '').strip().lower()
        if request.args.get('type') and body_type and topic != body_type:
            return jsonify({'ok':False,'error':'unauthorized'}),401
        signature = request.headers.get('X-Signature', '')
        request_id = request.headers.get('X-Request-Id', '')
        if not verify_webhook_signature(signature, request_id, event_id,
                                        os.getenv('MP_WEBHOOK_SECRET', '')):
            # Log only safe metadata, never token, HMAC, signature or payload.
            reason = ('missing_data_id' if not event_id else
                      'missing_signature' if not signature else
                      'missing_request_id' if not request_id else 'invalid_signature')
            app.logger.warning('[SECURITY] Webhook MP rejeitado: %s', reason)
            return jsonify({'ok':False,'error':'unauthorized'}),401

    if topic == 'payment':
        if not re.fullmatch(r'[0-9]{1,32}', event_id):
            return jsonify({'ok': False, 'error': 'invalid_payment_id'}), 400
        try:
            result = _processar_pagamento(event_id)
        except BillingError:
            db.session.rollback()
            app.logger.warning('[BILLING] Webhook %s: pagamento nao conciliado', channel)
            return jsonify({'ok': False, 'error': 'reconciliation_failed'}), 422
        except Exception:
            db.session.rollback()
            app.logger.error('[BILLING] Webhook %s: erro temporario', channel)
            return jsonify({'ok': False, 'error': 'temporarily_unavailable'}), 503
        # Safe diagnostics for operator, without ids, signatures or payer data.
        outcome = ('duplicate' if result.get('duplicado') else
                   'activated' if result.get('aplicado') else 'unchanged')
        app.logger.info('[BILLING] Webhook payment canal=%s resultado=%s', channel, outcome)
        return jsonify({'ok': True, 'result': outcome}), 200

    if topic == 'subscription_authorized_payment':
        # O data.id deste evento e ID DA FATURA, nao ID do pagamento.
        # Consultar a fatura autenticada e so depois o pagamento aprovado.
        if not re.fullmatch(r'[0-9]{1,32}', event_id):
            return jsonify({'ok': False, 'error': 'invalid_invoice_id'}), 400
        try:
            invoice = _release_mp_client().get_authorized_payment(event_id)
            paid = invoice.get('payment') or {}
            payment_id = str(paid.get('id') or '') if isinstance(paid, dict) else ''
            if not re.fullmatch(r'[0-9]{1,32}', payment_id) or str(paid.get('status') or '').lower() != 'approved':
                app.logger.info('[BILLING] Fatura recorrente recebida, ainda sem pagamento aprovado')
                return jsonify({'ok': True, 'result': 'unchanged'}), 200
            result = _processar_pagamento(payment_id, invoice_id=event_id)
        except BillingError:
            db.session.rollback()
            app.logger.warning('[BILLING] Fatura recorrente nao conciliada')
            return jsonify({'ok': False, 'error': 'reconciliation_failed'}), 422
        except Exception:
            db.session.rollback()
            app.logger.error('[BILLING] Falha temporaria ao validar fatura recorrente')
            return jsonify({'ok': False, 'error': 'temporarily_unavailable'}), 503
        outcome = ('duplicate' if result.get('duplicado') else
                   'activated' if result.get('aplicado') else 'unchanged')
        app.logger.info('[BILLING] Webhook subscription_authorized_payment canal=%s resultado=%s', channel, outcome)
        return jsonify({'ok': True, 'result': outcome}), 200

    if topic == 'subscription_preapproval':
        try:
            record = _release_mp_client().get_subscription(event_id)
            intent = BillingIntent.query.filter_by(provider_resource_id=str(record.get('id'))).first()
            if intent and record.get('external_reference') == intent.external_reference:
                if record.get('status') in ('canceled', 'paused'):
                    intent.status = str(record['status'])
                    intent.cancel_at_period_end = True
                    db.session.commit()
            return jsonify({'ok': True}), 200
        except Exception:
            db.session.rollback()
            return jsonify({'ok': False, 'error': 'temporarily_unavailable'}), 503

    # Outros eventos assinados permanecem somente informativos.
    return jsonify({'ok': True, 'ignored': True}), 200

@app.route("/magic/<token>")
def magic_login(token):
    try:
        data = _serializer().loads(token, max_age=15 * 60)  # 15 minutos
        empresa_id = int(data["empresa_id"])
    except SignatureExpired:
        return "<h3>Link expirado. Faça login novamente.</h3>", 401
    except (BadSignature, Exception):
        return "<h3>Link inválido.</h3>", 401

    empresa = Empresa.query.get(empresa_id)
    if not empresa:
        return "<h3>Empresa não encontrada.</h3>", 404

    if (empresa.status_pagamento or "").lower().strip() != "ativo":
        return "<h3>Conta ainda está pendente. Aguarde a confirmação.</h3>", 403

    session["empresa_id"] = empresa.id
    return redirect(url_for("painel_malharia"))

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
    abort(404)  # release V1: nao publicar rota de teste
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
    if not uf:
        return []
    uf = uf.strip().upper()
    # A lista oficial versionada com o aplicativo evita depender de chamadas
    # externas a cada cadastro (e funciona em deploys efêmeros do Render).
    cidades_estaticas = _CIDADES_ESTATICO.get(uf, [])
    if isinstance(cidades_estaticas, list) and cidades_estaticas:
        return cidades_estaticas
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
@limiter.limit("5 per hour", methods=["POST"])
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
    mensal = _release_price_cents('mensal') if os.getenv('ACHETECE_PRICE_MONTHLY_BRL') else 4700
    anual = _release_price_cents('anual') if os.getenv('ACHETECE_PRICE_YEARLY_BRL') else 47940
    return render_template('malharia_info.html', preco_mensal=fmt_brl(mensal),
                           preco_anual=fmt_brl(anual),
                           preco_equivalente=fmt_brl(anual // 12),
                           economia=fmt_brl(mensal * 12 - anual),
                           desconto_percentual=round((1 - anual / (12 * mensal)) * 100) if mensal else 0)

# --- Perfil público da empresa ---
from flask import render_template, abort, redirect, url_for
# ajuste os imports dos seus modelos conforme seu projeto:
# from models import Empresa, Tear
# ou: from app.models import Empresa, Tear

@app.get("/empresa/<int:empresa_id>")
def empresa_perfil(empresa_id):
    # ED01: a URL direta também deve respeitar a assinatura; 404 não expõe
    # dados comerciais de uma malharia pendente ou vencida.
    empresa = Empresa.query.filter(
        Empresa.id == empresa_id, Empresa.assinatura_ativa
    ).first_or_404()
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
    app.run(debug=False, host='127.0.0.1', port=5000)

