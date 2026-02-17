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
    SESSION_COOKIE_DOMAIN=".achetece.com.br"
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
    data_pagamento = db.Column(db.DateTime)  # último pagamento aprovado (UTC)

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
        """
        Regra FINAL:
        - Considera ativa se status_norm == "ativo"
        - E se data_pagamento existir: ainda está dentro do prazo do plano (mensal/anual)
        - Se data_pagamento for None e status estiver ativo: considera ativo (casos raros/trial)
        """
        if self.status_pagamento_norm != "ativo":
            return False

        if self.data_pagamento is None:
            return True

        dias = self._dias_por_plano(getattr(self, "plano", "mensal"))
        return (self.data_pagamento + timedelta(days=dias)) >= datetime.utcnow()

    @assinatura_ativa.expression
    def assinatura_ativa(cls):
        """
        Versão SQL (Postgres):
        status ok AND (data_pagamento IS NULL OR now() <= data_pagamento + intervalo(plano))
        """
        status_lower = func.lower(func.coalesce(cls.status_pagamento, ''))
        plano_lower  = func.lower(func.coalesce(cls.plano, 'mensal'))

        # dias por plano via CASE
        dias = func.case(
            (plano_lower == "anual", ASSINATURA_DIAS_ANUAL),
            else_=ASSINATURA_DIAS_MENSAL
        )

        # make_interval(days => CASE...) no Postgres
        return and_(
            status_lower.in_(list(STATUS_ATIVO_EQUIV)),
            or_(
                cls.data_pagamento.is_(None),
                func.now() <= (cls.data_pagamento + func.make_interval(days=dias))
            )
        )

    @property
    def assinatura_expira_em(self):
        if self.data_pagamento is None:
            return None
        dias = self._dias_por_plano(getattr(self, "plano", "mensal"))
        return self.data_pagamento + timedelta(days=dias)

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
        db.create_all()
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

        # 🔒 Regra de negócio: só empresas com pagamento/assinatura ativa
        # 1) Se você tiver a propriedade híbrida Empresa.assinatura_ativa (recomendado)
        try:
            q_base = q_base.filter(Empresa.assinatura_ativa)
        except Exception:
            # 2) Fallback por data "pago até"
            try:
                q_base = q_base.filter(Empresa.pago_ate >= db.func.now())
            except Exception:
                # 3) Fallback por status textual
                try:
                    q_base = q_base.filter(Empresa.assinatura_status.in_(["active", "approved", "trial"]))
                except Exception:
                    # Se nada disso existir, segue sem o filtro (legado)
                    pass
        # ---- FIM: nova query base ----
        
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

    if not DEMO_MODE and (user.status_pagamento or "").lower() not in ("aprovado", "ativo"):
        flash("Pagamento ainda não aprovado.", "warning")
        return redirect(url_for("login_method", email=email))

    session["empresa_id"] = user.id
    session["empresa_apelido"] = user.apelido or user.nome or user.email.split("@")[0]
    return redirect(url_for(""))

from flask import request, session, redirect, url_for, flash

@app.get("/oauth/google")
def oauth_google():
    # contexto padrão "empresa" e preserva redirecionamento
    ctx = request.args.get("ctx", "empresa")
    nxt = request.args.get("next") or url_for("")

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
    nxt = session.pop("oauth_next", url_for(""))

    if not email:
        flash("Não foi possível obter o e-mail do Google.", "danger")
        return redirect(url_for("login"))

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

@app.route("/logout")
def logout():
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

    # ---------------------------
    # Assinatura: status + vencimento por plano
    # ---------------------------
    # Status que consideramos "pago/ativo" (cobre variações comuns do MP)
    status_raw = (getattr(emp, "status_pagamento", None) or "pendente").strip().lower()
    STATUS_ATIVO = {"ativo", "aprovado", "approved", "paid", "active", "trial"}
    status_ok = status_raw in STATUS_ATIVO

    # Cálculo de vencimento do ciclo atual:
    # base = data_pagamento (ou outras datas se existirem) -> + dias do plano -> próximo dia útil BR
    vencimento_proximo, dias_restantes = (None, None)
    ativa_pelo_tempo = False

    try:
        # Hoje (preferindo timezone Brasil)
        try:
            from zoneinfo import ZoneInfo
            hoje = datetime.now(ZoneInfo("America/Sao_Paulo")).date()
        except Exception:
            from datetime import date as _date
            hoje = _date.today()

        # normaliza possíveis campos de data (sempre para date)
        def _to_date(v):
            if not v:
                return None
            if isinstance(v, datetime):
                return v.date()
            try:
                # se já vier date
                return v
            except Exception:
                return None

        ult_pgto = _to_date(getattr(emp, "assin_ultimo_pagamento", None))
        data_pag = _to_date(getattr(emp, "data_pagamento", None))
        inicio   = _to_date(getattr(emp, "assin_data_inicio", None))
        created  = _to_date(getattr(emp, "created_at", None))

        # ordem de prioridade: último pagamento > data_pagamento > início > criação > hoje
        base_dt = ult_pgto or data_pag or inicio or created or hoje

        # dias do ciclo conforme o plano
        plano = (getattr(emp, "plano", None) or "mensal").strip().lower()
        if "anual" in plano:
            dias_plano = 365
        else:
            # ✅ mensal com folga (se você quer 35 como já vinha usando)
            dias_plano = 35

        # vencimento nominal e ajuste para próximo dia útil BR
        nominal = base_dt + timedelta(days=dias_plano)
        venc = _proximo_dia_util_br(nominal)

        vencimento_proximo = venc

        # dias_restantes pode ficar negativo se já venceu (para você exibir "vencido")
        dias_restantes = (venc - hoje).days

        # Ativa pelo tempo (tolerância opcional)
        tol = int(globals().get("TOLERANCIA_DIAS", 0) or 0)
        ativa_pelo_tempo = hoje <= (venc + timedelta(days=tol))

        # (Opcional recomendado) Se venceu, e ainda está como "ativo", rebaixa para "pendente"
        # assim o admin e o painel ficam coerentes.
        if status_ok and (not ativa_pelo_tempo) and getattr(emp, "data_pagamento", None):
            try:
                emp.status_pagamento = "pendente"
                db.session.commit()
                status_ok = False
            except Exception:
                db.session.rollback()

    except Exception as e:
        app.logger.warning(f"[painel] cálculo de vencimento falhou: {e}")

    # Assinatura ativa = status OK (pagamento) E ainda dentro do prazo calculado
    is_ativa = bool(status_ok and ativa_pelo_tempo)

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
# Página da Aula (conteúdo + PDF + quiz + marcar concluída)
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
    if st == "not_started":
        try:
            _training_upsert(emp.id, mod.get("key"), aula.get("key"), "in_progress")
            st = "in_progress"
        except Exception:
            pass

    file_name = aula.get("file")
    file_url = url_for("treinamento_file", filename=file_name) if file_name else None

    return render_template(
        "treinamento_aula.html",
        empresa=emp,
        module=mod,
        lesson=aula,
        status=st,
        file_url=file_url,
    )

from flask import current_app, abort
from datetime import datetime

from datetime import datetime
from flask import current_app, abort, redirect, url_for

@app.post("/treinamento/<module_key>/<lesson_key>/concluir")
def treinamento_concluir(module_key, lesson_key):
    empresa_id = session.get("empresa_id")
    if not empresa_id:
        return redirect(url_for("login"))

    # Resolve o model existente (ajuste a lista se seu nome real for outro)
    Model = (
        globals().get("ProgressoAula")
        or globals().get("ProgressoTreinamento")
        or globals().get("TreinamentoProgresso")
        or globals().get("AulaProgresso")
    )
    if Model is None:
        current_app.logger.exception("Modelo de progresso não encontrado (ProgressoAula/alternativas).")
        abort(500)

    # Monta filtros compatíveis com nomes de colunas diferentes
    filtros = {"empresa_id": empresa_id}

    if hasattr(Model, "module_key"):
        filtros["module_key"] = module_key
    elif hasattr(Model, "modulo"):
        filtros["modulo"] = module_key
    elif hasattr(Model, "module"):
        filtros["module"] = module_key
    else:
        current_app.logger.exception("Model de progresso sem coluna de módulo (module_key/modulo/module).")
        abort(500)

    if hasattr(Model, "lesson_key"):
        filtros["lesson_key"] = lesson_key
    elif hasattr(Model, "aula"):
        filtros["aula"] = lesson_key
    elif hasattr(Model, "lesson"):
        filtros["lesson"] = lesson_key
    else:
        current_app.logger.exception("Model de progresso sem coluna de aula (lesson_key/aula/lesson).")
        abort(500)

    prog = Model.query.filter_by(**filtros).first()
    now = datetime.utcnow()

    def _set(obj, field, value):
        if hasattr(obj, field):
            setattr(obj, field, value)

    # ✅ Toggle por existência (funciona mesmo sem status/completed_at/is_done)
    if prog:
        # DESMARCAR: remove o registro
        db.session.delete(prog)
        db.session.commit()
        return redirect(url_for("treinamento_aula", module_key=module_key, lesson_key=lesson_key))

    # MARCAR: cria o registro mínimo
    prog = Model(**filtros)

    # Se existirem campos de timestamp no seu model, atualiza sem quebrar
    _set(prog, "updated_at", now)
    _set(prog, "created_at", now)
    _set(prog, "atualizado_em", now)
    _set(prog, "criado_em", now)

    db.session.add(prog)
    db.session.commit()

    return redirect(url_for("treinamento_aula", module_key=module_key, lesson_key=lesson_key))

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

@app.route('/admin/editar_status/<int:empresa_id>', methods=['GET', 'POST'])
@login_admin_requerido
def admin_editar_status(empresa_id):
    empresa = Empresa.query.get_or_404(empresa_id)

    STATUS_VALIDOS = {"ativo", "pendente"}

    status_req = (request.values.get('status') or '').strip().lower()

    if status_req in STATUS_VALIDOS:
        novo_status = status_req
    else:
        # toggle seguro
        novo_status = 'ativo' if (empresa.status_pagamento or '').strip().lower() != 'ativo' else 'pendente'

    status_anterior = (empresa.status_pagamento or '').strip().lower()

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
    q = Empresa.query
    if escopo == "demo":
        q = q.filter(DEMO_FILTER)
    elif escopo == "pagantes":
        q = q.filter(Empresa.status_pagamento == "ativo")
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
    base = _public_base_url()  # você já usa essa função
    return f"{base}/magic/{token}"

def _processar_pagamento(payment_id: str):
    """
    Consulta no MP e atualiza Empresa.status_pagamento / data_pagamento.
    Envia e-mail quando virar aprovado.
    """
    payment = _mp_get_payment(payment_id)

    status = (payment.get("status") or "").lower()          # approved, pending, in_process...
    detail = (payment.get("status_detail") or "").lower()
    ext_ref = payment.get("external_reference") or ""
    payer_email = (payment.get("payer") or {}).get("email")

    app.logger.info(f"[MP] payment_id={payment_id} status={status} detail={detail} ext_ref={ext_ref}")

    empresa_id = _parse_empresa_id_from_external_reference(ext_ref)

    # fallback por e-mail do pagador
    empresa = None
    if empresa_id:
        empresa = Empresa.query.get(empresa_id)
    if not empresa and payer_email:
        empresa = Empresa.query.filter(Empresa.email.ilike(payer_email)).first()

    if not empresa:
        raise RuntimeError("Não encontrei a Empresa para este pagamento (sem external_reference e sem match por email).")

    # Só envia e-mail quando houver transição para ativo
    status_atual = (empresa.status_pagamento or "").strip().lower()

    if status == "approved":
        status_atual = (empresa.status_pagamento or "").strip().lower()
    
        empresa.status_pagamento = "ativo"
        empresa.data_pagamento = datetime.utcnow()
        db.session.commit()
    
        # envia e-mail só na transição (evita spam por webhooks repetidos)
        if status_atual != "ativo":
            link = _make_magic_link(empresa.id)
            html = _email_ativacao_html(empresa, link)
            _send_email(empresa.email, "Pagamento aprovado - AcheTece", html)
    
        return {"ok": True, "empresa_id": empresa.id, "ativou": True}

    # outros status: mantém como pendente (mas atualiza se quiser)
    if status_atual != "ativo":
        empresa.status_pagamento = "pendente"
        db.session.commit()

    return {"ok": True, "empresa_id": empresa.id, "ativou": False, "status": status}

@app.route('/checkout')
def checkout():
    # exige sessão da empresa
    if 'empresa_id' not in session:
        return redirect(url_for('login'))

    empresa = Empresa.query.get(session['empresa_id'])
    if not empresa:
        session.clear()
        return redirect(url_for('login'))

    base = _public_base_url()

    # plano
    plano = (request.args.get('plano') or 'mensal').strip().lower()
    if plano not in ('mensal', 'anual'):
        plano = 'mensal'

    titulo_plano = "Assinatura anual AcheTece" if plano == 'anual' else "Assinatura mensal AcheTece"
    preco = float(PLAN_YEARLY if plano == 'anual' else PLAN_MONTHLY)

    # URLs de retorno + webhook
    success_url = f"{base}/pagamento_aprovado?plano={plano}"
    failure_url = f"{base}/pagamento_erro?plano={plano}"
    pending_url = f"{base}/pagamento_pendente?plano={plano}"
    notify_url  = f"{base}/webhook"  # se sua rota for /webhook/mercadopago, troque aqui

    ext_ref = f"achetece:{empresa.id}:{uuid.uuid4().hex}"

    preference_data = {
        "items": [{
            "title": titulo_plano,
            "quantity": 1,
            "currency_id": "BRL",
            "unit_price": preco
        }],
        "payer": {"email": getattr(empresa, "email", "")} if getattr(empresa, "email", "") else {},
        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url
        },
        "auto_return": "approved",
        "notification_url": notify_url,
        "external_reference": ext_ref,
        "statement_descriptor": "AcheTece"
    }

    try:
        sdk = mercadopago.SDK(os.environ.get("MP_ACCESS_TOKEN", ""))
        preference_response = sdk.preference().create(preference_data)
        preference = preference_response.get("response", {}) if isinstance(preference_response, dict) else {}
        init_point = preference.get("init_point") or preference.get("sandbox_init_point")

        if not init_point:
            app.logger.error(f"[CHECKOUT] init_point ausente. Resposta MP: {preference_response}")
            return "<h2>Erro ao iniciar pagamento (init_point ausente).</h2>", 500

        return redirect(init_point)

    except Exception as e:
        app.logger.exception(f"[CHECKOUT] Erro: {e}")
        return "<h2>Erro ao iniciar pagamento.</h2>", 500

@app.route('/pagamento_aprovado')
def pagamento_aprovado():
    payment_id = (
        request.args.get("payment_id")
        or request.args.get("collection_id")
        or request.args.get("paymentId")
    )

    if payment_id:
        try:
            result = _processar_pagamento(str(payment_id))
            app.logger.info(f"[BACK_URL] processado: {result}")
        except Exception as e:
            app.logger.exception(f"[BACK_URL] erro payment_id={payment_id}: {e}")

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
    if request.method == "GET":
        return jsonify({"ok": True, "hint": "webhook ativo"}), 200

    payment_id, payload = _extract_payment_id(request)

    # loga args + payload (isso é essencial!)
    app.logger.info(f"[WEBHOOK] args={dict(request.args)} payload={payload}")

    if not payment_id:
        app.logger.warning("[WEBHOOK] payment_id ausente. Vou responder 200 mesmo assim.")
        return jsonify({"ok": True, "ignored": True}), 200

    try:
        result = _processar_pagamento(payment_id)
        app.logger.info(f"[WEBHOOK] processado: {result}")
        return jsonify(result), 200
    except Exception as e:
        app.logger.exception(f"[WEBHOOK] erro payment_id={payment_id}: {e}")
        # 200 evita loop de reenvio agressivo
        return jsonify({"ok": True, "error": str(e)}), 200

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

