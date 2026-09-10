from flask import (
    Flask, render_template, request, redirect, url_for, flash, session,
    render_template_string, send_file, jsonify, abort, g
)
# Removido o uso de Flask-Mail; usamos Resend + SMTP com timeout
from flask_sqlalchemy import SQLAlchemy
from flask_wtf.csrf import (
    CSRFProtect,
    CSRFError
)
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
from PIL import Image, ImageOps, UnidentifiedImageError
import shutil
from datetime import datetime, timedelta
from flask import current_app, request
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from email.utils import make_msgid, formataddr
from authlib.integrations.flask_client import OAuth
from decimal import Decimal, InvalidOperation
import secrets
import hmac
import hashlib
import ipaddress
from flask_limiter import Limiter
from flask_limiter.errors import RateLimitExceeded

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

app.logger.setLevel(
    logging.INFO
)

# ==============================================================
# SEGURANÇA — SECRET KEY OBRIGATÓRIA
# ==============================================================

SECRET_KEY = (
    os.getenv("SECRET_KEY")
    or ""
).strip()

if not SECRET_KEY:

    raise RuntimeError(
        "SECRET_KEY não configurada no ambiente."
    )

app.config["SECRET_KEY"] = (
    SECRET_KEY
)

app.config[
    "PREFERRED_URL_SCHEME"
] = "https"

# ==============================================================
# SEGURANÇA — CSRF
# ==============================================================

# Durante a implantação progressiva, o CSRF não será
# aplicado automaticamente a todos os POSTs.
#
# As rotas protegidas serão adicionadas de forma controlada
# antes de ativarmos a proteção global.

app.config[
    "WTF_CSRF_CHECK_DEFAULT"
] = True

app.config[
    "WTF_CSRF_TIME_LIMIT"
] = 4 * 60 * 60

csrf = CSRFProtect(
    app
)


# ==============================================================
# ERRO CSRF
# ==============================================================

@app.errorhandler(
    CSRFError
)
def tratar_erro_csrf(error):

    # ==============================================================
    # LOG DE SEGURANÇA
    # ==============================================================

    current_app.logger.warning(
        (
            "[SECURITY][CSRF] "
            f"path={request.path} "
            f"endpoint={request.endpoint} "
            f"motivo={error.description}"
        )
    )

    flash(
        (
            "Sua sessão de segurança expirou "
            "ou a solicitação não pôde ser validada. "
            "Atualize a página e tente novamente."
        ),
        "warning"
    )

    # ==============================================================
    # ADMIN
    # ==============================================================

    if request.path.startswith(
        "/admin"
    ):

        return redirect(
            url_for(
                "admin_login"
            )
        )

    # ==============================================================
    # COMPRADOR
    # ==============================================================

    if request.path.startswith(
        "/comprador/"
    ):

        return redirect(
            url_for(
                "painel_comprador"
            )
        )

    # ==============================================================
    # MALHARIA
    # ==============================================================

    if request.path.startswith(
        "/malharia/"
    ):

        return redirect(
            url_for(
                "painel_malharia"
            )
        )

    # ==============================================================
    # LOGIN / OUTROS
    # ==============================================================

    return redirect(
        url_for("login")
    )

    # ----------------------------------------------------------
    # Área administrativa
    # ----------------------------------------------------------

    if request.path.startswith(
        "/admin"
    ):

        return redirect(
            url_for(
                "admin_login"
            )
        )

    # ----------------------------------------------------------
    # Login / conta
    # ----------------------------------------------------------

    return redirect(
        url_for("login")
    )

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

    # ==========================================================
    # SESSÃO / COOKIES
    # ==========================================================
    SESSION_COOKIE_SECURE=True,
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_DOMAIN=(os.getenv("SESSION_COOKIE_DOMAIN") or None),

    # Sessões permanentes expiram após 12 horas.
    PERMANENT_SESSION_LIFETIME=12 * 60 * 60,

    # Usuário ativo renova o prazo da sessão.
    SESSION_REFRESH_EACH_REQUEST=True,
)

RESEND_API_KEY = os.getenv("RESEND_API_KEY") or ""
RESEND_DOMAIN  = os.getenv("RESEND_DOMAIN", "achetece.com.br")
EMAIL_FROM     = os.getenv("EMAIL_FROM", f"AcheTece <no-reply@{RESEND_DOMAIN}>")
REPLY_TO       = os.getenv("REPLY_TO", "")
SITE_URL       = os.getenv("SITE_URL", "https://www.achetece.com.br")

# ==============================================================
# ADMINISTRAÇÃO
# ==============================================================

ADMIN_EMAIL = (
    os.getenv("ADMIN_EMAIL")
    or ""
).strip().lower()

ADMIN_PASSWORD = (
    os.getenv("ADMIN_PASSWORD")
    or ""
)

ENABLE_ADMIN_TOOLS = _env_bool(
    "ENABLE_ADMIN_TOOLS",
    False
)

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

def _otp_validate(
    email: str,
    codigo: str
):
    """
    Valida um OTP de login utilizando exclusivamente
    o registro server-side OtpToken/PostgreSQL.

    Segurança:
    - nenhum OTP é lido da session;
    - código comparado por HMAC-SHA256;
    - comparação com hmac.compare_digest;
    - expiração obrigatória;
    - máximo de 5 tentativas incorretas por token;
    - sucesso invalida imediatamente o token;
    - token utilizado não pode ser reutilizado.
    """

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    email = (
        email
        or ""
    ).strip().lower()

    codigo = (
        codigo
        or ""
    ).strip()

    # Remove qualquer OTP legado que ainda possa estar
    # presente em um cookie antigo do navegador.
    _otp_clear_legacy_session()

    if (
        not email
        or not codigo
    ):
        return (
            False,
            "Código inválido. Solicite um novo."
        )

    try:

        now = datetime.utcnow()

        # ======================================================
        # LOCALIZA O ÚLTIMO TOKEN NÃO UTILIZADO
        # ======================================================

        token = (
            OtpToken.query
            .filter(
                OtpToken.email == email,
                OtpToken.used_at.is_(None),
            )
            .order_by(
                OtpToken.created_at.desc(),
                OtpToken.id.desc(),
            )
            .first()
        )

        if not token:
            return (
                False,
                "Código não encontrado para este e-mail. "
                "Solicite um novo código."
            )

        # ======================================================
        # EXPIRAÇÃO
        # ======================================================

        if (
            not token.expires_at
            or now >= token.expires_at
        ):

            token.used_at = now
            db.session.commit()

            return (
                False,
                "Código expirado. Solicite um novo."
            )

        # ======================================================
        # LIMITE INTERNO DE TENTATIVAS
        # ======================================================

        attempts = int(
            token.attempts
            or 0
        )

        if attempts >= 5:

            token.used_at = now
            db.session.commit()

            return (
                False,
                "Muitas tentativas. Solicite um novo código."
            )

        # ======================================================
        # HASH DO CÓDIGO INFORMADO
        # ======================================================

        provided_hash = _otp_code_hash(
            email,
            codigo
        )

        stored_hash = (
            token.code_hash
            or ""
        ).strip()

        # ======================================================
        # COMPARAÇÃO SEGURA
        # ======================================================

        codigo_ok = (
            bool(stored_hash)
            and hmac.compare_digest(
                stored_hash,
                provided_hash
            )
        )

        # ======================================================
        # CÓDIGO INCORRETO
        # ======================================================

        if not codigo_ok:

            token.attempts = (
                attempts + 1
            )

            if token.attempts >= 5:

                token.used_at = now
                db.session.commit()

                return (
                    False,
                    "Muitas tentativas. "
                    "Solicite um novo código."
                )

            db.session.commit()

            return (
                False,
                "Código incorreto. Tente novamente."
            )

        # ======================================================
        # SUCESSO — USO ÚNICO
        # ======================================================

        token.used_at = now
        db.session.commit()

        return (
            True,
            "OK"
        )

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "Falha ao validar OTP de login"
        )

        return (
            False,
            "Não foi possível validar o código agora. "
            "Tente novamente."
        )


# Mercado Pago (mantido para compat)
MP_ACCESS_TOKEN = os.getenv("MP_ACCESS_TOKEN") or os.getenv("MERCADO_PAGO_TOKEN", "")
sdk = mercadopago.SDK(MP_ACCESS_TOKEN)
PLAN_MONTHLY = float(os.getenv("PLAN_MONTHLY", "2.00"))
PLAN_YEARLY  = float(os.getenv("PLAN_YEARLY", "2.00"))

# ==============================================================
# AMBIENTE DE DEMONSTRAÇÃO
# ==============================================================

DEMO_MODE = _env_bool(
    "DEMO_MODE",
    False
)

# ==============================================================
# AVATAR / FOTO DE PERFIL — CONFIGURAÇÃO SEGURA
# ==============================================================

# Limite máximo da requisição.
#
# Um upload acima de 5 MB é rejeitado pelo Flask antes
# mesmo de ser processado pelo Pillow.
app.config[
    "MAX_CONTENT_LENGTH"
] = 5 * 1024 * 1024


# ==============================================================
# DIRETÓRIOS
# ==============================================================

UPLOAD_DIR = os.path.join(
    app.root_path,
    "static",
    "uploads",
    "perfil"
)

AVATAR_DIR = os.path.join(
    app.root_path,
    "static",
    "uploads",
    "avatars"
)

os.makedirs(
    UPLOAD_DIR,
    exist_ok=True
)

os.makedirs(
    AVATAR_DIR,
    exist_ok=True
)


# ==============================================================
# FORMATOS ACEITOS
# ==============================================================
#
# IMPORTANTE:
#
# Não confiamos apenas:
# - no nome do arquivo;
# - na extensão;
# - no Content-Type enviado pelo navegador.
#
# O Pillow abrirá o conteúdo real do arquivo e identificará
# o formato verdadeiro.
# ==============================================================

ALLOWED_EXTENSIONS = {
    "png",
    "jpg",
    "jpeg",
    "webp",
    "gif",
}

ALLOWED_IMAGE_FORMATS = {
    "JPEG",
    "PNG",
    "WEBP",
    "GIF",
}


# ==============================================================
# LIMITE DE DIMENSÕES
# ==============================================================
#
# Mesmo um arquivo pequeno em MB pode representar uma imagem
# gigantesca quando descompactada.
#
# 20 milhões de pixels é mais que suficiente para uma foto
# destinada a um avatar de 400 x 400.
# ==============================================================

AVATAR_MAX_PIXELS = (
    20_000_000
)


def _allowed_file(
    filename: str
) -> bool:
    """
    Validação auxiliar da extensão.

    Não é utilizada como única prova de que o arquivo
    realmente é uma imagem.
    """

    filename = (
        filename
        or ""
    ).strip()

    if "." not in filename:

        return False

    extension = (
        filename
        .rsplit(
            ".",
            1
        )[1]
        .lower()
    )

    return (
        extension
        in ALLOWED_EXTENSIONS
    )


# ==============================================================
# AVATAR — VALIDAÇÃO E NORMALIZAÇÃO
# ==============================================================

def _save_square_webp(
    file_storage,
    dest_path: str,
    side: int = 400,
    quality: int = 85
):
    """
    Valida uma imagem enviada pelo usuário e cria um novo
    arquivo WEBP seguro.

    Retorna:
        {
            "format": "...",
            "width": ...,
            "height": ...
        }

    Segurança:
    - não confia na extensão;
    - não confia no MIME informado;
    - verifica o conteúdo utilizando Pillow;
    - rejeita formatos não autorizados;
    - rejeita imagens excessivamente grandes;
    - corrige orientação EXIF;
    - converte para RGB;
    - elimina metadados da imagem original;
    - elimina animações;
    - gera um arquivo WEBP completamente novo.
    """

    if file_storage is None:

        raise ValueError(
            "Arquivo de imagem ausente."
        )

    stream = getattr(
        file_storage,
        "stream",
        None
    )

    if stream is None:

        raise ValueError(
            "Fluxo da imagem inválido."
        )

    # ==========================================================
    # PRIMEIRA LEITURA — IDENTIFICA E VERIFICA
    # ==========================================================

    try:

        stream.seek(0)

        img_check = Image.open(
            stream
        )

        detected_format = (
            img_check.format
            or ""
        ).upper()

        width, height = (
            img_check.size
        )

        # ------------------------------------------------------
        # FORMATO REAL
        # ------------------------------------------------------

        if (
            detected_format
            not in ALLOWED_IMAGE_FORMATS
        ):

            raise ValueError(
                "Formato de imagem não permitido."
            )

        # ------------------------------------------------------
        # DIMENSÕES
        # ------------------------------------------------------

        if (
            width <= 0
            or height <= 0
        ):

            raise ValueError(
                "Dimensões de imagem inválidas."
            )

        total_pixels = (
            int(width)
            * int(height)
        )

        if (
            total_pixels
            > AVATAR_MAX_PIXELS
        ):

            raise ValueError(
                "Imagem possui dimensões excessivas."
            )

        # ------------------------------------------------------
        # VERIFICA INTEGRIDADE
        # ------------------------------------------------------

        img_check.verify()

    except (
        UnidentifiedImageError,
        OSError
    ) as exc:

        raise ValueError(
            "O arquivo enviado não é uma imagem válida."
        ) from exc

    # ==========================================================
    # SEGUNDA LEITURA
    #
    # verify() invalida o objeto anterior, então abrimos
    # novamente o stream.
    # ==========================================================

    try:

        stream.seek(0)

        img = Image.open(
            stream
        )

        # Se for GIF animado, utiliza somente o primeiro quadro.
        try:

            img.seek(
                0
            )

        except Exception:

            pass

        # ======================================================
        # ORIENTAÇÃO EXIF
        #
        # Evita fotos de celular aparecerem giradas.
        # ======================================================

        img = ImageOps.exif_transpose(
            img
        )

        # ======================================================
        # CONVERSÃO SEGURA PARA RGB
        # ======================================================

        possui_transparencia = (
            img.mode in {
                "RGBA",
                "LA",
            }
            or "transparency"
            in img.info
        )

        if possui_transparencia:

            rgba = img.convert(
                "RGBA"
            )

            background = Image.new(
                "RGB",
                rgba.size,
                (
                    255,
                    255,
                    255
                )
            )

            background.paste(
                rgba,
                mask=rgba.getchannel(
                    "A"
                )
            )

            img = background

        else:

            img = img.convert(
                "RGB"
            )

        # ======================================================
        # CROP QUADRADO CENTRAL
        # ======================================================

        width_current, height_current = (
            img.size
        )

        menor_lado = min(
            width_current,
            height_current
        )

        left = (
            width_current
            - menor_lado
        ) // 2

        top = (
            height_current
            - menor_lado
        ) // 2

        img = img.crop(
            (
                left,
                top,
                left + menor_lado,
                top + menor_lado
            )
        )

        # ======================================================
        # REDIMENSIONAMENTO
        # ======================================================

        try:

            lanczos = (
                Image.Resampling.LANCZOS
            )

        except AttributeError:

            lanczos = (
                Image.LANCZOS
            )

        img = img.resize(
            (
                side,
                side
            ),
            lanczos
        )

        # ======================================================
        # SALVA NOVO ARQUIVO
        #
        # Nenhum EXIF ou metadado original é copiado.
        # ==============================================================

        img.save(
            dest_path,
            format="WEBP",
            quality=quality,
            method=6
        )

        return {
            "format": detected_format,
            "width": width,
            "height": height,
        }

    except ValueError:

        raise

    except (
        UnidentifiedImageError,
        OSError,
        Image.DecompressionBombError
    ) as exc:

        raise ValueError(
            "A imagem não pôde ser processada com segurança."
        ) from exc

    finally:

        try:

            stream.seek(
                0
            )

        except Exception:

            pass

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
    Retorna a URL pública base utilizada para callbacks
    e retornos do Mercado Pago.

    Segurança:
    - prioriza PUBLIC_BASE_URL;
    - aceita SITE_URL como fallback configurado;
    - nunca força automaticamente o domínio de produção;
    - falha de forma segura se nenhuma URL estiver configurada.
    """

    base = (
        current_app.config.get("PUBLIC_BASE_URL")
        or os.getenv("PUBLIC_BASE_URL")
        or os.getenv("SITE_URL")
        or ""
    ).strip()

    if not base:

        current_app.logger.error(
            "[CONFIG] PUBLIC_BASE_URL/SITE_URL não configurada."
        )

        raise RuntimeError(
            "URL pública do ambiente não configurada."
        )

    base = base.rstrip("/")

    if not (
        base.startswith("https://")
        or base.startswith("http://")
    ):

        raise RuntimeError(
            "PUBLIC_BASE_URL possui formato inválido."
        )

    return base
    
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

# ==============================================================
# RECUPERAÇÃO DE SENHA — HELPERS SERVER-SIDE
# ==============================================================

def _password_reset_token_hash(raw_token: str) -> str:
    """
    Gera SHA-256 do token de recuperação.

    O token real possui alta entropia e nunca é armazenado
    no PostgreSQL. Somente seu hash é persistido.
    """

    raw_token = (
        raw_token
        or ""
    ).strip()

    if not raw_token:

        raise ValueError(
            "Token de recuperação vazio."
        )

    return hashlib.sha256(
        raw_token.encode("utf-8")
    ).hexdigest()


def _criar_password_reset_token(
    email: str,
    account_type: str,
    account_id: int,
    request_ip: str = "",
    user_agent: str = ""
):
    """
    Cria um token de recuperação de senha server-side.

    Retorna:
        (raw_token, token_record)

    raw_token:
        enviado ao usuário por e-mail.

    token_record:
        registro PasswordResetToken persistido no PostgreSQL.

    Segurança:
    - usa secrets.token_urlsafe(32);
    - somente SHA-256 do token é armazenado;
    - expiração de 1 hora;
    - invalida tokens anteriores ainda ativos da mesma conta;
    - registra IP e User-Agent da solicitação.
    """

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    email = (
        email
        or ""
    ).strip().lower()

    account_type = (
        account_type
        or ""
    ).strip().lower()

    if not email:

        raise ValueError(
            "E-mail obrigatório para recuperação."
        )

    if account_type not in {
        "cliente",
        "malharia",
    }:

        raise ValueError(
            "Tipo de conta inválido para recuperação."
        )

    try:

        account_id = int(
            account_id
        )

    except Exception as exc:

        raise ValueError(
            "ID da conta inválido para recuperação."
        ) from exc

    if account_id <= 0:

        raise ValueError(
            "ID da conta inválido para recuperação."
        )

    # ==========================================================
    # GERA TOKEN DE ALTA ENTROPIA
    # ==========================================================

    raw_token = secrets.token_urlsafe(
        32
    )

    token_hash = _password_reset_token_hash(
        raw_token
    )

    now = datetime.utcnow()

    expires_at = (
        now
        + timedelta(
            hours=1
        )
    )

    try:

        # ======================================================
        # INVALIDA TOKENS ANTERIORES DA MESMA CONTA
        # ======================================================

        previous_tokens = (
            PasswordResetToken.query
            .filter(
                PasswordResetToken.account_type
                == account_type,

                PasswordResetToken.account_id
                == account_id,

                PasswordResetToken.used_at.is_(None),
            )
            .all()
        )

        for previous_token in previous_tokens:

            previous_token.used_at = now

        # ======================================================
        # CRIA NOVO TOKEN
        # ======================================================

        token_record = PasswordResetToken(
            token_hash=token_hash,
            email=email,
            account_type=account_type,
            account_id=account_id,
            created_at=now,
            expires_at=expires_at,
            used_at=None,
            request_ip=(
                request_ip
                or ""
            )[:64],
            user_agent=(
                user_agent
                or ""
            )[:255],
        )

        db.session.add(
            token_record
        )

        db.session.commit()

        return (
            raw_token,
            token_record
        )

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[SECURITY][PASSWORD_RESET] "
            "Falha ao criar token de recuperação."
        )

        raise

# ==============================================================
# RECUPERAÇÃO DE SENHA — VALIDAÇÃO DO TOKEN SERVER-SIDE
# ==============================================================

def _buscar_password_reset_token(
    raw_token: str
):
    """
    Localiza e valida um token de recuperação de senha.

    Retorna:
        (token_record, None)
        quando o token é válido.

    Retorna:
        (None, motivo)
        quando o token é inválido.

    Regras:
    - token real nunca é armazenado no banco;
    - consulta feita pelo SHA-256;
    - token precisa existir;
    - token não pode ter sido utilizado;
    - token precisa estar dentro da validade;
    - token expirado é invalidado imediatamente.
    """

    raw_token = (
        raw_token
        or ""
    ).strip()

    if not raw_token:

        return (
            None,
            "invalid"
        )

    try:

        token_hash = _password_reset_token_hash(
            raw_token
        )

        token_record = (
            PasswordResetToken.query
            .filter(
                PasswordResetToken.token_hash
                == token_hash
            )
            .first()
        )

        # ======================================================
        # TOKEN NÃO EXISTE
        # ======================================================

        if not token_record:

            return (
                None,
                "invalid"
            )

        # ======================================================
        # TOKEN JÁ UTILIZADO / INVALIDADO
        # ======================================================

        if token_record.used_at is not None:

            return (
                None,
                "used"
            )

        now = datetime.utcnow()

        # ======================================================
        # TOKEN EXPIRADO
        # ======================================================

        if (
            not token_record.expires_at
            or now >= token_record.expires_at
        ):

            token_record.used_at = now

            db.session.commit()

            return (
                None,
                "expired"
            )

        # ======================================================
        # TIPO DE CONTA VÁLIDO
        # ======================================================

        account_type = (
            token_record.account_type
            or ""
        ).strip().lower()

        if account_type not in {
            "cliente",
            "malharia",
        }:

            token_record.used_at = now

            db.session.commit()

            current_app.logger.warning(
                (
                    "[SECURITY][PASSWORD_RESET] "
                    f"Token com account_type inválido. "
                    f"id={token_record.id}"
                )
            )

            return (
                None,
                "invalid"
            )

        # ======================================================
        # ID DE CONTA VÁLIDO
        # ======================================================

        try:

            account_id = int(
                token_record.account_id
            )

        except Exception:

            account_id = 0

        if account_id <= 0:

            token_record.used_at = now

            db.session.commit()

            return (
                None,
                "invalid"
            )

        # ======================================================
        # TOKEN VÁLIDO
        # ======================================================

        return (
            token_record,
            None
        )

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[SECURITY][PASSWORD_RESET] "
            "Falha ao validar token de recuperação."
        )

        return (
            None,
            "error"
        )

# ==============================================================
# RECUPERAÇÃO DE SENHA — RESOLUÇÃO DA CONTA
# ==============================================================

def _resolver_conta_recuperacao(email: str):
    """
    Localiza a conta associada ao e-mail informado.

    Retorna um dicionário padronizado para:
    - comprador/cliente -> Usuario
    - malharia          -> Empresa

    Retorna None quando nenhuma conta é encontrada.

    Importante:
    esta função não deve gerar mensagens diferentes para o
    usuário final. A proteção contra enumeração será feita
    posteriormente na rota /esqueci_senha.
    """

    email = (
        email
        or ""
    ).strip().lower()

    if not email:
        return None

    # Usa a mesma resolução central já utilizada no login.
    tipo, empresa, usuario = _achar_conta_login(
        email
    )

    # ==========================================================
    # COMPRADOR
    # ==============================================================

    if (
        tipo == "cliente"
        and usuario is not None
    ):

        email_conta = (
            getattr(
                usuario,
                "email",
                ""
            )
            or email
        ).strip().lower()

        return {
            "account_type": "cliente",
            "account_id": usuario.id,
            "email": email_conta,
            "nome": email_conta,
            "usuario": usuario,
            "empresa": None,
        }

    # ==========================================================
    # MALHARIA
    # ==============================================================

    if (
        tipo == "malharia"
        and empresa is not None
    ):

        email_conta = (
            getattr(
                empresa,
                "email",
                ""
            )
            or email
        ).strip().lower()

        nome_empresa = (
            getattr(
                empresa,
                "nome",
                ""
            )
            or email_conta
        ).strip()

        return {
            "account_type": "malharia",
            "account_id": empresa.id,
            "email": email_conta,
            "nome": nome_empresa,
            "usuario": usuario,
            "empresa": empresa,
        }

    return None


# ==============================================================
# RECUPERAÇÃO DE SENHA — ENVIO DO E-MAIL SERVER-SIDE
# ==============================================================

def enviar_email_recuperacao(
    email,
    nome_empresa=""
):
    """
    Gera e envia um link seguro de redefinição de senha.

    Fluxo:
    1. resolve a conta pelo e-mail;
    2. cria PasswordResetToken server-side;
    3. envia somente o token real no link;
    4. mantém somente SHA-256 do token no PostgreSQL;
    5. invalida o token criado se o envio do e-mail falhar.

    Retorna:
        True  -> e-mail enviado com sucesso.
        False -> conta não encontrada ou falha de envio.

    A rota pública deve utilizar resposta genérica para evitar
    enumeração de contas.
    """

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    email = (
        email
        or ""
    ).strip().lower()

    if not email:

        return False

    # ==========================================================
    # RESOLVE A CONTA
    # ==========================================================

    conta = _resolver_conta_recuperacao(
        email
    )

    if conta is None:

        current_app.logger.info(
            (
                "[SECURITY][PASSWORD_RESET] "
                "Solicitação para e-mail sem conta cadastrada. "
                f"ip={_client_ip()}"
            )
        )

        return False

    email_conta = (
        conta.get("email")
        or email
    ).strip().lower()

    account_type = (
        conta.get("account_type")
        or ""
    ).strip().lower()

    account_id = conta.get(
        "account_id"
    )

    nome_exibicao = (
        conta.get("nome")
        or nome_empresa
        or email_conta
    ).strip()

    # ==========================================================
    # CRIA TOKEN SERVER-SIDE
    # ==========================================================

    raw_token = None
    token_record = None

    try:

        raw_token, token_record = (
            _criar_password_reset_token(
                email=email_conta,
                account_type=account_type,
                account_id=account_id,
                request_ip=_client_ip(),
                user_agent=(
                    request.headers.get(
                        "User-Agent",
                        ""
                    )
                    or ""
                ),
            )
        )

        # ======================================================
        # MONTA LINK
        # ======================================================

        link = url_for(
            "redefinir_senha",
            token=raw_token,
            _external=True
        )

        # ======================================================
        # HTML DO E-MAIL
        # ======================================================

        html = render_template_string(
            """
<!doctype html>
<html lang="pt-br">

<head>
  <meta charset="utf-8">
  <title>Redefinição de Senha - AcheTece</title>
</head>

<body
  style="
    margin:0;
    padding:0;
    background:#F7F7FA;
    font-family:-apple-system,BlinkMacSystemFont,'Segoe UI',Roboto,Helvetica,Arial,sans-serif;
    color:#1e1b2b;
  "
>

  <table
    role="presentation"
    width="100%"
    cellspacing="0"
    cellpadding="0"
    border="0"
    style="
      width:100%;
      background:#F7F7FA;
      padding:24px 0;
    "
  >

    <tr>
      <td align="center">

        <table
          role="presentation"
          width="600"
          cellspacing="0"
          cellpadding="0"
          border="0"
          style="
            max-width:600px;
            width:100%;
            background:#ffffff;
            border:1px solid #eeeeee;
            border-radius:12px;
          "
        >

          <tr>
            <td
              style="
                padding:22px 24px;
                border-bottom:1px solid #f0f0f0;
              "
            >

              <h2
                style="
                  margin:0;
                  font-size:20px;
                  line-height:1.25;
                  font-weight:800;
                "
              >
                Redefinição de Senha
              </h2>

            </td>
          </tr>

          <tr>
            <td style="padding:22px 24px;">

              <p
                style="
                  margin:0 0 10px 0;
                  line-height:1.55;
                "
              >
                Olá <strong>{{ nome }}</strong>,
              </p>

              <p
                style="
                  margin:0 0 16px 0;
                  line-height:1.55;
                "
              >
                Recebemos uma solicitação para redefinir
                a senha da sua conta AcheTece.
              </p>

              <p
                style="
                  margin:0 0 16px 0;
                  line-height:1.55;
                "
              >
                Clique no botão abaixo para criar uma nova senha.
                Este link é válido por <strong>1 hora</strong>
                e poderá ser utilizado apenas uma vez.
              </p>

              <table
                role="presentation"
                cellspacing="0"
                cellpadding="0"
                border="0"
                style="margin:18px 0 10px 0;"
              >

                <tr>
                  <td
                    align="center"
                    bgcolor="#8A00FF"
                    style="border-radius:9999px;"
                  >

                    <a
                      href="{{ link }}"
                      target="_blank"
                      style="
                        display:inline-block;
                        padding:12px 24px;
                        border-radius:9999px;
                        background:#8A00FF;
                        color:#ffffff;
                        text-decoration:none;
                        font-weight:800;
                        font-size:16px;
                        line-height:1;
                      "
                    >
                      Redefinir senha
                    </a>

                  </td>
                </tr>

              </table>

              <p
                style="
                  margin:14px 0 0 0;
                  font-size:13px;
                  color:#6b6b6b;
                  line-height:1.5;
                "
              >

                Se o botão não funcionar, copie e cole este link
                no navegador:

                <br>

                <a
                  href="{{ link }}"
                  target="_blank"
                  style="
                    color:#5b2fff;
                    word-break:break-all;
                  "
                >
                  {{ link }}
                </a>

              </p>

            </td>
          </tr>

          <tr>
            <td
              style="
                padding:16px 24px;
                border-top:1px solid #f0f0f0;
                color:#6b6b6b;
                font-size:12px;
              "
            >

              Se você não solicitou esta redefinição,
              ignore este e-mail.

              Nenhuma alteração será realizada sem a utilização
              do link acima.

            </td>
          </tr>

        </table>

      </td>
    </tr>

  </table>

</body>

</html>
            """,
            nome=nome_exibicao,
            link=link
        )

        # ======================================================
        # VERSÃO TEXTO PURO
        # ======================================================

        text = (
            f"Olá, {nome_exibicao}!\n\n"
            "Recebemos uma solicitação para redefinir "
            "a senha da sua conta AcheTece.\n\n"
            "Para criar uma nova senha, acesse:\n"
            f"{link}\n\n"
            "Este link é válido por 1 hora e poderá "
            "ser utilizado apenas uma vez.\n\n"
            "Se você não solicitou esta redefinição, "
            "ignore este e-mail."
        )

        # ======================================================
        # ENVIO
        # ======================================================

        ok = send_email(
            to=email_conta,
            subject=(
                "Redefinição de Senha - AcheTece"
            ),
            html=html,
            text=text,
        )

        if not ok:

            # ==================================================
            # ENVIO FALHOU
            #
            # O token já foi persistido. Portanto precisamos
            # invalidá-lo para não deixar um token ativo que
            # nunca chegou ao usuário.
            # ==================================================

            if token_record is not None:

                try:

                    token_record.used_at = (
                        datetime.utcnow()
                    )

                    db.session.commit()

                except Exception:

                    db.session.rollback()

                    current_app.logger.exception(
                        (
                            "[SECURITY][PASSWORD_RESET] "
                            "Falha ao invalidar token após "
                            "erro no envio do e-mail."
                        )
                    )

            current_app.logger.error(
                (
                    "[SECURITY][PASSWORD_RESET] "
                    "Falha ao enviar e-mail de recuperação. "
                    f"account_type={account_type} "
                    f"account_id={account_id}"
                )
            )

            return False

        # ======================================================
        # SUCESSO
        # ======================================================

        current_app.logger.info(
            (
                "[SECURITY][PASSWORD_RESET_REQUEST] "
                f"account_type={account_type} "
                f"account_id={account_id} "
                f"ip={_client_ip()}"
            )
        )

        return True

    except Exception:

        db.session.rollback()

        # ======================================================
        # SE O TOKEN JÁ TIVER SIDO PERSISTIDO, TENTA INVALIDÁ-LO
        # ======================================================

        if token_record is not None:

            try:

                persisted_token = db.session.get(
                    PasswordResetToken,
                    token_record.id
                )

                if (
                    persisted_token is not None
                    and persisted_token.used_at is None
                ):

                    persisted_token.used_at = (
                        datetime.utcnow()
                    )

                    db.session.commit()

            except Exception:

                db.session.rollback()

                current_app.logger.exception(
                    (
                        "[SECURITY][PASSWORD_RESET] "
                        "Falha ao invalidar token após "
                        "exceção no fluxo de recuperação."
                    )
                )

        current_app.logger.exception(
            (
                "[SECURITY][PASSWORD_RESET] "
                "Falha inesperada ao gerar/enviar "
                "recuperação de senha."
            )
        )

        return False


def login_admin_requerido(f):

    @wraps(f)
    def decorated_function(
        *args,
        **kwargs
    ):

        admin_autenticado = (
            session.get(
                "admin_authenticated"
            )
            is True
        )

        admin_email_sessao = (
            session.get(
                "admin_email"
            )
            or ""
        ).strip().lower()

        if (
            not admin_autenticado
            or not ADMIN_EMAIL
            or admin_email_sessao
            != ADMIN_EMAIL
        ):

            flash(
                "Acesso administrativo necessário.",
                "warning"
            )

            return redirect(
                url_for(
                    "admin_login"
                )
            )

        return f(
            *args,
            **kwargs
        )

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
    "CARD_IMPRESSION",
    "COMPANY_PROFILE_VIEW",
    "CONTACT_CLICK_WHATSAPP",
    "TEAR_DETAIL_VIEW",
}


# ==============================================================
# ANALYTICS — IDENTIFICADOR DE SESSÃO INDEPENDENTE
# ==============================================================

def _analytics_session_id():
    """
    Retorna um identificador anônimo destinado exclusivamente
    aos eventos analíticos do AcheTece.

    Segurança:
    - nunca utiliza o cookie Flask de autenticação;
    - nunca grava request.cookies["session"] no banco;
    - não reutiliza identificadores internos de autenticação;
    - usa identificador aleatório próprio para analytics;
    - tamanho limitado.
    """

    analytics_sid = (
        session.get("_analytics_sid")
        or ""
    ).strip()

    if analytics_sid:

        return analytics_sid[:64]

    analytics_sid = secrets.token_urlsafe(
        18
    )

    session["_analytics_sid"] = (
        analytics_sid
    )

    session.modified = True

    return analytics_sid[:64]


# ==============================================================
# ANALYTICS — REGISTRO INTERNO DE EVENTOS
# ==============================================================

def track_event(
    event: str,
    company_id: int,
    tear_id: int | None = None,
    meta: dict | None = None
):
    """
    Registra internamente um evento analítico.

    Segurança:
    - aceita somente eventos autorizados;
    - utiliza session ID exclusivo de analytics;
    - nunca utiliza o cookie Flask de autenticação;
    - valida IDs básicos antes da gravação;
    - limita os metadados persistidos.
    """

    # ==========================================================
    # EVENTO
    # ==========================================================

    event = (
        event
        or ""
    ).strip()

    if event not in ALLOWED_EVENTS:

        return

    # ==========================================================
    # EMPRESA
    # ==========================================================

    try:

        company_id = int(
            company_id
        )

    except Exception:

        return

    if company_id <= 0:

        return

    # ==========================================================
    # TEAR — OPCIONAL
    # ==========================================================

    if tear_id is not None:

        try:

            tear_id = int(
                tear_id
            )

        except Exception:

            return

        if tear_id <= 0:

            return

    # ==========================================================
    # META
    # ==========================================================

    if meta is None:

        meta = {}

    if not isinstance(
        meta,
        dict
    ):

        meta = {}

    if len(meta) > 30:

        current_app.logger.warning(
            "[ANALYTICS] meta interno excedeu o limite."
        )

        return

    try:

        meta_json = json.dumps(
            meta,
            ensure_ascii=False,
            separators=(
                ",",
                ":"
            ),
        )

    except Exception:

        current_app.logger.warning(
            "[ANALYTICS] meta interno inválido."
        )

        return

    if len(
        meta_json.encode("utf-8")
    ) > 4096:

        current_app.logger.warning(
            "[ANALYTICS] meta interno muito grande."
        )

        return

    # ==========================================================
    # IDENTIFICADOR EXCLUSIVO DE ANALYTICS
    #
    # Nunca utiliza:
    # - request.cookies.get("session")
    # - session.get("_sid")
    # ==========================================================

    analytics_sid = (
        _analytics_session_id()
    )

    # ==========================================================
    # GRAVAÇÃO
    # ==========================================================

    try:

        with db.engine.begin() as conn:

            conn.execute(
                text(
                    """
                    INSERT INTO analytics_events
                        (
                            company_id,
                            tear_id,
                            event,
                            session_id,
                            meta
                        )
                    VALUES
                        (
                            :cid,
                            :tid,
                            :evt,
                            :sid,
                            :meta
                        )
                    """
                ),
                {
                    "cid": company_id,
                    "tid": tear_id,
                    "evt": event,
                    "sid": analytics_sid,
                    "meta": meta_json,
                },
            )

    except Exception:

        current_app.logger.exception(
            "[ANALYTICS] Falha ao registrar evento interno."
        )

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

# --------------------------------------------------------------------
# AcheTece 2.0 - Demanda de Produção
# --------------------------------------------------------------------

class ProductionRequest(db.Model):
    __tablename__ = "production_request"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # Comprador responsável pela demanda
    user_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id"),
        nullable=False,
        index=True
    )

    usuario = db.relationship(
        "Usuario",
        backref=db.backref(
            "production_requests",
            lazy=True
        )
    )

    # Código público da demanda
    # Ex.: ATD-000001
    codigo = db.Column(
        db.String(30),
        unique=True,
        nullable=True,
        index=True
    )

    # --------------------------------------------------------------
    # Produto / especificação
    # --------------------------------------------------------------

    produto = db.Column(
        db.String(120),
        nullable=False
    )

    estrutura_malha = db.Column(
        db.String(120),
        nullable=True
    )

    composicao = db.Column(
        db.String(180),
        nullable=True
    )

    titulo_fio = db.Column(
        db.String(100),
        nullable=True
    )

    gramatura = db.Column(
        db.Integer,
        nullable=True
    )

    # --------------------------------------------------------------
    # Volume / prazo
    # --------------------------------------------------------------

    quantidade_kg = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    data_necessidade = db.Column(
        db.Date,
        nullable=True
    )

    # --------------------------------------------------------------
    # Preferência geográfica
    # --------------------------------------------------------------

    estado_preferencial = db.Column(
        db.String(2),
        nullable=True,
        index=True
    )

    cidade_preferencial = db.Column(
        db.String(100),
        nullable=True
    )

    # --------------------------------------------------------------
    # Escopo do serviço
    # --------------------------------------------------------------

    tipo_servico = db.Column(
        db.String(80),
        nullable=True
    )

    observacoes = db.Column(
        db.Text,
        nullable=True
    )

    # --------------------------------------------------------------
    # Gestão da demanda
    # --------------------------------------------------------------

    status = db.Column(
        db.String(20),
        nullable=False,
        default="rascunho",
        index=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    def __repr__(self):
        return (
            f"<ProductionRequest "
            f"id={self.id} "
            f"codigo={self.codigo} "
            f"status={self.status}>"
        )

# --------------------------------------------------------------------
# AcheTece 2.0 - Requisitos Técnicos da Demanda
# --------------------------------------------------------------------

class DemandTechnicalRequirement(db.Model):
    __tablename__ = "demand_technical_requirement"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # Uma demanda possui um conjunto de requisitos técnicos
    demand_id = db.Column(
        db.Integer,
        db.ForeignKey("production_request.id"),
        nullable=False,
        unique=True,
        index=True
    )

    demanda = db.relationship(
        "ProductionRequest",
        backref=db.backref(
            "technical_requirement",
            uselist=False
        )
    )

    # --------------------------------------------------------------
    # Tipo de equipamento
    # --------------------------------------------------------------

    tipo_tear = db.Column(
        db.String(50),
        nullable=True,
        index=True
    )

    # --------------------------------------------------------------
    # Galga / Finura
    # Permite intervalo, por exemplo 24 até 28
    # --------------------------------------------------------------

    finura_min = db.Column(
        db.Integer,
        nullable=True
    )

    finura_max = db.Column(
        db.Integer,
        nullable=True
    )

    # --------------------------------------------------------------
    # Diâmetro
    # Permite intervalo
    # --------------------------------------------------------------

    diametro_min = db.Column(
        db.Integer,
        nullable=True
    )

    diametro_max = db.Column(
        db.Integer,
        nullable=True
    )

    # --------------------------------------------------------------
    # Alimentadores
    # --------------------------------------------------------------

    alimentadores_min = db.Column(
        db.Integer,
        nullable=True
    )

    # --------------------------------------------------------------
    # Pistas
    # --------------------------------------------------------------

    pistas_cilindro_min = db.Column(
        db.Integer,
        nullable=True
    )

    pistas_disco_min = db.Column(
        db.Integer,
        nullable=True
    )

    # --------------------------------------------------------------
    # Elastano
    #
    # None  = indiferente
    # True  = necessário
    # False = não necessário
    # --------------------------------------------------------------

    elastano_required = db.Column(
        db.Boolean,
        nullable=True
    )

    # --------------------------------------------------------------
    # Informação complementar
    # --------------------------------------------------------------

    observacoes_tecnicas = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Resultado de Matching
# --------------------------------------------------------------------

class DemandMatch(db.Model):
    __tablename__ = "demand_match"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    demand_id = db.Column(
        db.Integer,
        db.ForeignKey("production_request.id"),
        nullable=False,
        index=True
    )

    tear_id = db.Column(
        db.Integer,
        db.ForeignKey("tear.id"),
        nullable=False,
        index=True
    )

    empresa_id = db.Column(
        db.Integer,
        db.ForeignKey("empresa.id"),
        nullable=False,
        index=True
    )

    demanda = db.relationship(
        "ProductionRequest",
        backref=db.backref(
            "matches",
            lazy=True,
            cascade="all, delete-orphan"
        )
    )

    tear = db.relationship(
        "Tear"
    )

    empresa = db.relationship(
        "Empresa"
    )

    # --------------------------------------------------------------
    # Score
    # --------------------------------------------------------------

    score = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        index=True
    )

    # Explicação textual do cálculo.
    # Mantido em texto para facilitar auditoria e leitura do resultado.
    detalhes = db.Column(
        db.Text,
        nullable=True
    )

    # ativo | descartado | selecionado
    status = db.Column(
        db.String(20),
        nullable=False,
        default="ativo",
        index=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # Uma demanda não pode gerar duas vezes
    # exatamente o mesmo tear.
    __table_args__ = (
        db.UniqueConstraint(
            "demand_id",
            "tear_id",
            name="uq_demand_match_demand_tear"
        ),
    )    

# --------------------------------------------------------------------
# AcheTece 2.0 - Oportunidade para Malharia
# --------------------------------------------------------------------

class Opportunity(db.Model):
    __tablename__ = "opportunity"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # --------------------------------------------------------------
    # Demanda que originou a oportunidade
    # --------------------------------------------------------------

    demand_id = db.Column(
        db.Integer,
        db.ForeignKey("production_request.id"),
        nullable=False,
        index=True
    )

    # --------------------------------------------------------------
    # Malharia que recebeu a oportunidade
    # --------------------------------------------------------------

    empresa_id = db.Column(
        db.Integer,
        db.ForeignKey("empresa.id"),
        nullable=False,
        index=True
    )

    # --------------------------------------------------------------
    # Relacionamentos
    # --------------------------------------------------------------

    demanda = db.relationship(
        "ProductionRequest",
        backref=db.backref(
            "opportunities",
            lazy=True,
            cascade="all, delete-orphan"
        )
    )

    empresa = db.relationship(
        "Empresa",
        backref=db.backref(
            "opportunities",
            lazy=True
        )
    )

    # --------------------------------------------------------------
    # Resumo técnico
    # --------------------------------------------------------------

    best_score = db.Column(
        db.Integer,
        nullable=False,
        default=0,
        index=True
    )

    compatible_tears = db.Column(
        db.Integer,
        nullable=False,
        default=0
    )

    # --------------------------------------------------------------
    # Status
    #
    # nova
    # visualizada
    # interessada
    # recusada
    # inativa
    # --------------------------------------------------------------

    status = db.Column(
        db.String(20),
        nullable=False,
        default="nova",
        index=True
    )

    # --------------------------------------------------------------
    # Datas
    # --------------------------------------------------------------

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    # --------------------------------------------------------------
    # Uma demanda gera somente uma oportunidade por empresa
    # --------------------------------------------------------------

    __table_args__ = (
        db.UniqueConstraint(
            "demand_id",
            "empresa_id",
            name="uq_opportunity_demand_empresa"
        ),
    )

    def __repr__(self):
        return (
            f"<Opportunity "
            f"id={self.id} "
            f"demand_id={self.demand_id} "
            f"empresa_id={self.empresa_id} "
            f"score={self.best_score} "
            f"status={self.status}>"
        )

# --------------------------------------------------------------------
# AcheTece 2.0 - Proposta Comercial
# --------------------------------------------------------------------

class Proposal(db.Model):
    __tablename__ = "proposal"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # --------------------------------------------------------------
    # Oportunidade que originou a proposta
    # --------------------------------------------------------------

    opportunity_id = db.Column(
        db.Integer,
        db.ForeignKey("opportunity.id"),
        nullable=False,
        unique=True,
        index=True
    )

    # --------------------------------------------------------------
    # Demanda
    # --------------------------------------------------------------

    demand_id = db.Column(
        db.Integer,
        db.ForeignKey("production_request.id"),
        nullable=False,
        index=True
    )

    # --------------------------------------------------------------
    # Malharia
    # --------------------------------------------------------------

    empresa_id = db.Column(
        db.Integer,
        db.ForeignKey("empresa.id"),
        nullable=False,
        index=True
    )

    # --------------------------------------------------------------
    # Relacionamentos
    # --------------------------------------------------------------

    oportunidade = db.relationship(
        "Opportunity",
        backref=db.backref(
            "proposal",
            uselist=False
        )
    )

    demanda = db.relationship(
        "ProductionRequest",
        backref=db.backref(
            "proposals",
            lazy=True
        )
    )

    empresa = db.relationship(
        "Empresa",
        backref=db.backref(
            "proposals",
            lazy=True
        )
    )

    # --------------------------------------------------------------
    # Condições comerciais
    # --------------------------------------------------------------

    quantidade_kg = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    preco_por_kg = db.Column(
        db.Numeric(12, 4),
        nullable=False
    )

    prazo_dias = db.Column(
        db.Integer,
        nullable=False
    )

    validade_dias = db.Column(
        db.Integer,
        nullable=False,
        default=7
    )

    condicoes_pagamento = db.Column(
        db.String(255),
        nullable=True
    )

    observacoes = db.Column(
        db.Text,
        nullable=True
    )

    # --------------------------------------------------------------
    # Status
    #
    # rascunho
    # enviada
    # aceita
    # recusada
    # cancelada
    # --------------------------------------------------------------

    status = db.Column(
        db.String(20),
        nullable=False,
        default="rascunho",
        index=True
    )

    # --------------------------------------------------------------
    # Datas
    # --------------------------------------------------------------

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    sent_at = db.Column(
        db.DateTime,
        nullable=True
    )

    def __repr__(self):
        return (
            f"<Proposal "
            f"id={self.id} "
            f"opportunity_id={self.opportunity_id} "
            f"demand_id={self.demand_id} "
            f"empresa_id={self.empresa_id} "
            f"status={self.status}>"
        )

# --------------------------------------------------------------------
# AcheTece 2.0 - Histórico de Interações da Proposta
# --------------------------------------------------------------------

class ProposalInteraction(db.Model):
    __tablename__ = "proposal_interaction"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    proposal_id = db.Column(
        db.Integer,
        db.ForeignKey("proposal.id"),
        nullable=False,
        index=True
    )

    proposta = db.relationship(
        "Proposal",
        backref=db.backref(
            "interactions",
            lazy=True,
            cascade="all, delete-orphan"
        )
    )

    # comprador | malharia | sistema
    actor_role = db.Column(
        db.String(20),
        nullable=False,
        index=True
    )

    # proposta_enviada
    # ajuste_solicitado
    # proposta_reenviada
    # aceita
    # recusada
    action = db.Column(
        db.String(30),
        nullable=False,
        index=True
    )

    message = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    def __repr__(self):
        return (
            f"<ProposalInteraction "
            f"id={self.id} "
            f"proposal_id={self.proposal_id} "
            f"action={self.action}>"
        )

# --------------------------------------------------------------------
# AcheTece 2.0 - Pedido / Order
# --------------------------------------------------------------------

class Order(db.Model):
    __tablename__ = "marketplace_order"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    # --------------------------------------------------------------
    # Código público do pedido
    #
    # Exemplo:
    # ATP-000001
    # --------------------------------------------------------------

    codigo = db.Column(
        db.String(30),
        unique=True,
        nullable=True,
        index=True
    )

    # --------------------------------------------------------------
    # Proposta que originou o pedido
    #
    # Uma proposta aceita pode gerar somente um pedido.
    # --------------------------------------------------------------

    proposal_id = db.Column(
        db.Integer,
        db.ForeignKey("proposal.id"),
        nullable=False,
        unique=True,
        index=True
    )

    # --------------------------------------------------------------
    # Demanda
    # --------------------------------------------------------------

    demand_id = db.Column(
        db.Integer,
        db.ForeignKey("production_request.id"),
        nullable=False,
        index=True
    )

    # --------------------------------------------------------------
    # Comprador
    # --------------------------------------------------------------

    buyer_user_id = db.Column(
        db.Integer,
        db.ForeignKey("usuario.id"),
        nullable=False,
        index=True
    )

    # --------------------------------------------------------------
    # Malharia
    # --------------------------------------------------------------

    empresa_id = db.Column(
        db.Integer,
        db.ForeignKey("empresa.id"),
        nullable=False,
        index=True
    )

    # --------------------------------------------------------------
    # Relacionamentos
    # --------------------------------------------------------------

    proposta = db.relationship(
        "Proposal",
        backref=db.backref(
            "order",
            uselist=False
        )
    )

    demanda = db.relationship(
        "ProductionRequest",
        backref=db.backref(
            "orders",
            lazy=True
        )
    )

    comprador = db.relationship(
        "Usuario",
        backref=db.backref(
            "buyer_orders",
            lazy=True
        )
    )

    empresa = db.relationship(
        "Empresa",
        backref=db.backref(
            "received_orders",
            lazy=True
        )
    )

    # --------------------------------------------------------------
    # Snapshot das condições comerciais
    #
    # IMPORTANTE:
    # Copiamos os valores da proposta para o pedido.
    #
    # Assim, mesmo que no futuro exista revisão de proposta,
    # o pedido mantém as condições que foram aceitas.
    # --------------------------------------------------------------

    quantidade_kg = db.Column(
        db.Numeric(12, 2),
        nullable=False
    )

    preco_por_kg = db.Column(
        db.Numeric(12, 4),
        nullable=False
    )

    valor_total = db.Column(
        db.Numeric(14, 2),
        nullable=False
    )

    prazo_dias = db.Column(
        db.Integer,
        nullable=False
    )

    condicoes_pagamento = db.Column(
        db.String(255),
        nullable=True
    )

    observacoes = db.Column(
        db.Text,
        nullable=True
    )

    # --------------------------------------------------------------
    # Status do pedido
    #
    # aguardando_confirmacao
    # confirmado
    # em_producao
    # concluido
    # entregue
    # cancelado
    # --------------------------------------------------------------

    status = db.Column(
        db.String(30),
        nullable=False,
        default="aguardando_confirmacao",
        index=True
    )

    # --------------------------------------------------------------
    # Datas
    # --------------------------------------------------------------

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    updated_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        onupdate=datetime.utcnow
    )

    confirmed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    completed_at = db.Column(
        db.DateTime,
        nullable=True
    )

    def __repr__(self):

        return (
            f"<Order "
            f"id={self.id} "
            f"codigo={self.codigo} "
            f"proposal_id={self.proposal_id} "
            f"status={self.status}>"
        )

# --------------------------------------------------------------------
# AcheTece 2.0 - Histórico Operacional do Pedido
# --------------------------------------------------------------------

class OrderEvent(db.Model):
    __tablename__ = "order_event"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    order_id = db.Column(
        db.Integer,
        db.ForeignKey("marketplace_order.id"),
        nullable=False,
        index=True
    )

    pedido = db.relationship(
        "Order",
        backref=db.backref(
            "events",
            lazy=True,
            cascade="all, delete-orphan"
        )
    )

    # malharia | comprador | sistema
    actor_role = db.Column(
        db.String(20),
        nullable=False,
        index=True
    )

    # pedido_criado
    # pedido_confirmado
    # producao_iniciada
    # producao_concluida
    # entrega_confirmada
    # pedido_cancelado
    action = db.Column(
        db.String(40),
        nullable=False,
        index=True
    )

    status_anterior = db.Column(
        db.String(30),
        nullable=True
    )

    status_novo = db.Column(
        db.String(30),
        nullable=False
    )

    message = db.Column(
        db.Text,
        nullable=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow
    )

    def __repr__(self):

        return (
            f"<OrderEvent "
            f"id={self.id} "
            f"order_id={self.order_id} "
            f"action={self.action}>"
        )

# --------------------------------------------------------------------
# AcheTece 2.0 - Histórico cronológico do Pedido
# --------------------------------------------------------------------

def _montar_historico_operacional_pedido(pedido):

    if not pedido:

        return []

    # ==============================================================
    # EVENTOS REAIS GRAVADOS NO ORDER_EVENT
    # ==============================================================

    eventos = (
        OrderEvent.query
        .filter_by(
            order_id=pedido.id
        )
        .order_by(
            OrderEvent.created_at.asc(),
            OrderEvent.id.asc()
        )
        .all()
    )

    acoes_existentes = {
        (
            evento.action
            or ""
        ).strip().lower()

        for evento in eventos
    }

    # ==============================================================
    # NOMES AMIGÁVEIS
    # ==============================================================

    rotulos = {

        "pedido_criado":
            "Pedido criado",

        "pedido_confirmado":
            "Pedido confirmado",

        "producao_iniciada":
            "Produção iniciada",

        "producao_concluida":
            "Produção concluída",

        "entrega_confirmada":
            "Entrega confirmada",

        "pedido_cancelado":
            "Pedido cancelado",
    }

    atores = {

        "sistema":
            "AcheTece",

        "malharia":
            "Malharia",

        "comprador":
            "Comprador",
    }

    historico = []

    # ==============================================================
    # PEDIDOS LEGADOS
    #
    # Os primeiros pedidos foram criados antes de o OrderEvent
    # registrar "pedido_criado" e "pedido_confirmado".
    #
    # Utilizamos as datas existentes no próprio Order para que
    # o histórico visual permaneça completo.
    # ==============================================================

    if (
        "pedido_criado"
        not in acoes_existentes
        and pedido.created_at
    ):

        historico.append(
            {
                "action":
                    "pedido_criado",

                "label":
                    "Pedido criado",

                "actor_label":
                    "AcheTece",

                "created_at":
                    pedido.created_at,

                "message":
                    (
                        f"O pedido {pedido.codigo} "
                        "foi criado a partir da proposta aceita."
                    ),

                "reconstruido":
                    True,
            }
        )

    if (
        "pedido_confirmado"
        not in acoes_existentes
        and pedido.confirmed_at
    ):

        historico.append(
            {
                "action":
                    "pedido_confirmado",

                "label":
                    "Pedido confirmado",

                "actor_label":
                    "Malharia",

                "created_at":
                    pedido.confirmed_at,

                "message":
                    (
                        f"A malharia confirmou o recebimento "
                        f"do pedido {pedido.codigo}."
                    ),

                "reconstruido":
                    True,
            }
        )

    # --------------------------------------------------------------
    # Fallback adicional para produção concluída em registros antigos
    # --------------------------------------------------------------

    if (
        "producao_concluida"
        not in acoes_existentes
        and pedido.completed_at
    ):

        historico.append(
            {
                "action":
                    "producao_concluida",

                "label":
                    "Produção concluída",

                "actor_label":
                    "Malharia",

                "created_at":
                    pedido.completed_at,

                "message":
                    (
                        f"A produção do pedido "
                        f"{pedido.codigo} foi concluída."
                    ),

                "reconstruido":
                    True,
            }
        )

    # ==============================================================
    # EVENTOS REAIS
    # ==============================================================

    for evento in eventos:

        acao = (
            evento.action
            or ""
        ).strip().lower()

        ator = (
            evento.actor_role
            or ""
        ).strip().lower()

        historico.append(
            {
                "action":
                    acao,

                "label":
                    rotulos.get(
                        acao,
                        acao.replace(
                            "_",
                            " "
                        ).capitalize()
                    ),

                "actor_label":
                    atores.get(
                        ator,
                        ator.capitalize()
                        if ator
                        else "Sistema"
                    ),

                "created_at":
                    evento.created_at,

                "message":
                    evento.message,

                "reconstruido":
                    False,
            }
        )

    # ==============================================================
    # ORDEM CRONOLÓGICA
    # ==============================================================

    historico.sort(
        key=lambda item: (
            item.get("created_at")
            or datetime.min
        )
    )

    return historico

# --------------------------------------------------------------------
# AcheTece 2.0 - Motor de Matching Técnico
# --------------------------------------------------------------------

def _normalizar_tipo_tear(valor):
    """
    Normaliza diferentes formas de cadastro para:
    MONO | DUPLA
    """

    s = (valor or "").strip().upper()

    if s.startswith("MONO"):
        return "MONO"

    if s.startswith("DUPLA"):
        return "DUPLA"

    return s


def _tear_tem_elastano(tear):
    """
    Interpreta os diferentes formatos já existentes
    no cadastro de teares.
    """

    raw = getattr(tear, "elastano", None)

    if raw is None:
        raw = getattr(
            tear,
            "kit_elastano",
            None
        )

    if isinstance(raw, bool):
        return raw

    s = (
        str(raw or "")
        .strip()
        .lower()
    )

    return s in {
        "sim",
        "s",
        "true",
        "t",
        "on",
        "1",
        "com",
        "tem",
        "yes",
        "y"
    }


def _valor_dentro_intervalo(
    valor,
    minimo=None,
    maximo=None
):
    """
    Verifica se valor está dentro dos limites configurados.
    """

    if valor is None:
        return False

    try:
        valor = int(valor)
    except Exception:
        return False

    if minimo is not None and valor < minimo:
        return False

    if maximo is not None and valor > maximo:
        return False

    return True


def _texto_match(valor):
    """
    Normalização simples para comparação de cidade/estado.
    """

    return (
        str(valor or "")
        .strip()
        .casefold()
    )


def _calcular_match_v1(
    demanda,
    requisito,
    tear,
    empresa
):
    """
    Retorna:

        compativel: bool
        score: int 0-100
        detalhes: list[str]

    Critérios técnicos são eliminatórios.
    Localização gera pontuação, mas não elimina.
    """

    pontos = 0
    max_pontos = 0

    detalhes = []

    # ==============================================================
    # 1. TIPO DE TEAR - 30 pontos
    # ==============================================================

    if requisito.tipo_tear:

        max_pontos += 30

        esperado = _normalizar_tipo_tear(
            requisito.tipo_tear
        )

        encontrado = _normalizar_tipo_tear(
            tear.tipo
        )

        if encontrado != esperado:

            return (
                False,
                0,
                [
                    f"Tipo incompatível: "
                    f"necessário {esperado}, "
                    f"tear {encontrado or 'não informado'}."
                ]
            )

        pontos += 30

        detalhes.append(
            f"✓ Tipo {encontrado}"
        )

    # ==============================================================
    # 2. FINURA / GALGA - 20 pontos
    # ==============================================================

    if (
        requisito.finura_min is not None
        or requisito.finura_max is not None
    ):

        max_pontos += 20

        finura_tear = getattr(
            tear,
            "finura",
            None
        )

        if not _valor_dentro_intervalo(
            finura_tear,
            requisito.finura_min,
            requisito.finura_max
        ):

            return (
                False,
                0,
                [
                    f"Finura incompatível: "
                    f"tear {finura_tear or 'não informado'}."
                ]
            )

        pontos += 20

        detalhes.append(
            f"✓ Finura {finura_tear}"
        )

    # ==============================================================
    # 3. DIÂMETRO - 15 pontos
    # ==============================================================

    if (
        requisito.diametro_min is not None
        or requisito.diametro_max is not None
    ):

        max_pontos += 15

        diametro_tear = getattr(
            tear,
            "diametro",
            None
        )

        if not _valor_dentro_intervalo(
            diametro_tear,
            requisito.diametro_min,
            requisito.diametro_max
        ):

            return (
                False,
                0,
                [
                    f"Diâmetro incompatível: "
                    f"tear {diametro_tear or 'não informado'}."
                ]
            )

        pontos += 15

        detalhes.append(
            f"✓ Diâmetro {diametro_tear}\""
        )

    # ==============================================================
    # 4. ALIMENTADORES - 5 pontos
    # ==============================================================

    if requisito.alimentadores_min is not None:

        max_pontos += 5

        alimentadores = getattr(
            tear,
            "alimentadores",
            None
        )

        try:
            alimentadores_ok = (
                alimentadores is not None
                and int(alimentadores)
                >= int(requisito.alimentadores_min)
            )
        except Exception:
            alimentadores_ok = False

        if not alimentadores_ok:

            return (
                False,
                0,
                [
                    f"Alimentadores insuficientes: "
                    f"tear {alimentadores or 'não informado'}."
                ]
            )

        pontos += 5

        detalhes.append(
            f"✓ {alimentadores} alimentadores"
        )

    # ==============================================================
    # 5. PISTAS - 5 pontos
    # ==============================================================

    usa_pistas = (
        requisito.pistas_cilindro_min is not None
        or requisito.pistas_disco_min is not None
    )

    if usa_pistas:

        max_pontos += 5

        if requisito.pistas_cilindro_min is not None:

            pistas_cil = getattr(
                tear,
                "pistas_cilindro",
                None
            )

            try:
                ok = (
                    pistas_cil is not None
                    and int(pistas_cil)
                    >= int(
                        requisito.pistas_cilindro_min
                    )
                )
            except Exception:
                ok = False

            if not ok:

                return (
                    False,
                    0,
                    [
                        "Quantidade de pistas do cilindro "
                        "incompatível."
                    ]
                )

        if requisito.pistas_disco_min is not None:

            pistas_disco = getattr(
                tear,
                "pistas_disco",
                None
            )

            try:
                ok = (
                    pistas_disco is not None
                    and int(pistas_disco)
                    >= int(
                        requisito.pistas_disco_min
                    )
                )
            except Exception:
                ok = False

            if not ok:

                return (
                    False,
                    0,
                    [
                        "Quantidade de pistas do disco "
                        "incompatível."
                    ]
                )

        pontos += 5

        detalhes.append(
            "✓ Pistas compatíveis"
        )

    # ==============================================================
    # 6. ELASTANO - 10 pontos
    #
    # Regra atual:
    # True = obrigatório e eliminatório
    # False/None = não elimina
    # ==============================================================

    if requisito.elastano_required is True:

        max_pontos += 10

        if not _tear_tem_elastano(
            tear
        ):

            return (
                False,
                0,
                [
                    "Elastano obrigatório, "
                    "mas o tear não possui o recurso."
                ]
            )

        pontos += 10

        detalhes.append(
            "✓ Elastano disponível"
        )

    # ==============================================================
    # 7. ESTADO PREFERENCIAL - 10 pontos
    #
    # Preferência: NÃO elimina.
    # ==============================================================

    if demanda.estado_preferencial:

        max_pontos += 10

        estado_demanda = _texto_match(
            demanda.estado_preferencial
        )

        estado_empresa = _texto_match(
            empresa.estado
        )

        if estado_demanda == estado_empresa:

            pontos += 10

            detalhes.append(
                f"✓ Estado preferencial: "
                f"{empresa.estado}"
            )

        else:

            detalhes.append(
                f"• Fora do estado preferencial: "
                f"{empresa.estado or 'não informado'}"
            )

    # ==============================================================
    # 8. CIDADE PREFERENCIAL - 5 pontos
    #
    # Preferência: NÃO elimina.
    # ==============================================================

    if demanda.cidade_preferencial:

        max_pontos += 5

        cidade_demanda = _texto_match(
            demanda.cidade_preferencial
        )

        cidade_empresa = _texto_match(
            empresa.cidade
        )

        if cidade_demanda == cidade_empresa:

            pontos += 5

            detalhes.append(
                f"✓ Cidade preferencial: "
                f"{empresa.cidade}"
            )

        else:

            detalhes.append(
                f"• Cidade diferente: "
                f"{empresa.cidade or 'não informada'}"
            )

    # ==============================================================
    # SCORE NORMALIZADO
    # ==============================================================

    if max_pontos <= 0:

        score = 100

    else:

        score = round(
            (pontos / max_pontos)
            * 100
        )

    return (
        True,
        int(score),
        detalhes
    )

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

# ==============================================================
# RECUPERAÇÃO DE SENHA — TOKEN SERVER-SIDE
# ==============================================================
#
# Cada solicitação de recuperação cria um token aleatório.
#
# O token real é enviado somente por e-mail.
# No PostgreSQL armazenamos apenas o hash do token.
#
# account_type:
#   cliente  → Usuario
#   malharia → Empresa
#
# account_id identifica o registro correspondente ao tipo.
#
# used_at preenchido significa que o token:
# - já foi utilizado; ou
# - foi invalidado/substituído.
# ==============================================================

class PasswordResetToken(db.Model):

    __tablename__ = "password_reset_token"

    id = db.Column(
        db.Integer,
        primary_key=True
    )

    token_hash = db.Column(
        db.String(64),
        unique=True,
        nullable=False,
        index=True
    )

    email = db.Column(
        db.String(255),
        nullable=False,
        index=True
    )

    # cliente | malharia
    account_type = db.Column(
        db.String(20),
        nullable=False,
        index=True
    )

    # Usuario.id para cliente
    # Empresa.id para malharia
    account_id = db.Column(
        db.Integer,
        nullable=False,
        index=True
    )

    created_at = db.Column(
        db.DateTime,
        nullable=False,
        default=datetime.utcnow,
        index=True
    )

    expires_at = db.Column(
        db.DateTime,
        nullable=False,
        index=True
    )

    used_at = db.Column(
        db.DateTime,
        nullable=True,
        index=True
    )

    request_ip = db.Column(
        db.String(64),
        nullable=True
    )

    user_agent = db.Column(
        db.String(255),
        nullable=True
    )

    def __repr__(self):

        return (
            f"<PasswordResetToken "
            f"id={self.id} "
            f"account_type={self.account_type} "
            f"account_id={self.account_id} "
            f"used={self.used_at is not None}>"
        )


# === Helpers de autenticação/empresa =========================================
# flask_login é opcional no projeto; faça import seguro
try:
    from flask_login import current_user  # type: ignore
except Exception:  # noqa: E722
    current_user = None  # fallback silencioso

# ==============================================================
# IDENTIFICAÇÃO SEGURA DO IP DO CLIENTE
# ==============================================================

def _client_ip():
    """
    Retorna o IP real do cliente para auditoria e segurança.

    Em produção/staging no Render:
    - prioriza CF-Connecting-IP, definido pela infraestrutura;
    - não confia diretamente em X-Forwarded-For;
    - valida o valor como IPv4 ou IPv6.

    Em desenvolvimento local:
    - usa request.remote_addr como fallback.

    Retorna "unknown" quando nenhum IP válido puder ser obtido.
    """

    candidates = [
        request.headers.get("CF-Connecting-IP"),
        request.remote_addr,
    ]

    for raw_value in candidates:

        value = (
            raw_value
            or ""
        ).strip()

        if not value:
            continue

        # O CF-Connecting-IP esperado deve representar
        # apenas um endereço, não uma cadeia de proxies.
        if "," in value:
            continue

        try:

            ip = ipaddress.ip_address(
                value
            )

            return ip.compressed

        except ValueError:
            continue

    return "unknown"

# ==============================================================
# RATE LIMITING
# ==============================================================
#
# O Flask-Limiter utiliza _client_ip() como chave do cliente.
#
# No staging utilizamos memory:// explicitamente.
# Esse backend é adequado para validação, mas NÃO será a
# configuração definitiva de produção.
#
# Não há limites globais nesta fase.
# Cada endpoint sensível receberá seu próprio limite
# progressivamente nas próximas etapas.
# ==============================================================

RATE_LIMIT_STORAGE_URI = (
    os.getenv("RATELIMIT_STORAGE_URI")
    or "memory://"
)

limiter = Limiter(
    key_func=_client_ip,
    app=app,

    # Nenhum limite global por enquanto.
    default_limits=[],

    # Backend temporário do staging.
    storage_uri=RATE_LIMIT_STORAGE_URI,

    # Estratégia simples e previsível.
    strategy="fixed-window",

    # Inclui os headers de controle na resposta.
    headers_enabled=True,
)

# ==============================================================
# LIMITES COMPARTILHADOS — AUTENTICAÇÃO
# ==============================================================
#
# Solicitação inicial de OTP.
#
# As duas URLs:
#
#   POST /login/codigo
#   POST /login/código
#
# compartilham exatamente o mesmo contador.
#
# Limite:
# 5 solicitações a cada 15 minutos por cliente/IP.
# ==============================================================

otp_send_limit = limiter.shared_limit(
    "5 per 15 minutes",
    scope="otp-send"
)

# ==============================================================
# RATE LIMIT — COMPRADOR AUTENTICADO
# ==============================================================

def _cliente_rate_limit_key():
    """
    Usa o ID do comprador autenticado como chave de rate limit.

    Se não houver sessão válida, utiliza o IP como fallback.
    """

    user_id = session.get(
        "user_id"
    )

    if user_id:

        return (
            f"cliente:{user_id}"
        )

    return (
        f"ip:{_client_ip()}"
    )

# ==============================================================
# RATE LIMIT — MALHARIA AUTENTICADA
# ==============================================================

def _malharia_rate_limit_key():
    """
    Usa o ID da malharia autenticada como chave de Rate Limit.

    Assim:
    - uma empresa não compartilha o contador com outra;
    - diferentes dispositivos da mesma conta compartilham
      o mesmo limite;
    - se não existir sessão válida, o IP é utilizado
      como fallback seguro.
    """

    empresa_id = session.get(
        "empresa_id"
    )

    if empresa_id:

        return (
            f"malharia:{empresa_id}"
        )

    return (
        f"ip:{_client_ip()}"
    )

# ==============================================================
# RATE LIMIT — ADMINISTRADOR AUTENTICADO
# ==============================================================

def _admin_rate_limit_key():
    """
    Usa a identidade do administrador autenticado
    como chave do Rate Limit.

    Se não existir sessão administrativa válida,
    utiliza o IP como fallback.
    """

    admin_autenticado = (
        session.get(
            "admin_authenticated"
        )
        is True
    )

    admin_email = (
        session.get(
            "admin_email"
        )
        or ""
    ).strip().lower()

    if (
        admin_autenticado
        and admin_email
    ):

        return (
            f"admin:{admin_email}"
        )

    return (
        f"ip:{_client_ip()}"
    )

# ==============================================================
# EXCLUSÃO DE MALHARIA — HELPERS DE SEGURANÇA
# ==============================================================

def _empresa_historico_marketplace(
    empresa_id: int
):
    """
    Verifica se a malharia possui histórico dentro
    do marketplace AcheTece 2.0.

    Enquanto existir qualquer um destes registros,
    a Empresa não poderá ser apagada definitivamente:

    - DemandMatch
    - Opportunity
    - Proposal
    - Order

    Retorna:
        {
            "matches": int,
            "oportunidades": int,
            "propostas": int,
            "pedidos": int,
            "bloqueia_exclusao": bool
        }
    """

    try:

        empresa_id = int(
            empresa_id
        )

    except Exception:

        empresa_id = 0

    if empresa_id <= 0:

        return {
            "matches": 0,
            "oportunidades": 0,
            "propostas": 0,
            "pedidos": 0,
            "bloqueia_exclusao": True,
        }

    matches = (
        DemandMatch.query
        .filter(
            DemandMatch.empresa_id
            == empresa_id
        )
        .count()
    )

    oportunidades = (
        Opportunity.query
        .filter(
            Opportunity.empresa_id
            == empresa_id
        )
        .count()
    )

    propostas = (
        Proposal.query
        .filter(
            Proposal.empresa_id
            == empresa_id
        )
        .count()
    )

    pedidos = (
        Order.query
        .filter(
            Order.empresa_id
            == empresa_id
        )
        .count()
    )

    bloqueia = any(
        (
            matches,
            oportunidades,
            propostas,
            pedidos,
        )
    )

    return {
        "matches": matches,
        "oportunidades": oportunidades,
        "propostas": propostas,
        "pedidos": pedidos,
        "bloqueia_exclusao": bloqueia,
    }


def _usuario_tem_historico_comprador(
    usuario_id: int
) -> bool:
    """
    Evita apagar um Usuario vinculado à malharia
    caso esse mesmo registro possua histórico de comprador.

    Protegemos:
    - ClienteProfile
    - ProductionRequest
    - pedidos em que seja buyer_user_id
    """

    try:

        usuario_id = int(
            usuario_id
        )

    except Exception:

        return True

    if usuario_id <= 0:

        return True

    perfil = (
        ClienteProfile.query
        .filter(
            ClienteProfile.user_id
            == usuario_id
        )
        .first()
    )

    if perfil:

        return True

    demanda = (
        ProductionRequest.query
        .filter(
            ProductionRequest.user_id
            == usuario_id
        )
        .first()
    )

    if demanda:

        return True

    pedido_comprador = (
        Order.query
        .filter(
            Order.buyer_user_id
            == usuario_id
        )
        .first()
    )

    if pedido_comprador:

        return True

    return False


def _remover_arquivos_avatar_empresa(
    empresa_id: int
):
    """
    Remove arquivos conhecidos de avatar após a Empresa
    já ter sido excluída com sucesso do banco.

    Falha na remoção do arquivo não desfaz a transação
    do banco.
    """

    try:

        empresa_id = int(
            empresa_id
        )

    except Exception:

        return

    if empresa_id <= 0:

        return

    diretorios = [
        Path(
            current_app.static_folder
        ) / "avatars",

        Path(
            current_app.static_folder
        ) / "uploads" / "avatars",

        Path(
            current_app.static_folder
        ) / "uploads" / "perfil",
    ]

    padroes = [
        f"empresa_{empresa_id}.*",
        f"emp_{empresa_id}.*",
        f".empresa_{empresa_id}.*.tmp.webp",
    ]

    for diretorio in diretorios:

        try:

            if not diretorio.exists():

                continue

            for padrao in padroes:

                for arquivo in diretorio.glob(
                    padrao
                ):

                    try:

                        if arquivo.is_file():

                            arquivo.unlink()

                    except OSError:

                        current_app.logger.warning(
                            (
                                "[ADMIN_DELETE] "
                                "Não foi possível remover "
                                "arquivo de avatar. "
                                f"empresa_id={empresa_id}"
                            )
                        )

        except Exception:

            current_app.logger.exception(
                (
                    "[ADMIN_DELETE] "
                    "Falha ao limpar arquivos da empresa. "
                    f"empresa_id={empresa_id}"
                )
            )


def _excluir_empresa_sem_historico(
    empresa
):
    """
    Executa a exclusão definitiva de uma Empresa
    SOMENTE depois de a ausência de histórico comercial
    ter sido validada.

    Remove:
    - progresso de treinamento;
    - analytics;
    - tokens de recuperação da malharia;
    - teares;
    - Empresa;
    - Usuario vinculado, somente quando ele não possui
      histórico como comprador.

    Toda a parte de banco é executada em uma única transação.
    """

    if not isinstance(
        empresa,
        Empresa
    ):

        raise ValueError(
            "Empresa inválida para exclusão."
        )

    empresa_id = int(
        empresa.id
    )

    email_empresa = (
        empresa.email
        or ""
    ).strip().lower()

    usuario = None

    if getattr(
        empresa,
        "user_id",
        None
    ):

        usuario = db.session.get(
            Usuario,
            empresa.user_id
        )

    usuario_removido = False
    usuario_preservado = False

    try:

        # ======================================================
        # CONFIRMA NOVAMENTE O HISTÓRICO
        #
        # Fazemos a verificação também aqui para que nenhum
        # chamador consiga pular a proteção.
        # ======================================================

        historico = (
            _empresa_historico_marketplace(
                empresa_id
            )
        )

        if historico[
            "bloqueia_exclusao"
        ]:

            raise RuntimeError(
                "EMPRESA_POSSUI_HISTORICO"
            )

        
        # ======================================================
        # ANALYTICS
        #
        # analytics_events é tabela auxiliar e não possui FK ORM.
        # ======================================================

        try:

            if inspect(
                db.engine
            ).has_table(
                "analytics_events"
            ):

                db.session.execute(
                    text(
                        """
                        DELETE FROM analytics_events
                        WHERE company_id = :empresa_id
                        """
                    ),
                    {
                        "empresa_id":
                            empresa_id
                    }
                )

        except Exception:

            current_app.logger.exception(
                (
                    "[ADMIN_DELETE] "
                    "Falha ao limpar analytics. "
                    f"empresa_id={empresa_id}"
                )
            )

            raise

        # ======================================================
        # TOKENS DA MALHARIA
        # ======================================================

        PasswordResetToken.query.filter(
            PasswordResetToken.account_type
            == "malharia",

            PasswordResetToken.account_id
            == empresa_id
        ).delete(
            synchronize_session=False
        )

        # ======================================================
        # TEARES
        #
        # Sem DemandMatch, não há referência histórica aos teares.
        # ======================================================

        Tear.query.filter(
            Tear.empresa_id
            == empresa_id
        ).delete(
            synchronize_session=False
        )

        # ======================================================
        # USUARIO VINCULADO
        # ======================================================

        if usuario is not None:

            historico_comprador = (
                _usuario_tem_historico_comprador(
                    usuario.id
                )
            )

            if historico_comprador:

                # ----------------------------------------------
                # Preserva o usuário porque ele também possui
                # identidade/histórico de comprador.
                # ----------------------------------------------

                usuario.role = (
                    "cliente"
                )

                empresa.user_id = (
                    None
                )

                usuario_preservado = True

            else:

                # ----------------------------------------------
                # Tokens que somente fariam sentido para este
                # usuário também podem ser removidos.
                # ----------------------------------------------

                PasswordResetToken.query.filter(
                    PasswordResetToken.account_type
                    == "cliente",

                    PasswordResetToken.account_id
                    == usuario.id
                ).delete(
                    synchronize_session=False
                )

                if email_empresa:

                    OtpToken.query.filter(
                        func.lower(
                            OtpToken.email
                        )
                        == email_empresa
                    ).delete(
                        synchronize_session=False
                    )

                empresa.user_id = (
                    None
                )

                db.session.flush()

        # ======================================================
        # EMPRESA
        # ======================================================

        db.session.delete(
            empresa
        )

        db.session.flush()

        # ======================================================
        # USUARIO
        #
        # Apagamos somente depois de remover Empresa porque
        # Empresa.user_id referencia Usuario.id.
        # ======================================================

        if (
            usuario is not None
            and not usuario_preservado
        ):

            db.session.delete(
                usuario
            )

            usuario_removido = True

        # ======================================================
        # COMMIT ÚNICO
        # ======================================================

        db.session.commit()

    except Exception:

        db.session.rollback()

        raise

    # ==========================================================
    # ARQUIVOS
    #
    # Somente depois do commit do banco.
    # ==========================================================

    _remover_arquivos_avatar_empresa(
        empresa_id
    )

    return {
        "ok": True,
        "empresa_id": empresa_id,
        "usuario_removido": usuario_removido,
        "usuario_preservado": usuario_preservado,
    }

# ==============================================================
# TRATAMENTO GLOBAL — HTTP 429 TOO MANY REQUESTS
# ==============================================================

@app.errorhandler(RateLimitExceeded)
def handle_rate_limit_exceeded(error):
    """
    Resposta centralizada quando um limite de requisições
    do Flask-Limiter é excedido.

    Para APIs, retorna JSON.

    Para páginas normais, exibe uma mensagem amigável
    e mantém o status HTTP 429.
    """

    retry_after = (
        getattr(
            error,
            "retry_after",
            None
        )
        or request.headers.get(
            "Retry-After"
        )
        or 60
    )

    app.logger.warning(
        "[SECURITY][RATE_LIMIT] "
        "path=%s endpoint=%s ip=%s limite=%s",
        request.path,
        request.endpoint,
        _client_ip(),
        getattr(
            error,
            "description",
            "excedido"
        ),
    )

    wants_json = (
        request.path.startswith("/api/")
        or request.is_json
        or (
            request.accept_mimetypes.best
            == "application/json"
        )
    )

    if wants_json:

        response = jsonify(
            {
                "ok": False,
                "error": "rate_limit_exceeded",
                "message": (
                    "Muitas tentativas em pouco tempo. "
                    "Aguarde antes de tentar novamente."
                ),
                "retry_after": retry_after,
            }
        )

        response.status_code = 429

        return response

    return render_template(
        "429.html",
        retry_after=retry_after,
    ), 429

# ==============================================================
# TESTE DE RATE LIMIT — SOMENTE STAGING / DEMO
# ==============================================================
#
# Esta rota existe exclusivamente para validar a infraestrutura
# do Flask-Limiter sem utilizar login, OTP, banco ou outras
# funções reais do marketplace.
#
# Limite:
# 3 requisições por minuto por cliente/IP.
#
# Com DEMO_MODE=False a rota retorna 404.
# ==============================================================

@app.get(
    "/__security/rate-limit-test",
    endpoint="rate_limit_test"
)
@limiter.limit("3 per minute")
def rate_limit_test():
    """
    Endpoint temporário para validação do rate limiting.

    Resultado esperado no staging:
    1ª chamada -> 200
    2ª chamada -> 200
    3ª chamada -> 200
    4ª chamada -> 429

    Esta rota será removida após a conclusão dos testes
    de segurança da etapa 9A.3C.
    """

    if not _env_bool(
        "DEMO_MODE",
        False
    ):
        abort(404)

    return {
        "ok": True,
        "message": "Rate limit test",
    }, 200

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

    @wraps(f)
    def wrapper(
        *args,
        **kwargs
    ):

        # ==========================================================
        # EMPRESA DA SESSÃO
        # ==========================================================

        (
            empresa,
            usuario
        ) = _get_empresa_usuario_da_sessao()

        if not empresa:

            flash(
                "Faça login para continuar.",
                "warning"
            )

            return redirect(
                url_for("login")
            )

        # ==========================================================
        # REGRA CENTRAL
        # ==========================================================

        (
            situacao,
            mensagem
        ) = _avaliar_acesso_conta(
            "malharia",
            empresa,
            usuario
        )

        if situacao == "ok":

            return f(
                *args,
                **kwargs
            )

        if situacao == "assinatura_inativa":

            flash(
                mensagem,
                "warning"
            )

            return redirect(
                url_for(
                    "planos"
                )
            )

        session.clear()

        flash(
            mensagem
            or "Acesso não autorizado.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    return wrapper

# ----------------------------------------------------------------------
# AcheTece 2.0 - Endpoints protegidos da Malharia
# ----------------------------------------------------------------------

MALHARIA_ENDPOINTS_PROTEGIDOS = {

    # Portal
    "painel_malharia",

    # Marketplace
    "minhas_oportunidades",
    "analisar_oportunidade",
    "oportunidade_interesse",
    "oportunidade_recusar",
    "enviar_proposta",
    "propostas_malharia",
    "meus_pedidos_malharia",
    "detalhe_pedido_malharia",
    "confirmar_pedido_malharia",
    "iniciar_producao_malharia",
    "concluir_producao_malharia",

    # Parque produtivo
    "cadastrar_teares",
    "teares_form",
    "editar_tear",
    "excluir_tear",

    # Perfil
    "editar_empresa",
    "perfil_foto_upload",

    # Performance
    "performance_acesso",
    
}


@app.before_request
def proteger_area_malharia():

    endpoint = (
        request.endpoint
        or ""
    )

    if (
        endpoint
        not in MALHARIA_ENDPOINTS_PROTEGIDOS
    ):

        return None

    # ==============================================================
    # IDENTIDADE
    # ==============================================================

    (
        empresa,
        usuario
    ) = _get_empresa_usuario_da_sessao()

    if not empresa:

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # REGRA CENTRAL
    # ==============================================================

    (
        situacao,
        mensagem
    ) = _avaliar_acesso_conta(
        "malharia",
        empresa,
        usuario
    )

    if situacao == "ok":

        return None

    # --------------------------------------------------------------
    # Assinatura vencida
    #
    # NÃO limpamos a sessão porque ela é necessária para
    # identificar a empresa no fluxo de renovação.
    # --------------------------------------------------------------

    if situacao == "assinatura_inativa":

        flash(
            mensagem,
            "warning"
        )

        return redirect(
            url_for("planos")
        )

    # --------------------------------------------------------------
    # Conta inválida/desativada
    # --------------------------------------------------------------

    session.clear()

    flash(
        mensagem
        or "Acesso não autorizado.",
        "warning"
    )

    return redirect(
        url_for("login")
    )

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

# ==============================================================
# ACCESS LOG SEGURO
# ==============================================================
#
# Evita registrar URLs completas ou parâmetros sensíveis.
#
# Exemplo seguro:
#
# [ACCESS]
# method=GET
# endpoint=redefinir_senha
# status=200
# ip=...
#
# O token presente na URL nunca é escrito por este logger.
# ==============================================================

@app.after_request
def _safe_access_log(response):

    try:

        endpoint = (
            request.endpoint
            or "unknown"
        )

        current_app.logger.info(
            (
                "[ACCESS] "
                f"method={request.method} "
                f"endpoint={endpoint} "
                f"status={response.status_code} "
                f"ip={_client_ip()}"
            )
        )

    except Exception:

        # Log nunca deve impedir a resposta ao usuário.
        pass

    return response

# ==============================================================
# CSP NONCE
# ==============================================================

def _get_csp_nonce():
    """
    Retorna um nonce aleatório único para a requisição atual.

    O mesmo valor é utilizado:
    - nos templates Jinja;
    - no header Content-Security-Policy.

    Uma nova requisição recebe um novo nonce.
    """
    nonce = getattr(g, "_csp_nonce", None)

    if not nonce:
        nonce = secrets.token_urlsafe(24)
        g._csp_nonce = nonce

    return nonce


@app.context_processor
def _inject_csp_nonce():
    """
    Disponibiliza {{ csp_nonce }} automaticamente
    em todos os templates Jinja.
    """
    return {
        "csp_nonce": _get_csp_nonce()
    }


# ==============================================================
# PASSWORD RESET — HEADERS DE PRIVACIDADE
# ==============================================================

@app.after_request
def _password_reset_privacy_headers(response):

    sensitive_endpoints = {
        "redefinir_senha",
        "post_redefinir_senha",
    }

    if request.endpoint in sensitive_endpoints:

        # O token da URL não deve ser enviado como Referer
        # para recursos, páginas ou serviços subsequentes.
        response.headers[
            "Referrer-Policy"
        ] = "no-referrer"

        # Páginas que contêm token de recuperação não devem
        # permanecer armazenadas em caches intermediários.
        response.headers[
            "Cache-Control"
        ] = (
            "no-store, no-cache, "
            "must-revalidate, max-age=0"
        )

        response.headers[
            "Pragma"
        ] = "no-cache"

        response.headers[
            "Expires"
        ] = "0"

    return response
    

# ==============================================================
# HEADERS GLOBAIS DE SEGURANÇA
# ==============================================================

@app.after_request
def _security_headers(resp):
    """
    Aplica headers HTTP globais de segurança.

    A política HSTS permanece com duração curta no staging.

    A Content Security Policy utiliza nonce por requisição
    para autorizar JavaScript inline legítimo do AcheTece.

    JavaScript inline genérico e handlers HTML inline
    não são permitidos.

    CSS inline permanece temporariamente permitido
    por compatibilidade com o front-end atual.
    """

    # ==========================================================
    # HEADERS BÁSICOS DE SEGURANÇA
    # ==========================================================

    # Impede MIME sniffing.
    resp.headers.setdefault(
        "X-Content-Type-Options",
        "nosniff"
    )

    # Proteção contra clickjacking externo.
    resp.headers.setdefault(
        "X-Frame-Options",
        "SAMEORIGIN"
    )

    # Reduz exposição de URL/caminho ao navegar
    # para outro domínio.
    resp.headers.setdefault(
        "Referrer-Policy",
        "strict-origin-when-cross-origin"
    )

    # APIs do navegador que o AcheTece atualmente
    # não necessita utilizar.
    resp.headers.setdefault(
        "Permissions-Policy",
        "camera=(), microphone=(), geolocation=()"
    )

    # ==========================================================
    # HSTS — FASE DE VALIDAÇÃO NO STAGING
    # ==========================================================
    #
    # 300 segundos = 5 minutos.
    #
    # Ainda não utilizamos:
    # - includeSubDomains
    # - preload
    #
    # Esses itens serão avaliados posteriormente
    # antes de qualquer aplicação em produção.
    resp.headers.setdefault(
        "Strict-Transport-Security",
        "max-age=300"
    )

    # ==========================================================
    # CONTENT SECURITY POLICY — NONCE ATIVO
    # ==========================================================
    #
    # Cada requisição possui um nonce aleatório próprio.
    #
    # JavaScript permitido:
    #
    # 1. arquivos JavaScript do próprio AcheTece:
    #       script-src 'self'
    #
    # 2. blocos <script> que possuam:
    #       nonce="{{ csp_nonce }}"
    #
    # Atributos JavaScript inline como:
    #
    # onclick=""
    # onsubmit=""
    # onerror=""
    #
    # são explicitamente bloqueados.
    #
    # CSS inline permanece temporariamente permitido
    # porque o projeto ainda possui vários:
    #
    # <style>
    # style=""
    #
    # Essa parte será tratada posteriormente.
    nonce = _get_csp_nonce()

    csp_policy = (
        "default-src 'self'; "

        # ------------------------------------------------------
        # JAVASCRIPT
        # ------------------------------------------------------
        f"script-src 'self' 'nonce-{nonce}'; "
        "script-src-attr 'none'; "

        # ------------------------------------------------------
        # CSS
        # ------------------------------------------------------
        "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "

        # ------------------------------------------------------
        # FONTES
        # ------------------------------------------------------
        "font-src 'self' https://fonts.gstatic.com; "

        # ------------------------------------------------------
        # IMAGENS
        # ------------------------------------------------------
        "img-src 'self'; "

        # ------------------------------------------------------
        # FETCH / XHR / API
        # ------------------------------------------------------
        "connect-src 'self'; "

        # ------------------------------------------------------
        # IFRAMES
        # ------------------------------------------------------
        #
        # 'self':
        # materiais internos / PDFs do próprio AcheTece.
        #
        # www.google.com:
        # mapa exibido no perfil da malharia.
        "frame-src 'self' https://www.google.com; "

        # ------------------------------------------------------
        # PROTEÇÕES ESTRUTURAIS
        # ------------------------------------------------------
        "object-src 'none'; "
        "base-uri 'self'; "
        "form-action 'self'; "
        "frame-ancestors 'self'; "
    )

    # CSP efetiva.
    #
    # Não usamos mais Content-Security-Policy-Report-Only
    # nesta fase.
    resp.headers["Content-Security-Policy"] = csp_policy

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



# ==============================================================
# ANALYTICS — REGISTRO DE EVENTOS
# ==============================================================

@app.post("/api/track")
@csrf.exempt
@limiter.limit(
    "300 per minute"
)
@limiter.limit(
    "10000 per day"
)
def api_track():
    """
    Registra eventos analíticos públicos do AcheTece.

    Proteções:
    - CSRF exempt somente porque é endpoint técnico de analytics;
    - rate limit por IP;
    - payload máximo específico;
    - somente JSON;
    - evento precisa pertencer a ALLOWED_EVENTS;
    - empresa precisa existir;
    - tear, quando informado, precisa existir e pertencer à empresa;
    - meta precisa ser objeto JSON e ter tamanho limitado;
    - nunca persiste cookie de autenticação.
    """

    # ==========================================================
    # LIMITE ESPECÍFICO DO PAYLOAD
    # ==========================================================

    max_payload_bytes = (
        16 * 1024
    )

    content_length = (
        request.content_length
        or 0
    )

    if content_length > max_payload_bytes:

        return jsonify({
            "ok": False,
            "error": "payload too large"
        }), 413

    # ==========================================================
    # EXIGE JSON
    # ==========================================================

    if not request.is_json:

        return jsonify({
            "ok": False,
            "error": "json required"
        }), 415

    data = request.get_json(
        silent=True
    )

    if not isinstance(
        data,
        dict
    ):

        return jsonify({
            "ok": False,
            "error": "invalid json"
        }), 400

    # ==========================================================
    # EVENTO
    # ==========================================================

    event = (
        data.get("event")
        or ""
    ).strip()

    if event not in ALLOWED_EVENTS:

        return jsonify({
            "ok": False,
            "error": "invalid event"
        }), 400

    # ==========================================================
    # EMPRESA
    # ==========================================================

    company_id_raw = data.get(
        "company_id"
    )

    try:

        company_id = int(
            company_id_raw
        )

    except Exception:

        company_id = 0

    if company_id <= 0:

        return jsonify({
            "ok": False,
            "error": "invalid company"
        }), 400

    empresa = db.session.get(
        Empresa,
        company_id
    )

    if empresa is None:

        return jsonify({
            "ok": False,
            "error": "invalid company"
        }), 400

    # ==========================================================
    # TEAR — OPCIONAL
    # ==========================================================

    tear_id_raw = data.get(
        "tear_id"
    )

    tear_id = None

    if tear_id_raw not in {
        None,
        "",
    }:

        try:

            tear_id = int(
                tear_id_raw
            )

        except Exception:

            tear_id = 0

        if tear_id <= 0:

            return jsonify({
                "ok": False,
                "error": "invalid tear"
            }), 400

        tear = db.session.get(
            Tear,
            tear_id
        )

        if tear is None:

            return jsonify({
                "ok": False,
                "error": "invalid tear"
            }), 400

        tear_empresa_id = getattr(
            tear,
            "empresa_id",
            None
        )

        if tear_empresa_id != company_id:

            return jsonify({
                "ok": False,
                "error": "tear/company mismatch"
            }), 400

    # ==========================================================
    # META
    # ==========================================================

    meta = data.get(
        "meta"
    )

    if meta is None:
        meta = {}

    if not isinstance(
        meta,
        dict
    ):

        return jsonify({
            "ok": False,
            "error": "invalid meta"
        }), 400

    # Evita objetos excessivamente grandes.
    if len(meta) > 30:

        return jsonify({
            "ok": False,
            "error": "meta too large"
        }), 400

    try:

        meta_json = json.dumps(
            meta,
            ensure_ascii=False,
            separators=(
                ",",
                ":"
            ),
        )

    except Exception:

        return jsonify({
            "ok": False,
            "error": "invalid meta"
        }), 400

    # Máximo de aproximadamente 4 KB para metadados.
    if len(
        meta_json.encode("utf-8")
    ) > 4096:

        return jsonify({
            "ok": False,
            "error": "meta too large"
        }), 400

    # ==========================================================
    # SESSION ID ANALÍTICO
    #
    # NÃO utiliza request.cookies["session"].
    # ==========================================================

    analytics_session_id = (
        _analytics_session_id()
    )

    # ==========================================================
    # GRAVA EVENTO
    # ==========================================================

    try:

        with db.engine.begin() as conn:

            conn.execute(
                text(
                    """
                    INSERT INTO analytics_events
                        (
                            company_id,
                            tear_id,
                            event,
                            session_id,
                            meta
                        )
                    VALUES
                        (
                            :cid,
                            :tid,
                            :evt,
                            :sid,
                            :meta
                        )
                    """
                ),
                {
                    "cid": company_id,
                    "tid": tear_id,
                    "evt": event,
                    "sid": (
                        analytics_session_id
                    ),
                    "meta": meta_json,
                },
            )

        return jsonify({
            "ok": True
        }), 200

    except Exception:

        current_app.logger.exception(
            "[ANALYTICS] Falha ao registrar evento."
        )

        return jsonify({
            "ok": False
        }), 500

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

# ==============================================================
# OTP — HELPERS DE SEGURANÇA
# ==============================================================

def _otp_code_hash(email: str, codigo: str) -> str:
    """
    Gera um HMAC-SHA256 do código OTP.

    O código OTP nunca é salvo em texto puro no banco.

    O e-mail faz parte da mensagem assinada para que o mesmo
    código utilizado para contas diferentes produza hashes
    diferentes.

    A SECRET_KEY permanece somente no servidor.
    """

    email = (
        email
        or ""
    ).strip().lower()

    codigo = (
        codigo
        or ""
    ).strip()

    secret_key = current_app.config.get(
        "SECRET_KEY"
    )

    if not secret_key:

        raise RuntimeError(
            "SECRET_KEY não configurada para geração do OTP."
        )

    if isinstance(
        secret_key,
        bytes
    ):
        secret_bytes = secret_key
    else:
        secret_bytes = str(
            secret_key
        ).encode(
            "utf-8"
        )

    payload = (
        f"{email}:{codigo}"
    ).encode(
        "utf-8"
    )

    return hmac.new(
        secret_bytes,
        payload,
        hashlib.sha256
    ).hexdigest()


def _otp_clear_legacy_session():
    """
    Remove implementações antigas de OTP que eram armazenadas
    na sessão/cookie do Flask.

    Após a migração para OtpToken/PostgreSQL, nenhum código OTP
    deve permanecer dentro da sessão do navegador.
    """

    changed = False

    for key in (
        "otp_login",
        "otp",
    ):

        if key in session:

            session.pop(
                key,
                None
            )

            changed = True

    if changed:

        session.modified = True

def _otp_send(
    to_email: str,
    ip: str = "",
    ua: str = ""
):
    """
    Gera e envia um OTP de login.

    Segurança:
    - usa secrets para geração criptograficamente segura;
    - não armazena o código em texto puro;
    - armazena somente HMAC-SHA256 no PostgreSQL;
    - invalida OTPs anteriores do mesmo e-mail;
    - OTP expira em 30 minutos;
    - contador de tentativas começa em zero;
    - remove implementações antigas armazenadas na session;
    - se o envio de e-mail falhar ou gerar exceção,
      o novo OTP é invalidado.
    """

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    to_email = (
        to_email
        or ""
    ).strip().lower()

    if not to_email:

        return (
            False,
            "Não foi possível enviar o código agora. "
            "Tente novamente."
        )

    # Remove eventual OTP legado do cookie/sessão.
    _otp_clear_legacy_session()

    token = None

    try:

        # ======================================================
        # GERAÇÃO SEGURA
        # ======================================================

        code = (
            f"{secrets.randbelow(1_000_000):06d}"
        )

        minutes = 30

        now = datetime.utcnow()

        expires_at = (
            now
            + timedelta(
                minutes=minutes
            )
        )

        code_hash = _otp_code_hash(
            to_email,
            code
        )

        # ======================================================
        # INVALIDA TOKENS ANTERIORES
        # ======================================================

        previous_tokens = (
            OtpToken.query
            .filter(
                OtpToken.email == to_email,
                OtpToken.used_at.is_(None),
            )
            .all()
        )

        for previous_token in previous_tokens:

            previous_token.used_at = now

        # ======================================================
        # CRIA NOVO TOKEN SERVER-SIDE
        # ======================================================

        token = OtpToken(
            email=to_email,
            code_hash=code_hash,
            created_at=now,
            expires_at=expires_at,
            used_at=None,
            attempts=0,
            last_sent_at=now,
            ip=(
                ip
                or ""
            )[:64],
            user_agent=(
                ua
                or ""
            )[:255],
        )

        db.session.add(
            token
        )

        db.session.commit()

        # ======================================================
        # PREPARA E-MAIL
        # ======================================================

        subject = (
            "Seu código de acesso – AcheTece"
        )

        text = (
            f"Seu código é {code}. "
            f"Ele expira em {minutes} minutos."
        )

        html = _otp_email_html(
            to_email,
            code,
            minutes
        )

        # ======================================================
        # ENVIA E-MAIL
        # ======================================================

        try:

            sent = _email_send_html_first(
                to_email,
                subject,
                text,
                html
            )

        except Exception:

            current_app.logger.exception(
                "[OTP] Exceção durante envio do e-mail."
            )

            sent = False

        # ======================================================
        # ENVIO COM SUCESSO
        # ======================================================

        if sent:

            current_app.logger.info(
                "[OTP] HTML enviado com sucesso"
            )

            return (
                True,
                "Enviamos um código para o seu e-mail."
            )

        # ======================================================
        # ENVIO FALHOU → INVALIDA TOKEN
        # ======================================================

        token.used_at = datetime.utcnow()

        db.session.commit()

        current_app.logger.error(
            "[OTP] Falha ao enviar HTML "
            "(nenhum backend aceitou)"
        )

        return (
            False,
            "Não foi possível enviar o código agora. "
            "Tente novamente."
        )

    except Exception:

        db.session.rollback()

        # Se o token chegou a ser persistido antes de alguma
        # falha posterior, tenta invalidá-lo de forma defensiva.
        if (
            token is not None
            and getattr(
                token,
                "id",
                None
            )
        ):

            try:

                persisted_token = db.session.get(
                    OtpToken,
                    token.id
                )

                if (
                    persisted_token
                    and persisted_token.used_at is None
                ):

                    persisted_token.used_at = (
                        datetime.utcnow()
                    )

                    db.session.commit()

            except Exception:

                db.session.rollback()

                current_app.logger.exception(
                    "[OTP] Falha ao invalidar token "
                    "após exceção."
                )

        current_app.logger.exception(
            "Falha ao enviar OTP de login"
        )

        return (
            False,
            "Não foi possível enviar o código agora. "
            "Tente novamente."
        )

# Mantém seu _otp_validate como estava (com guard ou não, tanto faz)
# ----------------------------------------------------------------------

# ----------------------------------------------------------------------
# AcheTece 2.0 - identificação e abertura de sessão por perfil
# ----------------------------------------------------------------------

def _achar_conta_login(email: str):
    """
    Retorna:
        ("malharia", empresa, usuario)
        ("cliente", None, usuario)
        (None, None, None)

    Mantém prioridade para Empresa para preservar os cadastros legados
    das malharias.
    """

    email = (email or "").strip().lower()

    if not email:
        return None, None, None

    # 1. Primeiro preserva o fluxo histórico das malharias
    empresa = Empresa.query.filter(
        func.lower(Empresa.email) == email
    ).first()

    if empresa:
        usuario = getattr(empresa, "usuario", None)

        return "malharia", empresa, usuario

    # 2. Depois procura o novo usuário do AcheTece 2.0
    usuario = Usuario.query.filter(
        func.lower(Usuario.email) == email
    ).first()

    if usuario:
        role = (usuario.role or "").strip().lower()

        if role == "cliente":
            return "cliente", None, usuario

    # Admin continua utilizando seu fluxo administrativo próprio.
    return None, None, None


def _abrir_sessao_cliente(usuario):
    """
    Cria a sessão padrão do Portal do Comprador.
    """

    session.clear()

    session["user_id"] = usuario.id
    session["auth_user_id"] = usuario.id

    session["login_email"] = usuario.email
    session["auth_email"] = usuario.email

    session["perfil"] = "cliente"

    session.permanent = True


def _abrir_sessao_malharia(empresa):
    """
    Cria a sessão atual da malharia e acrescenta,
    quando disponível, a identidade Usuario.
    """

    session.clear()

    session["empresa_id"] = empresa.id

    session["empresa_apelido"] = (
        empresa.apelido
        or empresa.nome
        or empresa.email.split("@")[0]
    )

    session["empresa_nome"] = empresa.nome

    session["login_email"] = empresa.email
    session["auth_email"] = empresa.email

    session["perfil"] = "malharia"

    # Malharias novas já podem possuir Usuario associado.
    if getattr(empresa, "user_id", None):
        session["user_id"] = empresa.user_id
        session["auth_user_id"] = empresa.user_id

    session.permanent = True

# ----------------------------------------------------------------------
# AcheTece 2.0 - Regra central de acesso à conta
# ----------------------------------------------------------------------

def _avaliar_acesso_conta(
    tipo,
    empresa=None,
    usuario=None
):

    tipo = (
        tipo
        or ""
    ).strip().lower()

    # ==============================================================
    # COMPRADOR
    # ==============================================================

    if tipo == "cliente":

        if not usuario:

            return (
                "conta_invalida",
                "Conta não localizada."
            )

        if usuario.is_active is False:

            return (
                "conta_inativa",
                "Esta conta está desativada."
            )

        return (
            "ok",
            None
        )

    # ==============================================================
    # MALHARIA
    # ==============================================================

    if tipo == "malharia":

        if not empresa:

            return (
                "conta_invalida",
                "Conta da malharia não localizada."
            )

        # ----------------------------------------------------------
        # Usuário associado, quando existir
        # ----------------------------------------------------------

        usuario_empresa = (
            usuario
            or getattr(
                empresa,
                "usuario",
                None
            )
        )

        if (
            usuario_empresa
            and usuario_empresa.is_active is False
        ):

            return (
                "conta_inativa",
                "Esta conta está desativada."
            )

        # ----------------------------------------------------------
        # STAGING / DEMO
        #
        # Em modo DEMO, a assinatura não bloqueia a operação.
        # ----------------------------------------------------------

        if DEMO_MODE:

            return (
                "ok",
                None
            )

        # ----------------------------------------------------------
        # ASSINATURA
        #
        # Usa a propriedade Empresa.assinatura_ativa,
        # que considera status + validade do plano.
        # ----------------------------------------------------------

        try:

            assinatura_ativa = bool(
                empresa.assinatura_ativa
            )

        except Exception:

            assinatura_ativa = False

        if not assinatura_ativa:

            return (
                "assinatura_inativa",
                (
                    "Sua assinatura não está ativa. "
                    "Regularize seu plano para acessar "
                    "o Portal da Malharia."
                )
            )

        return (
            "ok",
            None
        )

    return (
        "conta_invalida",
        "Conta não localizada."
    )


def _finalizar_login_conta(
    tipo,
    empresa=None,
    usuario=None,
    avatar_url=None
):

    # ==============================================================
    # REGRA CENTRAL
    # ==============================================================

    (
        situacao,
        mensagem
    ) = _avaliar_acesso_conta(
        tipo,
        empresa,
        usuario
    )

    # ==============================================================
    # CONTA INVÁLIDA / DESATIVADA
    # ==============================================================

    if situacao in {
        "conta_invalida",
        "conta_inativa"
    }:

        session.clear()

        flash(
            mensagem
            or "Não foi possível acessar esta conta.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # COMPRADOR
    # ==============================================================

    if tipo == "cliente":

        _abrir_sessao_cliente(
            usuario
        )

        if avatar_url:

            session[
                "avatar_url"
            ] = avatar_url

        flash(
            "Bem-vindo!",
            "success"
        )

        return redirect(
            url_for(
                "painel_comprador"
            )
        )

    # ==============================================================
    # MALHARIA
    #
    # Mesmo com assinatura vencida, abrimos uma sessão limitada.
    # Isso permite que a empresa acesse /planos e regularize.
    # ==============================================================

    if tipo == "malharia":

        _abrir_sessao_malharia(
            empresa
        )

        if avatar_url:

            session[
                "avatar_url"
            ] = avatar_url

        if situacao == "assinatura_inativa":

            flash(
                mensagem,
                "warning"
            )

            return redirect(
                url_for(
                    "planos"
                )
            )

        flash(
            "Bem-vindo!",
            "success"
        )

        return redirect(
            url_for(
                "painel_malharia"
            )
        )

    session.clear()

    return redirect(
        url_for("login")
    )

# /login
@app.route(
    "/login",
    methods=["GET", "POST"],
    endpoint="login"
)
def view_login():

    # ==============================================================
    # GET
    # ==============================================================

    if request.method == "GET":

        email = (
            request.args.get("email")
            or ""
        ).strip().lower()

        return render_template(
            "login.html",
            email=email
        )

    # ==============================================================
    # POST — CONTINUAR
    # ==============================================================

    email = (
        request.form.get("email")
        or request.args.get("email")
        or ""
    ).strip().lower()

    # --------------------------------------------------------------
    # Validação básica
    # --------------------------------------------------------------

    if (
        not email
        or "@" not in email
    ):

        return render_template(
            "login.html",
            email=email,
            error="Informe um e-mail válido."
        )

    # --------------------------------------------------------------
    # Localiza conta
    # --------------------------------------------------------------

    tipo, empresa, usuario = (
        _achar_conta_login(
            email
        )
    )

    if not tipo:

        return render_template(
            "login.html",
            email=email,
            no_account=True
        )

    # --------------------------------------------------------------
    # Escolha do método de autenticação
    # --------------------------------------------------------------

    return redirect(
        url_for(
            "login_method",
            email=email
        )
    )

@app.get("/login/")
def view_login_trailing():
    return redirect(url_for("login"), code=301)

# /login/metodo (escolha)
@app.get(
    "/login/metodo",
    endpoint="login_method"
)
def view_login_method():

    email = (
        request.args.get("email")
        or ""
    ).strip().lower()

    if not email:

        flash(
            "Informe um e-mail para continuar.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    return render_template(
        "login_method.html",
        email=email
    )

@app.get("/login/método", endpoint="login_method_accent")
def view_login_method_alias_accent():
    return redirect(url_for("login_method", **request.args), code=301)

@app.get("/login/metodo/", endpoint="login_method_alias_trailing")
def view_login_method_alias_trailing():
    return redirect(url_for("login_method", **request.args), code=301)

# ==============================================================
# LOGIN POR CÓDIGO — PROCESSAMENTO INTERNO
# ==============================================================
#
# Esta função concentra a lógica real de solicitação de OTP.
#
# Ela não possui rota própria.
#
# As duas rotas POST abaixo chamam esta mesma implementação
# e compartilham o mesmo rate limit.
# ==============================================================

def _process_login_code_request():

    # ==========================================================
    # E-MAIL
    # ==========================================================

    email = (
        request.form.get("email")
        or request.args.get("email")
        or ""
    ).strip().lower()

    if not email:

        flash(
            "Informe um e-mail válido.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    # ==========================================================
    # CONFIRMA EXISTÊNCIA DA CONTA
    # ==========================================================

    (
        tipo,
        empresa,
        usuario
    ) = _achar_conta_login(
        email
    )

    if not tipo:

        return render_template(
            "login.html",
            email=email,
            no_account=True
        )

    # ==========================================================
    # ENVIA OTP
    # ==========================================================

    ok, msg = _otp_send(
        email,
        ip=_client_ip()[:64],
        ua=(
            request.headers.get(
                "User-Agent"
            )
            or ""
        )[:255],
    )

    flash(
        msg,
        "success" if ok else "error"
    )

    return redirect(
        url_for(
            "login_code",
            email=email
        )
    )


# ==============================================================
# SOLICITAR OTP — POST CANÔNICO
# ==============================================================
#
# Limite compartilhado:
# 5 solicitações / 15 minutos / IP.
# ==============================================================

@app.post(
    "/login/codigo",
    endpoint="post_login_code"
)
@otp_send_limit
def post_login_code():
    """
    Solicita o envio do código OTP pela URL canônica.
    """

    return _process_login_code_request()


# ==============================================================
# SOLICITAR OTP — POST COM ACENTO
# ==============================================================
#
# Mantido como alias de compatibilidade.
#
# Utiliza o MESMO contador da rota canônica.
# Assim, alternar entre /codigo e /código não permite
# contornar o rate limit.
# ==============================================================

@app.post(
    "/login/código",
    endpoint="post_login_code_accent"
)
@otp_send_limit
def post_login_code_accent():
    """
    Alias da solicitação de OTP com acento na URL.
    """

    return _process_login_code_request()


# ==============================================================
# TELA PARA DIGITAR O CÓDIGO — GET
# ==============================================================
#
# Apenas exibe a tela.
# Não dispara e-mail e não consome o rate limit de envio.
# ==============================================================

@app.get(
    "/login/codigo",
    endpoint="login_code"
)
def get_login_code():

    email = (
        request.form.get("email")
        or request.args.get("email")
        or ""
    ).strip().lower()

    if not email:

        return redirect(
            url_for("login")
        )

    return render_template(
        "login_code.html",
        email=email
    )

# ==============================================================
# REENVIAR CÓDIGO OTP — POST
# ==============================================================
#
# Segurança:
# - não utiliza mais GET para gerar/enviar OTP;
# - protegido pelo CSRF global;
# - compartilha o MESMO rate limit da solicitação inicial;
# - 5 envios/reenvios a cada 15 minutos por IP.
# ==============================================================

@app.post(
    "/login/codigo/reenviar",
    endpoint="resend_login_code"
)
@otp_send_limit
def resend_login_code():
    """
    Reenvia o código OTP utilizando a mesma lógica segura
    da solicitação inicial.

    O contador é compartilhado entre:
    - POST /login/codigo
    - POST /login/código
    - POST /login/codigo/reenviar
    """

    return _process_login_code_request()

# ==============================================================
# VALIDAR CÓDIGO OTP — POST
# ==============================================================
#
# Esta rota valida o código informado pelo usuário.
#
# Limite:
# 10 tentativas a cada 15 minutos por cliente/IP.
#
# O GET /login/codigo, que apenas exibe a tela,
# permanece sem rate limit.
#
# Ao exceder o limite:
#
# Flask-Limiter
#     ↓
# HTTP 429
#     ↓
# handle_rate_limit_exceeded()
#     ↓
# página amigável do AcheTece.
# ==============================================================

@app.post(
    "/login/codigo/validar",
    endpoint="validate_login_code"
)
@limiter.limit("10 per 15 minutes")
def validate_login_code():
    """
    Valida o código OTP informado pelo usuário.

    Segurança:
    - POST protegido pelo CSRF global;
    - máximo de 10 tentativas em 15 minutos por IP;
    - excesso de tentativas gera HTTP 429;
    - nenhuma informação sobre o código informado
      é registrada nos logs.
    """

    # ==========================================================
    # DADOS
    # ==========================================================

    email = (
        request.form.get("email")
        or request.args.get("email")
        or ""
    ).strip().lower()

    codigo = (
        request.form.get("codigo")
        or request.form.get("code")
        or ""
    ).strip()

    # ==========================================================
    # VALIDAÇÕES BÁSICAS
    # ==========================================================

    if not email:

        flash(
            "Informe um e-mail válido.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    if not codigo:

        flash(
            "Informe o código recebido.",
            "warning"
        )

        return redirect(
            url_for(
                "login_code",
                email=email
            )
        )

    # ==========================================================
    # VALIDA OTP
    # ==========================================================

    ok, msg = _otp_validate(
        email,
        codigo
    )

    if not ok:

        current_app.logger.warning(
            "[SECURITY][OTP_INVALID] "
            "ip=%s",
            _client_ip(),
        )

        flash(
            msg,
            "danger"
        )

        return redirect(
            url_for(
                "login_code",
                email=email
            )
        )

    # ==========================================================
    # LOCALIZA CONTA
    # ==========================================================

    (
        tipo,
        empresa,
        usuario
    ) = _achar_conta_login(
        email
    )

    if not tipo:

        flash(
            (
                "E-mail ainda não cadastrado. "
                "Conclua seu cadastro para continuar."
            ),
            "info"
        )

        return redirect(
            url_for("login")
        )

    # ==========================================================
    # REGRA CENTRAL DE ACESSO
    # ==========================================================

    return _finalizar_login_conta(
        tipo,
        empresa,
        usuario
    )

# Senha: TELA (GET)
@app.get(
    "/login/senha",
    endpoint="view_login_password"
)
def view_login_password():

    email = (
        request.args.get("email")
        or ""
    ).strip().lower()

    if not email:

        return redirect(
            url_for("login")
        )

    return render_template(
        "login_senha.html",
        email=email
    )

# Senha: AUTENTICAR (POST)
@app.post(
    "/login/senha/entrar",
    endpoint="post_login_password"
)
@limiter.limit("10 per 15 minutes")
def post_login_password():
    """
    Processa o login por senha de compradores e malharias.

    Segurança:
    - CSRF global continua ativo;
    - máximo de 10 tentativas a cada 15 minutos
      por cliente/IP identificado por _client_ip();
    - ao exceder o limite, o Flask-Limiter gera HTTP 429,
      tratado pelo handler global do AcheTece.
    """

    email = (
        request.form.get("email")
        or request.args.get("email")
        or ""
    ).strip().lower()

    senha = (
        request.form.get("senha")
        or ""
    )

    GENERIC_FAIL = (
        "E-mail ou senha incorretos. Tente novamente."
    )

    # ==============================================================
    # LOCALIZA CONTA
    # ==============================================================

    (
        tipo,
        empresa,
        usuario
    ) = _achar_conta_login(
        email
    )

    if not tipo:

        flash(
            GENERIC_FAIL,
            "error"
        )

        return redirect(
            url_for(
                "view_login_password",
                email=email
            )
        )

    # ==============================================================
    # COMPRADOR
    # ==============================================================

    if tipo == "cliente":

        if not usuario:

            flash(
                GENERIC_FAIL,
                "error"
            )

            return redirect(
                url_for(
                    "view_login_password",
                    email=email
                )
            )

        senha_ok = False

        try:

            if usuario.senha_hash:

                senha_ok = (
                    check_password_hash(
                        usuario.senha_hash,
                        senha
                    )
                )

        except Exception as e:

            app.logger.warning(
                (
                    "[LOGIN CLIENTE WARN] "
                    f"check_password_hash: {e}"
                )
            )

        if not senha_ok:

            flash(
                GENERIC_FAIL,
                "error"
            )

            return redirect(
                url_for(
                    "view_login_password",
                    email=email
                )
            )

        return _finalizar_login_conta(
            tipo,
            empresa,
            usuario
        )

    # ==============================================================
    # MALHARIA
    # ==============================================================

    if tipo == "malharia":

        senha_ok = False

        try:

            if empresa and empresa.senha:

                senha_ok = (
                    check_password_hash(
                        empresa.senha,
                        senha
                    )
                )

        except Exception as e:

            app.logger.warning(
                (
                    "[LOGIN MALHARIA WARN] "
                    f"check_password_hash: {e}"
                )
            )

        if not senha_ok:

            flash(
                GENERIC_FAIL,
                "error"
            )

            return redirect(
                url_for(
                    "view_login_password",
                    email=email
                )
            )

        return _finalizar_login_conta(
            tipo,
            empresa,
            usuario
        )

    # ==============================================================
    # FALLBACK
    # ==============================================================

    flash(
        GENERIC_FAIL,
        "error"
    )

    return redirect(
        url_for(
            "view_login_password",
            email=email
        )
    )

@app.get("/oauth/google")
def oauth_google():
    # contexto padrão "empresa" e preserva redirecionamento
    ctx = request.args.get("ctx", "empresa")
    nxt = request.args.get("next") or url_for("index")

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

@app.get(
    "/oauth/google/callback"
)
def oauth_google_callback():

    # ==============================================================
    # GOOGLE
    # ==============================================================

    try:

        oauth.google.authorize_access_token()

        userinfo = (
            oauth.google
            .get(
                (
                    "https://openidconnect.googleapis.com/"
                    "v1/userinfo"
                )
            )
            .json()
        )

    except Exception as e:

        current_app.logger.exception(
            (
                "Falha no callback "
                f"do Google: {e}"
            )
        )

        flash(
            (
                "Não foi possível concluir "
                "o login com o Google."
            ),
            "danger"
        )

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # IDENTIDADE GOOGLE
    # ==============================================================

    email = (
        userinfo.get("email")
        or ""
    ).strip().lower()

    foto = (
        userinfo.get("picture")
        or None
    )

    # Limpa informações temporárias do OAuth
    session.pop(
        "oauth_ctx",
        None
    )

    session.pop(
        "oauth_next",
        None
    )

    if not email:

        flash(
            (
                "Não foi possível obter "
                "o e-mail do Google."
            ),
            "danger"
        )

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # CONTA ACHETECE
    # ==============================================================

    (
        tipo,
        empresa,
        usuario
    ) = _achar_conta_login(
        email
    )

    if not tipo:

        flash(
            (
                "Não encontramos uma conta AcheTece "
                "para este e-mail. "
                "Faça seu cadastro para continuar."
            ),
            "warning"
        )

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # REGRA CENTRAL
    # ==============================================================

    return _finalizar_login_conta(
        tipo,
        empresa,
        usuario,
        avatar_url=foto
    )

@app.route("/logout")
def logout():

    session.clear()

    return redirect(
        url_for("login")
    )

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
# Cadastro do Comprador - AcheTece 2.0
# --------------------------------------------------------------------
@app.route(
    "/cadastro/comprador",
    methods=["GET", "POST"],
    endpoint="cadastro_comprador"
)
@limiter.limit(
    "5 per hour",
    methods=["POST"]
)
@limiter.limit(
    "20 per day",
    methods=["POST"]
)
def cadastro_comprador():

    estados = [
        "AC","AL","AM","AP","BA","CE","DF","ES","GO","MA",
        "MG","MS","MT","PA","PB","PE","PI","PR","RJ","RN",
        "RO","RR","RS","SC","SE","SP","TO"
    ]

    form_data = {}

    if request.method == "GET":

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )

    # --------------------------------------------------------------
    # Dados recebidos
    # --------------------------------------------------------------

    nome = (request.form.get("nome") or "").strip()

    empresa_nome = (
        request.form.get("empresa") or ""
    ).strip()

    whatsapp = (
        request.form.get("whatsapp") or ""
    ).strip()

    cidade = (
        request.form.get("cidade") or ""
    ).strip()

    estado = (
        request.form.get("estado") or ""
    ).strip().upper()

    email = (
        request.form.get("email") or ""
    ).strip().lower()

    senha = (
        request.form.get("senha") or ""
    ).strip()


    form_data = {
        "nome": nome,
        "empresa": empresa_nome,
        "whatsapp": whatsapp,
        "cidade": cidade,
        "estado": estado,
        "email": email,
    }


    # --------------------------------------------------------------
    # Validações básicas
    # --------------------------------------------------------------

    if not nome:

        flash(
            "Informe seu nome.",
            "warning"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    if not empresa_nome:

        flash(
            "Informe a empresa, marca ou confecção.",
            "warning"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    if estado not in estados:

        flash(
            "Selecione um estado válido.",
            "warning"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    if not cidade:

        flash(
            "Informe sua cidade.",
            "warning"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    if not email or "@" not in email:

        flash(
            "Informe um e-mail válido.",
            "warning"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    if len(senha) < 6:

        flash(
            "A senha precisa ter pelo menos 6 caracteres.",
            "warning"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    # --------------------------------------------------------------
    # Evita conta duplicada
    # --------------------------------------------------------------

    usuario_existente = Usuario.query.filter(
        func.lower(Usuario.email) == email
    ).first()

    empresa_existente = Empresa.query.filter(
        func.lower(Empresa.email) == email
    ).first()


    if usuario_existente or empresa_existente:

        flash(
            "Este e-mail já possui uma conta no AcheTece.",
            "warning"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    # --------------------------------------------------------------
    # Cria Usuario + ClienteProfile
    # --------------------------------------------------------------

    try:

        novo_usuario = Usuario(
            email=email,
            senha_hash=generate_password_hash(senha),
            role="cliente",
            is_active=True
        )

        db.session.add(novo_usuario)

        db.session.flush()


        novo_perfil = ClienteProfile(
            user_id=novo_usuario.id,
            nome=nome,
            empresa=empresa_nome,
            whatsapp=whatsapp or None,
            cidade=cidade,
            estado=estado
        )

        db.session.add(novo_perfil)

        db.session.commit()


    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[COMPRADOR] Falha ao criar conta."
        )

        flash(
            "Não foi possível criar sua conta agora. Tente novamente.",
            "danger"
        )

        return render_template(
            "criar_conta_cliente.html",
            estados=estados,
            form_data=form_data
        )


    # ------------------------------------------------------------
    # Login automático
    # ------------------------------------------------------------
    
    _abrir_sessao_cliente(novo_usuario)


    flash(
        "Conta criada com sucesso. Bem-vindo ao AcheTece!",
        "success"
    )


    return redirect(
        url_for("painel_comprador")
    )

# --------------------------------------------------------------------
# Nova Demanda - AcheTece 2.0
# --------------------------------------------------------------------

@app.route(
    "/comprador/demandas/nova",
    methods=["GET", "POST"],
    endpoint="nova_demanda"
)
@limiter.limit(
    "10 per hour",
    key_func=_cliente_rate_limit_key,
    methods=["POST"]
)
@limiter.limit(
    "30 per day",
    key_func=_cliente_rate_limit_key,
    methods=["POST"]
)
def nova_demanda():

    user_id = session.get("user_id")

    if not user_id:
        return redirect(url_for("login"))

    try:
        usuario = db.session.get(
            Usuario,
            int(user_id)
        )
    except Exception:
        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (usuario.role or "").strip().lower() != "cliente"
    ):
        return redirect(
            url_for("painel_comprador")
        )

    estados = [
        "AC","AL","AM","AP","BA","CE","DF","ES","GO","MA",
        "MG","MS","MT","PA","PB","PE","PI","PR","RJ","RN",
        "RO","RR","RS","SC","SE","SP","TO"
    ]

    form_data = {}

    if request.method == "GET":

        return render_template(
            "nova_demanda.html",
            estados=estados,
            form_data=form_data
        )

    # --------------------------------------------------------------
    # Dados recebidos
    # --------------------------------------------------------------

    produto = (
        request.form.get("produto")
        or ""
    ).strip()

    estrutura_malha = (
        request.form.get("estrutura_malha")
        or ""
    ).strip()

    composicao = (
        request.form.get("composicao")
        or ""
    ).strip()

    titulo_fio = (
        request.form.get("titulo_fio")
        or ""
    ).strip()

    gramatura_raw = (
        request.form.get("gramatura")
        or ""
    ).strip()

    quantidade_raw = (
        request.form.get("quantidade_kg")
        or ""
    ).strip()

    data_raw = (
        request.form.get("data_necessidade")
        or ""
    ).strip()

    estado_preferencial = (
        request.form.get("estado_preferencial")
        or ""
    ).strip().upper()

    cidade_preferencial = (
        request.form.get("cidade_preferencial")
        or ""
    ).strip()

    tipo_servico = (
        request.form.get("tipo_servico")
        or ""
    ).strip()

    observacoes = (
        request.form.get("observacoes")
        or ""
    ).strip()

    form_data = {
        "produto": produto,
        "estrutura_malha": estrutura_malha,
        "composicao": composicao,
        "titulo_fio": titulo_fio,
        "gramatura": gramatura_raw,
        "quantidade_kg": quantidade_raw,
        "data_necessidade": data_raw,
        "estado_preferencial": estado_preferencial,
        "cidade_preferencial": cidade_preferencial,
        "tipo_servico": tipo_servico,
        "observacoes": observacoes,
    }

    # --------------------------------------------------------------
    # Validação
    # --------------------------------------------------------------

    if not produto:

        flash(
            "Informe o produto que precisa produzir.",
            "warning"
        )

        return render_template(
            "nova_demanda.html",
            estados=estados,
            form_data=form_data
        )

    try:

        quantidade_normalizada = (
            quantidade_raw
            .replace(".", "")
            .replace(",", ".")
        )

        quantidade_kg = Decimal(
            quantidade_normalizada
        )

        if quantidade_kg <= 0:
            raise InvalidOperation

    except Exception:

        flash(
            "Informe uma quantidade válida em kg.",
            "warning"
        )

        return render_template(
            "nova_demanda.html",
            estados=estados,
            form_data=form_data
        )

    gramatura = None

    if gramatura_raw:

        try:
            gramatura = int(
                gramatura_raw
            )

            if gramatura <= 0:
                raise ValueError

        except Exception:

            flash(
                "Informe uma gramatura válida.",
                "warning"
            )

            return render_template(
                "nova_demanda.html",
                estados=estados,
                form_data=form_data
            )

    data_necessidade = None

    if data_raw:

        try:

            data_necessidade = datetime.strptime(
                data_raw,
                "%Y-%m-%d"
            ).date()

        except Exception:

            flash(
                "Informe uma data válida.",
                "warning"
            )

            return render_template(
                "nova_demanda.html",
                estados=estados,
                form_data=form_data
            )

    if (
        estado_preferencial
        and estado_preferencial not in estados
    ):

        flash(
            "Selecione um estado válido.",
            "warning"
        )

        return render_template(
            "nova_demanda.html",
            estados=estados,
            form_data=form_data
        )

    # --------------------------------------------------------------
    # Grava a demanda
    # --------------------------------------------------------------

    try:

        demanda = ProductionRequest(
            user_id=usuario.id,
            produto=produto,
            estrutura_malha=estrutura_malha or None,
            composicao=composicao or None,
            titulo_fio=titulo_fio or None,
            gramatura=gramatura,
            quantidade_kg=quantidade_kg,
            data_necessidade=data_necessidade,
            estado_preferencial=estado_preferencial or None,
            cidade_preferencial=cidade_preferencial or None,
            tipo_servico=tipo_servico or None,
            observacoes=observacoes or None,
            status="rascunho"
        )

        db.session.add(
            demanda
        )

        # Precisamos do ID para formar o código público.
        db.session.flush()

        demanda.codigo = (
            f"ATD-{demanda.id:06d}"
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[DEMANDA] Falha ao criar demanda."
        )

        flash(
            "Não foi possível criar a demanda agora.",
            "danger"
        )

        return render_template(
            "nova_demanda.html",
            estados=estados,
            form_data=form_data
        )

    flash(
        f"Demanda {demanda.codigo} criada com sucesso.",
        "success"
    )

    return redirect(
        url_for("painel_comprador")
    )

# --------------------------------------------------------------------
# Minhas Demandas - AcheTece 2.0
# --------------------------------------------------------------------

@app.get(
    "/comprador/demandas",
    endpoint="minhas_demandas"
)
def minhas_demandas():

    user_id = session.get("user_id")

    if not user_id:
        return redirect(
            url_for("login")
        )

    try:
        usuario = db.session.get(
            Usuario,
            int(user_id)
        )
    except Exception:
        usuario = None

    if not usuario or usuario.is_active is False:

        session.clear()

        return redirect(
            url_for("login")
        )

    role = (
        usuario.role or ""
    ).strip().lower()

    if role != "cliente":

        if getattr(
            usuario,
            "empresa",
            None
        ):
            return redirect(
                url_for("painel_malharia")
            )

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Demandas do comprador
    # --------------------------------------------------------------

    demandas = (
        ProductionRequest.query
        .filter_by(
            user_id=usuario.id
        )
        .order_by(
            ProductionRequest.created_at.desc(),
            ProductionRequest.id.desc()
        )
        .all()
    )

    # --------------------------------------------------------------
    # Indicadores reais
    # --------------------------------------------------------------

    total_demandas = len(
        demandas
    )

    total_rascunhos = sum(
        1
        for demanda in demandas
        if (
            demanda.status or ""
        ).lower() == "rascunho"
    )

    total_publicadas = sum(
        1
        for demanda in demandas
        if (
            demanda.status or ""
        ).lower() == "publicada"
    )

    total_encerradas = sum(
        1
        for demanda in demandas
        if (
            demanda.status or ""
        ).lower() == "encerrada"
    )

    return render_template(
        "minhas_demandas.html",
        usuario=usuario,
        demandas=demandas,
        total_demandas=total_demandas,
        total_rascunhos=total_rascunhos,
        total_publicadas=total_publicadas,
        total_encerradas=total_encerradas,
    )

# --------------------------------------------------------------------
# Regra central de bloqueio do Matching - AcheTece 2.0
# --------------------------------------------------------------------

def _verificar_bloqueio_matching(demanda):

    # ==============================================================
    # SEGURANÇA BÁSICA
    # ==============================================================

    if not demanda:

        return (
            True,
            "A demanda não foi localizada."
        )

    # ==============================================================
    # STATUS DA DEMANDA
    #
    # Matching técnico só é operacional enquanto a demanda
    # estiver publicada.
    # ==============================================================

    status_demanda = (
        demanda.status
        or ""
    ).strip().lower()

    if status_demanda != "publicada":

        return (
            True,
            (
                "O Matching Técnico somente pode ser configurado "
                "ou executado enquanto a demanda estiver publicada."
            )
        )

    # ==============================================================
    # PROPOSTA COMERCIAL
    #
    # Qualquer proposta já criada congela definitivamente
    # os critérios técnicos daquela demanda.
    #
    # Isso mantém rastreabilidade entre:
    #
    # requisito técnico
    # -> matching
    # -> oportunidade
    # -> proposta
    # ==============================================================

    proposta_existente = (
        Proposal.query
        .filter(
            Proposal.demand_id
            == demanda.id
        )
        .first()
    )

    if proposta_existente:

        return (
            True,
            (
                "A configuração técnica desta demanda está congelada "
                "porque já existe histórico de proposta comercial. "
                "Para utilizar outros requisitos técnicos, crie uma "
                "nova demanda."
            )
        )

    # ==============================================================
    # MANIFESTAÇÃO DE INTERESSE
    #
    # A primeira manifestação positiva da malharia congela
    # o Matching, mesmo que a proposta ainda não tenha sido enviada.
    # ==============================================================

    oportunidade_interessada = (
        Opportunity.query
        .filter(
            Opportunity.demand_id
            == demanda.id,

            Opportunity.status
            == "interessada"
        )
        .first()
    )

    if oportunidade_interessada:

        return (
            True,
            (
                "A configuração técnica desta demanda está congelada "
                "porque uma malharia já demonstrou interesse. "
                "Os critérios não podem mais ser alterados durante "
                "a negociação comercial."
            )
        )

    # ==============================================================
    # MATCHING LIBERADO
    # ==============================================================

    return (
        False,
        None
    )

# --------------------------------------------------------------------
# Detalhes da Demanda - AcheTece 2.0
# --------------------------------------------------------------------

@app.get(
    "/comprador/demandas/<int:demanda_id>",
    endpoint="detalhe_demanda"
)
def detalhe_demanda(demanda_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Usuário
    # --------------------------------------------------------------

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
    ):

        session.clear()

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Garante perfil comprador
    # --------------------------------------------------------------

    role = (
        usuario.role
        or ""
    ).strip().lower()

    if role != "cliente":

        if getattr(
            usuario,
            "empresa",
            None
        ):

            return redirect(
                url_for(
                    "painel_malharia"
                )
            )

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # DEMANDA
    #
    # Sempre limitada ao comprador autenticado.
    # ==============================================================

    demanda = (
        ProductionRequest.query
        .filter_by(
            id=demanda_id,
            user_id=usuario.id
        )
        .first()
    )

    if not demanda:

        flash(
            "Demanda não encontrada.",
            "warning"
        )

        return redirect(
            url_for(
                "minhas_demandas"
            )
        )

    # ==============================================================
    # SITUAÇÃO DO MATCHING
    # ==============================================================

    (
        matching_bloqueado,
        matching_bloqueio_motivo
    ) = _verificar_bloqueio_matching(
        demanda
    )

    # ==============================================================
    # PEDIDO VINCULADO À DEMANDA
    # ==============================================================

    pedido = (
        Order.query
        .filter(
            Order.demand_id
            == demanda.id,

            Order.buyer_user_id
            == usuario.id
        )
        .order_by(
            Order.id.desc()
        )
        .first()
    )

    # --------------------------------------------------------------
    # Empresa / malharia contratada
    # --------------------------------------------------------------

    empresa_pedido = None

    if pedido:

        try:

            empresa_pedido = (
                pedido.empresa
            )

        except Exception:

            empresa_pedido = (
                Empresa.query.get(
                    pedido.empresa_id
                )
            )

    # --------------------------------------------------------------
    # Proposta que originou o pedido
    # --------------------------------------------------------------

    proposta_pedido = None

    if pedido:

        try:

            proposta_pedido = (
                pedido.proposta
            )

        except Exception:

            proposta_pedido = None

    # ==============================================================
    # PERFIL DO COMPRADOR
    # ==============================================================

    perfil = (
        ClienteProfile.query
        .filter_by(
            user_id=usuario.id
        )
        .first()
    )

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "detalhe_demanda.html",

        usuario=usuario,

        perfil=perfil,

        demanda=demanda,

        pedido=pedido,

        empresa_pedido=
            empresa_pedido,

        proposta_pedido=
            proposta_pedido,

        matching_bloqueado=
            matching_bloqueado,

        matching_bloqueio_motivo=
            matching_bloqueio_motivo,
    )

# --------------------------------------------------------------------
# Publicar Demanda - AcheTece 2.0
# --------------------------------------------------------------------

@app.post(
    "/comprador/demandas/<int:demanda_id>/publicar",
    endpoint="publicar_demanda"
)
def publicar_demanda(demanda_id):

    # --------------------------------------------------------------
    # Verifica sessão
    # --------------------------------------------------------------

    user_id = session.get("user_id")

    if not user_id:
        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Carrega usuário
    # --------------------------------------------------------------

    try:
        usuario = db.session.get(
            Usuario,
            int(user_id)
        )
    except Exception:
        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (usuario.role or "").strip().lower() != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Busca demanda garantindo propriedade
    # --------------------------------------------------------------

    demanda = (
        ProductionRequest.query
        .filter_by(
            id=demanda_id,
            user_id=usuario.id
        )
        .first()
    )

    if not demanda:

        flash(
            "Demanda não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    status_atual = (
        demanda.status or "rascunho"
    ).strip().lower()

    # --------------------------------------------------------------
    # Já publicada
    # --------------------------------------------------------------

    if status_atual == "publicada":

        flash(
            f"A demanda {demanda.codigo} já está publicada.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Somente rascunho pode ser publicado nesta versão
    # --------------------------------------------------------------

    if status_atual != "rascunho":

        flash(
            "Esta demanda não pode ser publicada no status atual.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Valida requisitos mínimos
    # --------------------------------------------------------------

    if not demanda.produto:

        flash(
            "A demanda precisa ter um produto informado antes da publicação.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    if not demanda.quantidade_kg or demanda.quantidade_kg <= 0:

        flash(
            "A demanda precisa ter uma quantidade válida antes da publicação.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Publicação
    # --------------------------------------------------------------

    try:

        demanda.status = "publicada"

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[DEMANDA] Falha ao publicar demanda."
        )

        flash(
            "Não foi possível publicar a demanda agora.",
            "danger"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    flash(
        f"Demanda {demanda.codigo} publicada com sucesso.",
        "success"
    )

    return redirect(
        url_for(
            "detalhe_demanda",
            demanda_id=demanda.id
        )
    )

# --------------------------------------------------------------------
# Configuração Técnica do Matching - AcheTece 2.0
# --------------------------------------------------------------------

@app.route(
    "/comprador/demandas/<int:demanda_id>/matching/configurar",
    methods=["GET", "POST"],
    endpoint="configurar_matching"
)
def configurar_matching(demanda_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # DEMANDA
    #
    # Somente demanda pertencente ao comprador autenticado.
    # ==============================================================

    demanda = (
        ProductionRequest.query
        .filter_by(
            id=demanda_id,
            user_id=usuario.id
        )
        .first()
    )

    if not demanda:

        flash(
            "Demanda não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    # ==============================================================
    # BLINDAGEM DO MATCHING
    #
    # Impede:
    #
    # - configuração de demanda não publicada
    # - alteração após manifestação de interesse
    # - alteração após criação de proposta
    # ==============================================================

    (
        matching_bloqueado,
        matching_bloqueio_motivo
    ) = _verificar_bloqueio_matching(
        demanda
    )

    if matching_bloqueado:

        flash(
            matching_bloqueio_motivo,
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # CONFIGURAÇÃO EXISTENTE
    # ==============================================================

    requisito = (
        DemandTechnicalRequirement.query
        .filter_by(
            demand_id=demanda.id
        )
        .first()
    )

    # ==============================================================
    # GET
    # ==============================================================

    if request.method == "GET":

        return render_template(
            "configurar_matching.html",
            demanda=demanda,
            requisito=requisito
        )

    # ==============================================================
    # HELPERS
    # ==============================================================

    def _int_or_none(valor):

        valor = (
            valor
            or ""
        ).strip()

        if not valor:

            return None

        try:

            return int(
                valor
            )

        except Exception:

            return None

    # ==============================================================
    # DADOS DO FORMULÁRIO
    # ==============================================================

    tipo_tear = (
        request.form.get(
            "tipo_tear"
        )
        or ""
    ).strip().upper()

    finura_min = _int_or_none(
        request.form.get(
            "finura_min"
        )
    )

    finura_max = _int_or_none(
        request.form.get(
            "finura_max"
        )
    )

    diametro_min = _int_or_none(
        request.form.get(
            "diametro_min"
        )
    )

    diametro_max = _int_or_none(
        request.form.get(
            "diametro_max"
        )
    )

    alimentadores_min = (
        _int_or_none(
            request.form.get(
                "alimentadores_min"
            )
        )
    )

    pistas_cilindro_min = (
        _int_or_none(
            request.form.get(
                "pistas_cilindro_min"
            )
        )
    )

    pistas_disco_min = (
        _int_or_none(
            request.form.get(
                "pistas_disco_min"
            )
        )
    )

    elastano_raw = (
        request.form.get(
            "elastano_required"
        )
        or ""
    ).strip().lower()

    observacoes_tecnicas = (
        request.form.get(
            "observacoes_tecnicas"
        )
        or ""
    ).strip()

    # ==============================================================
    # TIPO DE TEAR
    # ==============================================================

    if tipo_tear not in {
        "",
        "MONO",
        "DUPLA"
    }:

        flash(
            "Selecione um tipo de tear válido.",
            "warning"
        )

        return redirect(
            url_for(
                "configurar_matching",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # INTERVALOS
    # ==============================================================

    if (
        finura_min is not None
        and finura_max is not None
        and finura_min > finura_max
    ):

        flash(
            "A finura mínima não pode ser maior que a máxima.",
            "warning"
        )

        return redirect(
            url_for(
                "configurar_matching",
                demanda_id=demanda.id
            )
        )

    if (
        diametro_min is not None
        and diametro_max is not None
        and diametro_min > diametro_max
    ):

        flash(
            "O diâmetro mínimo não pode ser maior que o máximo.",
            "warning"
        )

        return redirect(
            url_for(
                "configurar_matching",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # ELASTANO
    # ==============================================================

    elastano_required = None

    if elastano_raw == "sim":

        elastano_required = True

    elif elastano_raw == "nao":

        elastano_required = False

    # ==============================================================
    # VERIFICAÇÃO NOVAMENTE ANTES DE GRAVAR
    #
    # Fazemos uma segunda verificação imediatamente antes
    # da alteração do requisito técnico.
    # ==============================================================

    (
        matching_bloqueado,
        matching_bloqueio_motivo
    ) = _verificar_bloqueio_matching(
        demanda
    )

    if matching_bloqueado:

        flash(
            matching_bloqueio_motivo,
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # UPSERT
    # ==============================================================

    try:

        if not requisito:

            requisito = (
                DemandTechnicalRequirement(
                    demand_id=demanda.id
                )
            )

            db.session.add(
                requisito
            )

        requisito.tipo_tear = (
            tipo_tear
            or None
        )

        requisito.finura_min = (
            finura_min
        )

        requisito.finura_max = (
            finura_max
        )

        requisito.diametro_min = (
            diametro_min
        )

        requisito.diametro_max = (
            diametro_max
        )

        requisito.alimentadores_min = (
            alimentadores_min
        )

        requisito.pistas_cilindro_min = (
            pistas_cilindro_min
        )

        requisito.pistas_disco_min = (
            pistas_disco_min
        )

        requisito.elastano_required = (
            elastano_required
        )

        requisito.observacoes_tecnicas = (
            observacoes_tecnicas
            or None
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[MATCHING] Falha ao salvar requisitos técnicos."
        )

        flash(
            "Não foi possível salvar a configuração.",
            "danger"
        )

        return redirect(
            url_for(
                "configurar_matching",
                demanda_id=demanda.id
            )
        )

    flash(
        "Configuração técnica do matching salva com sucesso.",
        "success"
    )

    return redirect(
        url_for(
            "configurar_matching",
            demanda_id=demanda.id
        )
    )

# --------------------------------------------------------------------
# Executar Matching Técnico - AcheTece 2.0
# --------------------------------------------------------------------

@app.post(
    "/comprador/demandas/<int:demanda_id>/matching/executar",
    endpoint="executar_matching"
)
def executar_matching(demanda_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = (
        ProductionRequest.query
        .filter_by(
            id=demanda_id,
            user_id=usuario.id
        )
        .first()
    )

    if not demanda:

        flash(
            "Demanda não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    # ==============================================================
    # BLINDAGEM DO MATCHING
    #
    # A mesma regra vale para configuração e execução.
    # ==============================================================

    (
        matching_bloqueado,
        matching_bloqueio_motivo
    ) = _verificar_bloqueio_matching(
        demanda
    )

    if matching_bloqueado:

        flash(
            matching_bloqueio_motivo,
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # REQUISITOS TÉCNICOS
    # ==============================================================

    requisito = (
        DemandTechnicalRequirement.query
        .filter_by(
            demand_id=demanda.id
        )
        .first()
    )

    if not requisito:

        flash(
            "Configure os requisitos técnicos antes de executar o matching.",
            "warning"
        )

        return redirect(
            url_for(
                "configurar_matching",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # SEGUNDA VERIFICAÇÃO ANTES DO RECÁLCULO
    # ==============================================================

    (
        matching_bloqueado,
        matching_bloqueio_motivo
    ) = _verificar_bloqueio_matching(
        demanda
    )

    if matching_bloqueado:

        flash(
            matching_bloqueio_motivo,
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # TEARES
    # ==============================================================

    teares = (
        Tear.query
        .order_by(
            Tear.id.asc()
        )
        .all()
    )

    total_analisados = len(
        teares
    )

    total_compativeis = 0

    # ==============================================================
    # RESUMO POR EMPRESA
    # ==============================================================

    resumo_empresas = {}

    # ==============================================================
    # RECÁLCULO
    # ==============================================================

    try:

        # ----------------------------------------------------------
        # Remove matches técnicos anteriores
        # ----------------------------------------------------------

        (
            DemandMatch.query
            .filter_by(
                demand_id=demanda.id
            )
            .delete(
                synchronize_session=False
            )
        )

        # ----------------------------------------------------------
        # Analisa todos os teares
        # ----------------------------------------------------------

        for tear in teares:

            empresa = getattr(
                tear,
                "empresa",
                None
            )

            if not empresa:

                continue

            (
                compativel,
                score,
                detalhes
            ) = _calcular_match_v1(
                demanda,
                requisito,
                tear,
                empresa
            )

            if not compativel:

                continue

            # ------------------------------------------------------
            # Match técnico
            # ------------------------------------------------------

            novo_match = DemandMatch(
                demand_id=demanda.id,
                tear_id=tear.id,
                empresa_id=empresa.id,
                score=score,
                detalhes=" | ".join(
                    detalhes
                ),
                status="ativo"
            )

            db.session.add(
                novo_match
            )

            total_compativeis += 1

            # ------------------------------------------------------
            # Agrupa por empresa
            # ------------------------------------------------------

            resumo = (
                resumo_empresas
                .get(
                    empresa.id
                )
            )

            if not resumo:

                resumo = {
                    "empresa":
                        empresa,

                    "best_score":
                        score,

                    "compatible_tears":
                        1
                }

                resumo_empresas[
                    empresa.id
                ] = resumo

            else:

                resumo[
                    "compatible_tears"
                ] += 1

                if (
                    score
                    > resumo[
                        "best_score"
                    ]
                ):

                    resumo[
                        "best_score"
                    ] = score

        # ==========================================================
        # OPORTUNIDADES
        # ==============================================================

        oportunidades_existentes = (
            Opportunity.query
            .filter_by(
                demand_id=demanda.id
            )
            .all()
        )

        oportunidades_por_empresa = {

            oportunidade.empresa_id:
                oportunidade

            for oportunidade
            in oportunidades_existentes
        }

        empresas_compativeis = set(
            resumo_empresas.keys()
        )

        # ----------------------------------------------------------
        # Cria ou atualiza oportunidades compatíveis
        # ----------------------------------------------------------

        for (
            empresa_id,
            resumo
        ) in resumo_empresas.items():

            oportunidade = (
                oportunidades_por_empresa
                .get(
                    empresa_id
                )
            )

            if not oportunidade:

                oportunidade = Opportunity(
                    demand_id=demanda.id,
                    empresa_id=empresa_id,
                    best_score=resumo[
                        "best_score"
                    ],
                    compatible_tears=resumo[
                        "compatible_tears"
                    ],
                    status="nova"
                )

                db.session.add(
                    oportunidade
                )

            else:

                oportunidade.best_score = (
                    resumo[
                        "best_score"
                    ]
                )

                oportunidade.compatible_tears = (
                    resumo[
                        "compatible_tears"
                    ]
                )

                # --------------------------------------------------
                # Somente oportunidades técnicas sem decisão
                # comercial podem ser reativadas.
                # --------------------------------------------------

                if (
                    oportunidade.status
                    == "inativa"
                ):

                    oportunidade.status = (
                        "nova"
                    )

        # ----------------------------------------------------------
        # Empresas que deixaram de ser compatíveis
        # ----------------------------------------------------------

        for oportunidade in oportunidades_existentes:

            if (
                oportunidade.empresa_id
                not in empresas_compativeis
            ):

                # --------------------------------------------------
                # Nunca sobrescreve uma decisão comercial.
                # --------------------------------------------------

                if oportunidade.status in {
                    "nova",
                    "visualizada"
                }:

                    oportunidade.status = (
                        "inativa"
                    )

        # ==========================================================
        # COMMIT ÚNICO
        # ==============================================================

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[MATCHING] Falha ao executar Matching Técnico "
            "e gerar oportunidades."
        )

        flash(
            "Não foi possível executar o matching agora.",
            "danger"
        )

        return redirect(
            url_for(
                "detalhe_demanda",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # RESULTADO
    # ==============================================================

    total_oportunidades = len(
        resumo_empresas
    )

    flash(
        (
            f"Matching executado: "
            f"{total_compativeis} tear(es) compatível(is) "
            f"entre {total_analisados} analisado(s), "
            f"gerando {total_oportunidades} "
            f"oportunidade(s) para malharia(s)."
        ),
        "success"
    )

    return redirect(
        url_for(
            "matches_demanda",
            demanda_id=demanda.id
        )
    )

# --------------------------------------------------------------------
# Matches encontrados - AcheTece 2.0
# --------------------------------------------------------------------

@app.get(
    "/comprador/demandas/<int:demanda_id>/matches",
    endpoint="matches_demanda"
)
def matches_demanda(demanda_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # DEMANDA DO COMPRADOR
    # ==============================================================

    demanda = (
        ProductionRequest.query
        .filter_by(
            id=demanda_id,
            user_id=usuario.id
        )
        .first()
    )

    if not demanda:

        flash(
            "Demanda não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    # ==============================================================
    # REQUISITO TÉCNICO
    # ==============================================================

    requisito = (
        DemandTechnicalRequirement.query
        .filter_by(
            demand_id=demanda.id
        )
        .first()
    )

    # ==============================================================
    # MATCHES
    # ==============================================================

    matches = (
        DemandMatch.query
        .filter_by(
            demand_id=demanda.id,
            status="ativo"
        )
        .order_by(
            DemandMatch.score.desc(),
            DemandMatch.id.asc()
        )
        .all()
    )

    # ==============================================================
    # IDENTIFICAÇÃO ANÔNIMA DAS MALHARIAS
    #
    # O comprador pode conhecer quantas malharias/equipamentos
    # são compatíveis, mas a identidade permanece protegida até
    # que exista manifestação comercial.
    # ==============================================================

    anonimo_por_empresa = {}

    contador_malharias = 0

    for match in matches:

        empresa_id = (
            match.empresa_id
        )

        if not empresa_id:

            continue

        if empresa_id not in anonimo_por_empresa:

            contador_malharias += 1

            anonimo_por_empresa[
                empresa_id
            ] = (
                f"Malharia compatível "
                f"{contador_malharias}"
            )

    total_malharias = len(
        anonimo_por_empresa
    )

    # ==============================================================
    # SITUAÇÃO DO MATCHING
    #
    # Usa a regra criada na 8.4D.5B.
    # ==============================================================

    (
        matching_bloqueado,
        matching_bloqueio_motivo
    ) = _verificar_bloqueio_matching(
        demanda
    )

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "matches_demanda.html",

        usuario=usuario,

        demanda=demanda,

        requisito=requisito,

        matches=matches,

        total_matches=len(matches),

        total_malharias=
            total_malharias,

        anonimo_por_empresa=
            anonimo_por_empresa,

        matching_bloqueado=
            matching_bloqueado,

        matching_bloqueio_motivo=
            matching_bloqueio_motivo,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Retorno das Malharias para o Comprador
# --------------------------------------------------------------------

@app.get(
    "/comprador/demandas/<int:demanda_id>/retorno",
    endpoint="retorno_demanda"
)
def retorno_demanda(demanda_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = (
        ProductionRequest.query
        .filter_by(
            id=demanda_id,
            user_id=usuario.id
        )
        .first()
    )

    if not demanda:

        flash(
            "Demanda não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    status_demanda = (
        demanda.status
        or ""
    ).strip().lower()

    modo_historico = (
        status_demanda
        in {
            "contratada",
            "encerrada",
            "cancelada"
        }
    )

    # ==============================================================
    # TODAS AS OPORTUNIDADES
    #
    # Não excluímos mais "inativa", porque ela representa
    # também o histórico após o fechamento da demanda.
    # ==============================================================

    todas_oportunidades = (
        Opportunity.query
        .filter(
            Opportunity.demand_id
            == demanda.id
        )
        .order_by(
            Opportunity.best_score.desc(),
            Opportunity.created_at.asc()
        )
        .all()
    )

    # --------------------------------------------------------------
    # Enquanto a demanda está ativa, apenas oportunidades
    # ainda válidas entram nos indicadores técnicos atuais.
    #
    # No histórico, usamos todas.
    # --------------------------------------------------------------

    if modo_historico:

        oportunidades_base = (
            todas_oportunidades
        )

    else:

        oportunidades_base = [
            oportunidade
            for oportunidade
            in todas_oportunidades
            if (
                oportunidade.status
                or ""
            ).strip().lower()
            != "inativa"
        ]

    # ==============================================================
    # PROPOSTAS COM HISTÓRICO COMERCIAL
    # ==============================================================

    STATUS_PROPOSTAS_VISIVEIS = {
        "enviada",
        "ajuste_solicitado",
        "aceita",
        "recusada",
        "nao_selecionada",
        "cancelada"
    }

    propostas = (
        Proposal.query
        .filter(
            Proposal.demand_id
            == demanda.id,

            Proposal.status.in_(
                list(
                    STATUS_PROPOSTAS_VISIVEIS
                )
            )
        )
        .order_by(
            Proposal.id.asc()
        )
        .all()
    )

    propostas_por_empresa = {}

    for proposta in propostas:

        propostas_por_empresa[
            proposta.empresa_id
        ] = proposta

    # ==============================================================
    # QUAIS MALHARIAS PODEM SER IDENTIFICADAS?
    #
    # Regra:
    #
    # 1. oportunidade atualmente "interessada"
    # OU
    # 2. empresa possui histórico de proposta
    #
    # Nunca identificamos:
    # - nova
    # - visualizada
    # - recusada sem proposta
    # ==============================================================

    empresas_reveladas = set()

    for oportunidade in todas_oportunidades:

        status_oportunidade = (
            oportunidade.status
            or ""
        ).strip().lower()

        if status_oportunidade == "interessada":

            empresas_reveladas.add(
                oportunidade.empresa_id
            )

    for proposta in propostas:

        empresas_reveladas.add(
            proposta.empresa_id
        )

    oportunidades_reveladas = [
        oportunidade
        for oportunidade
        in todas_oportunidades
        if oportunidade.empresa_id
        in empresas_reveladas
    ]

    # ==============================================================
    # MATCHES DAS EMPRESAS JÁ REVELADAS
    # ==============================================================

    matches_por_empresa = {}

    for oportunidade in oportunidades_reveladas:

        matches_empresa = (
            DemandMatch.query
            .filter_by(
                demand_id=demanda.id,
                empresa_id=oportunidade.empresa_id,
                status="ativo"
            )
            .order_by(
                DemandMatch.score.desc(),
                DemandMatch.id.asc()
            )
            .all()
        )

        matches_por_empresa[
            oportunidade.empresa_id
        ] = matches_empresa

    # ==============================================================
    # PEDIDO
    # ==============================================================

    pedido = (
        Order.query
        .filter(
            Order.demand_id
            == demanda.id,

            Order.buyer_user_id
            == usuario.id
        )
        .order_by(
            Order.id.desc()
        )
        .first()
    )

    # ==============================================================
    # INDICADORES
    # ==============================================================

    total_compativeis = len(
        oportunidades_base
    )

    total_interessadas = len(
        empresas_reveladas
    )

    total_em_analise = sum(
        1
        for oportunidade
        in oportunidades_base
        if (
            oportunidade.status
            or ""
        ).strip().lower()
        in {
            "nova",
            "visualizada"
        }
    )

    total_recusadas = sum(
        1
        for oportunidade
        in oportunidades_base
        if (
            oportunidade.status
            or ""
        ).strip().lower()
        == "recusada"
    )

    total_propostas_recebidas = len(
        propostas
    )

    total_participacao_comercial = len(
        empresas_reveladas
    )

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "retorno_demanda.html",

        usuario=usuario,

        demanda=demanda,

        oportunidades=
            todas_oportunidades,

        oportunidades_reveladas=
            oportunidades_reveladas,

        matches_por_empresa=
            matches_por_empresa,

        propostas_por_empresa=
            propostas_por_empresa,

        pedido=pedido,

        modo_historico=
            modo_historico,

        total_compativeis=
            total_compativeis,

        total_interessadas=
            total_interessadas,

        total_em_analise=
            total_em_analise,

        total_recusadas=
            total_recusadas,

        total_propostas_recebidas=
            total_propostas_recebidas,

        total_participacao_comercial=
            total_participacao_comercial,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Propostas Recebidas pelo Comprador
# --------------------------------------------------------------------

@app.get(
    "/comprador/demandas/<int:demanda_id>/propostas",
    endpoint="propostas_recebidas"
)
def propostas_recebidas(demanda_id):

    # ==============================================================
    # AUTENTICAÇÃO DO COMPRADOR
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # DEMANDA DO PRÓPRIO COMPRADOR
    # ==============================================================

    demanda = (
        ProductionRequest.query
        .filter_by(
            id=demanda_id,
            user_id=usuario.id
        )
        .first()
    )

    if not demanda:

        flash(
            "Demanda não encontrada.",
            "warning"
        )

        return redirect(
            url_for(
                "minhas_demandas"
            )
        )

    # ==============================================================
    # PROPOSTAS VISÍVEIS AO COMPRADOR
    #
    # Rascunho NÃO aparece porque ainda pertence à elaboração
    # da malharia.
    #
    # Todos os estados que já tiveram participação comercial
    # permanecem visíveis para histórico.
    # ==============================================================

    propostas = (
        Proposal.query
        .filter(
            Proposal.demand_id
            == demanda.id,

            Proposal.status.in_(
                [
                    "enviada",
                    "ajuste_solicitado",
                    "aceita",
                    "recusada",
                    "nao_selecionada",
                    "cancelada"
                ]
            )
        )
        .order_by(
            Proposal.preco_por_kg.asc(),
            Proposal.sent_at.asc(),
            Proposal.id.asc()
        )
        .all()
    )

    # ==============================================================
    # INDICADORES
    # ==============================================================

    total_propostas = len(
        propostas
    )

    total_enviadas = sum(
        1
        for proposta in propostas
        if (
            proposta.status
            or ""
        ).strip().lower()
        == "enviada"
    )

    total_ajustes = sum(
        1
        for proposta in propostas
        if (
            proposta.status
            or ""
        ).strip().lower()
        == "ajuste_solicitado"
    )

    total_aceitas = sum(
        1
        for proposta in propostas
        if (
            proposta.status
            or ""
        ).strip().lower()
        == "aceita"
    )

    total_recusadas = sum(
        1
        for proposta in propostas
        if (
            proposta.status
            or ""
        ).strip().lower()
        == "recusada"
    )

    # --------------------------------------------------------------
    # Encerradas por decisão/sistema
    #
    # Variável adicional para manter semântica clara.
    # O template atual pode não exibi-la ainda.
    # --------------------------------------------------------------

    total_encerradas = sum(
        1
        for proposta in propostas
        if (
            proposta.status
            or ""
        ).strip().lower()
        in {
            "recusada",
            "nao_selecionada",
            "cancelada"
        }
    )

    # ==============================================================
    # VALORES TOTAIS
    # ==============================================================

    totais_propostas = {}

    for proposta in propostas:

        try:

            total = (
                proposta.quantidade_kg
                * proposta.preco_por_kg
            )

        except Exception:

            total = None

        totais_propostas[
            proposta.id
        ] = total

    # ==============================================================
    # ÚLTIMA SOLICITAÇÃO DE AJUSTE POR PROPOSTA
    # ==============================================================

    ajustes_por_proposta = {}

    for proposta in propostas:

        ajuste = (
            ProposalInteraction.query
            .filter_by(
                proposal_id=proposta.id,
                action="ajuste_solicitado"
            )
            .order_by(
                ProposalInteraction.created_at.desc()
            )
            .first()
        )

        if ajuste:

            ajustes_por_proposta[
                proposta.id
            ] = ajuste

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "propostas_recebidas.html",

        usuario=usuario,

        demanda=demanda,

        propostas=propostas,

        totais_propostas=
            totais_propostas,

        ajustes_por_proposta=
            ajustes_por_proposta,

        total_propostas=
            total_propostas,

        total_enviadas=
            total_enviadas,

        total_ajustes=
            total_ajustes,

        total_aceitas=
            total_aceitas,

        total_recusadas=
            total_recusadas,

        total_encerradas=
            total_encerradas,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Comprador aceita proposta
# --------------------------------------------------------------------

@app.post(
    "/comprador/propostas/<int:proposta_id>/aceitar",
    endpoint="aceitar_proposta"
)
def aceitar_proposta(proposta_id):

    # --------------------------------------------------------------
    # Comprador autenticado
    # --------------------------------------------------------------

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role or ""
        ).strip().lower() != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Proposta
    #
    # Garante que pertence a uma demanda do comprador logado.
    # --------------------------------------------------------------

    proposta = (
        Proposal.query
        .join(
            ProductionRequest,
            Proposal.demand_id
            == ProductionRequest.id
        )
        .filter(
            Proposal.id
            == proposta_id,

            ProductionRequest.user_id
            == usuario.id
        )
        .first()
    )

    if not proposta:

        flash(
            "Proposta não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    # --------------------------------------------------------------
    # Demanda
    # --------------------------------------------------------------

    demanda = proposta.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    status_demanda = (
        demanda.status or ""
    ).strip().lower()

    # --------------------------------------------------------------
    # Uma proposta só pode ser aceita enquanto a demanda
    # ainda estiver publicada.
    #
    # Depois de contratada/encerrada, nenhuma nova proposta
    # pode avançar.
    # --------------------------------------------------------------

    if status_demanda != "publicada":

        flash(
            (
                "Esta demanda não está mais disponível "
                "para aceite de propostas."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Status atual da proposta
    # --------------------------------------------------------------

    status_atual = (
        proposta.status or ""
    ).strip().lower()

    if status_atual != "enviada":

        flash(
            (
                "Esta proposta não está disponível "
                "para aceite no status atual."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Regra atual:
    # somente UMA proposta aceita por demanda
    # --------------------------------------------------------------

    outra_aceita = (
        Proposal.query
        .filter(
            Proposal.demand_id
            == demanda.id,

            Proposal.id
            != proposta.id,

            Proposal.status
            == "aceita"
        )
        .first()
    )

    if outra_aceita:

        flash(
            (
                "Já existe uma proposta aceita "
                "para esta demanda."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Proteção adicional:
    # se já houver pedido na demanda, não permite novo aceite.
    # --------------------------------------------------------------

    pedido_existente = (
        Order.query
        .filter_by(
            demand_id=demanda.id
        )
        .first()
    )

    if pedido_existente:

        flash(
            (
                f"A demanda já originou o pedido "
                f"{pedido_existente.codigo}."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Aceite
    # --------------------------------------------------------------

    try:

        proposta.status = "aceita"

        interacao = ProposalInteraction(
            proposal_id=proposta.id,
            actor_role="comprador",
            action="aceita",
            message=(
                "Proposta aceita pelo comprador."
            )
        )

        db.session.add(
            interacao
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PROPOSTA] Falha ao aceitar proposta."
        )

        flash(
            "Não foi possível aceitar a proposta agora.",
            "danger"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    flash(
        "Proposta aceita com sucesso.",
        "success"
    )

    return redirect(
        url_for(
            "propostas_recebidas",
            demanda_id=demanda.id
        )
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Comprador recusa proposta
# --------------------------------------------------------------------

@app.post(
    "/comprador/propostas/<int:proposta_id>/recusar",
    endpoint="recusar_proposta"
)
def recusar_proposta(proposta_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # PROPOSTA
    #
    # Garante que pertence a uma demanda do comprador autenticado.
    # ==============================================================

    proposta = (
        Proposal.query
        .join(
            ProductionRequest,
            Proposal.demand_id
            == ProductionRequest.id
        )
        .filter(
            Proposal.id
            == proposta_id,

            ProductionRequest.user_id
            == usuario.id
        )
        .first()
    )

    if not proposta:

        flash(
            "Proposta não encontrada.",
            "warning"
        )

        return redirect(
            url_for(
                "minhas_demandas"
            )
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = proposta.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for(
                "minhas_demandas"
            )
        )

    status_demanda = (
        demanda.status
        or ""
    ).strip().lower()

    # ==============================================================
    # BLINDAGEM DA DEMANDA
    #
    # Nenhuma decisão comercial nova pode ocorrer depois
    # que a demanda sair de PUBLICADA.
    # ==============================================================

    if status_demanda != "publicada":

        flash(
            (
                "Esta demanda não está mais aberta "
                "para decisões sobre propostas."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # STATUS DA PROPOSTA
    #
    # Somente uma proposta ENVIADA pode ser recusada.
    # ==============================================================

    status_atual = (
        proposta.status
        or ""
    ).strip().lower()

    if status_atual != "enviada":

        flash(
            (
                "Esta proposta não pode ser recusada "
                "no status atual."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # PROTEÇÃO CONTRA PEDIDO EXISTENTE
    #
    # É redundante quando o ciclo está consistente, mas protege
    # contra registros antigos ou alterações manuais de status.
    # ==============================================================

    pedido_existente = (
        Order.query
        .filter_by(
            demand_id=demanda.id
        )
        .first()
    )

    if pedido_existente:

        flash(
            (
                f"Esta demanda já originou o pedido "
                f"{pedido_existente.codigo} "
                "e não aceita novas decisões comerciais."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # RECUSA
    # ==============================================================

    try:

        proposta.status = (
            "recusada"
        )

        interacao = ProposalInteraction(
            proposal_id=proposta.id,
            actor_role="comprador",
            action="recusada",
            message=(
                "Proposta recusada pelo comprador."
            )
        )

        db.session.add(
            interacao
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PROPOSTA] Falha ao recusar proposta."
        )

        flash(
            "Não foi possível recusar a proposta agora.",
            "danger"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    flash(
        "Proposta recusada.",
        "success"
    )

    return redirect(
        url_for(
            "propostas_recebidas",
            demanda_id=demanda.id
        )
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Comprador solicita ajuste da proposta
# --------------------------------------------------------------------

@app.post(
    "/comprador/propostas/<int:proposta_id>/solicitar-ajuste",
    endpoint="solicitar_ajuste_proposta"
)
def solicitar_ajuste_proposta(proposta_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # PROPOSTA
    #
    # Somente proposta pertencente ao comprador autenticado.
    # ==============================================================

    proposta = (
        Proposal.query
        .join(
            ProductionRequest,
            Proposal.demand_id
            == ProductionRequest.id
        )
        .filter(
            Proposal.id
            == proposta_id,

            ProductionRequest.user_id
            == usuario.id
        )
        .first()
    )

    if not proposta:

        flash(
            "Proposta não encontrada.",
            "warning"
        )

        return redirect(
            url_for(
                "minhas_demandas"
            )
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = proposta.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for(
                "minhas_demandas"
            )
        )

    status_demanda = (
        demanda.status
        or ""
    ).strip().lower()

    # ==============================================================
    # BLINDAGEM DA DEMANDA
    # ==============================================================

    if status_demanda != "publicada":

        flash(
            (
                "Esta demanda não está mais aberta "
                "para negociação de propostas."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # STATUS DA PROPOSTA
    #
    # Ajuste somente pode ser solicitado quando a proposta
    # está efetivamente aguardando decisão do comprador.
    # ==============================================================

    status_proposta = (
        proposta.status
        or ""
    ).strip().lower()

    if status_proposta != "enviada":

        flash(
            (
                "Não é possível solicitar ajuste desta "
                "proposta no status atual."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # PROTEÇÃO CONTRA PEDIDO EXISTENTE
    # ==============================================================

    pedido_existente = (
        Order.query
        .filter_by(
            demand_id=demanda.id
        )
        .first()
    )

    if pedido_existente:

        flash(
            (
                f"Esta demanda já originou o pedido "
                f"{pedido_existente.codigo} "
                "e não aceita novos ajustes comerciais."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # MENSAGEM DO AJUSTE
    # ==============================================================

    mensagem = (
        request.form.get(
            "mensagem_ajuste"
        )
        or ""
    ).strip()

    if len(mensagem) < 5:

        flash(
            "Descreva o ajuste que deseja solicitar.",
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # ==============================================================
    # REGISTRA SOLICITAÇÃO
    # ==============================================================

    try:

        proposta.status = (
            "ajuste_solicitado"
        )

        interacao = ProposalInteraction(
            proposal_id=proposta.id,
            actor_role="comprador",
            action="ajuste_solicitado",
            message=mensagem
        )

        db.session.add(
            interacao
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PROPOSTA] Falha ao solicitar ajuste."
        )

        flash(
            "Não foi possível solicitar o ajuste agora.",
            "danger"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    flash(
        "Solicitação de ajuste enviada à malharia.",
        "success"
    )

    return redirect(
        url_for(
            "propostas_recebidas",
            demanda_id=demanda.id
        )
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Gerar Pedido a partir de Proposta Aceita
# --------------------------------------------------------------------

@app.post(
    "/comprador/propostas/<int:proposta_id>/gerar-pedido",
    endpoint="gerar_pedido"
)
def gerar_pedido(proposta_id):

    # --------------------------------------------------------------
    # Autenticação do comprador
    # --------------------------------------------------------------

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role or ""
        ).strip().lower() != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Proposta
    #
    # Garante que pertence ao comprador logado.
    # --------------------------------------------------------------

    proposta = (
        Proposal.query
        .join(
            ProductionRequest,
            Proposal.demand_id
            == ProductionRequest.id
        )
        .filter(
            Proposal.id
            == proposta_id,

            ProductionRequest.user_id
            == usuario.id
        )
        .first()
    )

    if not proposta:

        flash(
            "Proposta não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    # --------------------------------------------------------------
    # Somente proposta ACEITA pode gerar pedido
    # --------------------------------------------------------------

    status_proposta = (
        proposta.status or ""
    ).strip().lower()

    if status_proposta != "aceita":

        flash(
            (
                "Somente uma proposta aceita "
                "pode gerar um pedido."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=proposta.demand_id
            )
        )

    # --------------------------------------------------------------
    # Proteção contra pedido duplicado
    # --------------------------------------------------------------

    pedido_existente = (
        Order.query
        .filter_by(
            proposal_id=proposta.id
        )
        .first()
    )

    if pedido_existente:

        flash(
            (
                f"O pedido "
                f"{pedido_existente.codigo} "
                f"já foi criado para esta proposta."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=proposta.demand_id
            )
        )

    # --------------------------------------------------------------
    # Proteção adicional:
    # uma demanda só pode gerar um pedido
    # --------------------------------------------------------------

    pedido_da_demanda = (
        Order.query
        .filter_by(
            demand_id=proposta.demand_id
        )
        .first()
    )

    if pedido_da_demanda:

        flash(
            (
                f"A demanda já originou o pedido "
                f"{pedido_da_demanda.codigo}."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=proposta.demand_id
            )
        )

    # --------------------------------------------------------------
    # Demanda
    # --------------------------------------------------------------

    demanda = proposta.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_demandas")
        )

    status_demanda = (
        demanda.status or ""
    ).strip().lower()

    # --------------------------------------------------------------
    # Para gerar o pedido, a demanda ainda precisa estar publicada.
    # --------------------------------------------------------------

    if status_demanda != "publicada":

        flash(
            (
                "Esta demanda não está disponível "
                "para geração de um novo pedido."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Malharia
    # --------------------------------------------------------------

    empresa = proposta.empresa

    if not empresa:

        flash(
            "A malharia vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Valor total contratado
    # --------------------------------------------------------------

    try:

        valor_total = (
            proposta.quantidade_kg
            * proposta.preco_por_kg
        )

    except Exception:

        current_app.logger.exception(
            "[PEDIDO] Falha ao calcular valor total."
        )

        flash(
            "Não foi possível calcular o valor do pedido.",
            "danger"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Criação do pedido + consolidação da demanda
    # --------------------------------------------------------------

    try:

        pedido = Order(
            proposal_id=proposta.id,
            demand_id=demanda.id,
            buyer_user_id=usuario.id,
            empresa_id=empresa.id,

            quantidade_kg=proposta.quantidade_kg,
            preco_por_kg=proposta.preco_por_kg,
            valor_total=valor_total,
            prazo_dias=proposta.prazo_dias,

            condicoes_pagamento=(
                proposta.condicoes_pagamento
                or None
            ),

            observacoes=(
                proposta.observacoes
                or None
            ),

            status="aguardando_confirmacao"
        )

        db.session.add(
            pedido
        )

        # ----------------------------------------------------------
        # Precisamos do ID para gerar ATP-000001
        # ----------------------------------------------------------

        db.session.flush()

        pedido.codigo = (
            f"ATP-{pedido.id:06d}"
        )

        # ==========================================================
        # HISTÓRICO OPERACIONAL — PEDIDO CRIADO
        # ==========================================================
        
        evento_pedido_criado = OrderEvent(
            order_id=pedido.id,
            actor_role="sistema",
            action="pedido_criado",
            status_anterior=None,
            status_novo="aguardando_confirmacao",
            message=(
                f"O pedido {pedido.codigo} "
                "foi criado a partir da proposta aceita."
            )
        )
        
        db.session.add(
            evento_pedido_criado
        )

        # ==========================================================
        # DEMANDA PASSA PARA CONTRATADA
        # ==========================================================

        demanda.status = "contratada"

        # ==========================================================
        # ENCERRA AS OUTRAS PROPOSTAS
        #
        # Elas continuam armazenadas no banco para histórico,
        # porém não estão mais concorrendo pela demanda.
        # ==========================================================

        propostas_concorrentes = (
            Proposal.query
            .filter(
                Proposal.demand_id
                == demanda.id,

                Proposal.id
                != proposta.id,

                Proposal.status.in_(
                    [
                        "rascunho",
                        "enviada",
                        "ajuste_solicitado"
                    ]
                )
            )
            .all()
        )

        for proposta_concorrente in propostas_concorrentes:

            proposta_concorrente.status = (
                "nao_selecionada"
            )

            interacao_concorrente = ProposalInteraction(
                proposal_id=proposta_concorrente.id,
                actor_role="sistema",
                action="nao_selecionada",
                message=(
                    f"Proposta encerrada após a "
                    f"contratação do pedido "
                    f"{pedido.codigo}."
                )
            )

            db.session.add(
                interacao_concorrente
            )

        # ==========================================================
        # ENCERRA TODAS AS OPORTUNIDADES DA DEMANDA
        #
        # A negociação agora migra para o módulo de Pedidos.
        # ==========================================================

        oportunidades = (
            Opportunity.query
            .filter_by(
                demand_id=demanda.id
            )
            .all()
        )

        for oportunidade in oportunidades:

            oportunidade.status = "inativa"

        # ----------------------------------------------------------
        # Histórico da proposta escolhida
        # ----------------------------------------------------------

        interacao = ProposalInteraction(
            proposal_id=proposta.id,
            actor_role="sistema",
            action="pedido_gerado",
            message=(
                f"Pedido {pedido.codigo} "
                f"gerado a partir da proposta aceita."
            )
        )

        db.session.add(
            interacao
        )

        # ----------------------------------------------------------
        # Salva tudo em uma única transação
        # ----------------------------------------------------------

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PEDIDO] Falha ao gerar pedido."
        )

        flash(
            "Não foi possível gerar o pedido agora.",
            "danger"
        )

        return redirect(
            url_for(
                "propostas_recebidas",
                demanda_id=demanda.id
            )
        )

    # --------------------------------------------------------------
    # Sucesso
    # --------------------------------------------------------------

    flash(
        (
            f"Pedido {pedido.codigo} "
            f"criado com sucesso."
        ),
        "success"
    )

    return redirect(
        url_for(
            "propostas_recebidas",
            demanda_id=demanda.id
        )
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Comprador confirma Entrega
# --------------------------------------------------------------------

@app.post(
    "/comprador/pedidos/<int:pedido_id>/confirmar-entrega",
    endpoint="confirmar_entrega_comprador"
)
def confirmar_entrega_comprador(pedido_id):

    # --------------------------------------------------------------
    # Autenticação do comprador
    # --------------------------------------------------------------

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role or ""
        ).strip().lower() != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Pedido somente do próprio comprador
    # --------------------------------------------------------------

    pedido = (
        Order.query
        .filter_by(
            id=pedido_id,
            buyer_user_id=usuario.id
        )
        .first()
    )

    if not pedido:

        flash(
            "Pedido não encontrado.",
            "warning"
        )

        return redirect(
            url_for("meus_pedidos_comprador")
        )

    status_atual = (
        pedido.status or ""
    ).strip().lower()

    # --------------------------------------------------------------
    # Entrega só pode ser confirmada após conclusão
    # --------------------------------------------------------------

    if status_atual != "concluido":

        flash(
            (
                "A entrega somente pode ser confirmada "
                "após a conclusão da produção."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_pedido_comprador",
                pedido_id=pedido.id
            )
        )

    # --------------------------------------------------------------
    # Demanda vinculada
    # --------------------------------------------------------------

    demanda = pedido.demanda

    # --------------------------------------------------------------
    # Confirma entrega + encerra ciclo
    # --------------------------------------------------------------

    try:

        # ==========================================================
        # PEDIDO
        # ==========================================================

        pedido.status = "entregue"

        # ==========================================================
        # DEMANDA
        #
        # Pedido entregue significa:
        # demanda comercialmente encerrada.
        # ==========================================================

        if demanda:

            status_demanda = (
                demanda.status or ""
            ).strip().lower()

            if status_demanda != "cancelada":

                demanda.status = "encerrada"

        # ==========================================================
        # GARANTIA:
        # nenhuma oportunidade dessa demanda continua aberta
        # ==========================================================

        if demanda:

            oportunidades = (
                Opportunity.query
                .filter_by(
                    demand_id=demanda.id
                )
                .all()
            )

            for oportunidade in oportunidades:

                oportunidade.status = "inativa"

        # ==========================================================
        # GARANTIA:
        # nenhuma proposta concorrente permanece aberta
        # ==========================================================

        if demanda:

            propostas_abertas = (
                Proposal.query
                .filter(
                    Proposal.demand_id
                    == demanda.id,

                    Proposal.id
                    != pedido.proposal_id,

                    Proposal.status.in_(
                        [
                            "rascunho",
                            "enviada",
                            "ajuste_solicitado"
                        ]
                    )
                )
                .all()
            )

            for proposta_aberta in propostas_abertas:

                proposta_aberta.status = (
                    "nao_selecionada"
                )

                interacao = ProposalInteraction(
                    proposal_id=proposta_aberta.id,
                    actor_role="sistema",
                    action="nao_selecionada",
                    message=(
                        f"Proposta encerrada após "
                        f"a conclusão do pedido "
                        f"{pedido.codigo}."
                    )
                )

                db.session.add(
                    interacao
                )

        # ==========================================================
        # HISTÓRICO OPERACIONAL
        # ==========================================================

        evento = OrderEvent(
            order_id=pedido.id,
            actor_role="comprador",
            action="entrega_confirmada",
            status_anterior="concluido",
            status_novo="entregue",
            message=(
                f"O comprador confirmou a entrega "
                f"do pedido {pedido.codigo}."
            )
        )

        db.session.add(
            evento
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PEDIDO] Falha ao confirmar entrega."
        )

        flash(
            "Não foi possível confirmar a entrega agora.",
            "danger"
        )

        return redirect(
            url_for(
                "detalhe_pedido_comprador",
                pedido_id=pedido.id
            )
        )

    flash(
        (
            f"Entrega do pedido "
            f"{pedido.codigo} confirmada."
        ),
        "success"
    )

    return redirect(
        url_for(
            "detalhe_pedido_comprador",
            pedido_id=pedido.id
        )
    )

# --------------------------------------------------------------------
# Portal do Comprador - AcheTece 2.0
# --------------------------------------------------------------------

@app.route(
    '/painel_comprador',
    endpoint='painel_comprador'
)
def painel_comprador():

    """
    Painel inicial do comprador.

    - exige Usuario autenticado;
    - exige role='cliente';
    - carrega ClienteProfile;
    - calcula indicadores reais;
    - reconcilia registros antigos do staging;
    - preserva separação entre comprador e malharia.
    """

    # --------------------------------------------------------------
    # Verifica sessão
    # --------------------------------------------------------------

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Carrega usuário
    # --------------------------------------------------------------

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
    ):

        session.clear()

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Verifica perfil
    # --------------------------------------------------------------

    role = (
        usuario.role or ""
    ).strip().lower()

    if role != "cliente":

        # Caso seja uma malharia,
        # leva ao painel correto.
        if getattr(
            usuario,
            "empresa",
            None
        ):

            return redirect(
                url_for("painel_malharia")
            )

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Perfil do comprador
    # --------------------------------------------------------------

    perfil = (
        ClienteProfile.query
        .filter_by(
            user_id=usuario.id
        )
        .first()
    )

    # ==============================================================
    # RECONCILIAÇÃO DE REGISTROS ANTIGOS
    #
    # Importante para o STAGING:
    #
    # o ATP-000001 foi criado antes da ETAPA 8.1.
    #
    # Portanto:
    #
    # pedido ativo
    #      → demanda contratada
    #
    # pedido entregue
    #      → demanda encerrada
    #
    # Também encerra oportunidades e propostas concorrentes
    # que tenham permanecido abertas.
    #
    # É idempotente:
    # depois de corrigido, não altera novamente.
    # ==============================================================

    try:

        pedidos_reconciliacao = (
            Order.query
            .filter(
                Order.buyer_user_id
                == usuario.id,

                Order.status
                != "cancelado"
            )
            .all()
        )

        houve_alteracao = False

        for pedido in pedidos_reconciliacao:

            demanda = pedido.demanda

            if not demanda:
                continue

            status_pedido = (
                pedido.status or ""
            ).strip().lower()

            status_demanda = (
                demanda.status or ""
            ).strip().lower()

            # ------------------------------------------------------
            # Não sobrescreve demanda cancelada
            # ------------------------------------------------------

            if status_demanda != "cancelada":

                novo_status_demanda = None

                # Pedido finalizado
                if status_pedido == "entregue":

                    novo_status_demanda = (
                        "encerrada"
                    )

                # Pedido ainda operacionalmente ativo
                elif status_pedido in {
                    "aguardando_confirmacao",
                    "confirmado",
                    "em_producao",
                    "concluido"
                }:

                    novo_status_demanda = (
                        "contratada"
                    )

                if (
                    novo_status_demanda
                    and status_demanda
                    != novo_status_demanda
                ):

                    demanda.status = (
                        novo_status_demanda
                    )

                    houve_alteracao = True

            # ------------------------------------------------------
            # Uma demanda com pedido não deve continuar
            # apresentando oportunidades abertas.
            # ------------------------------------------------------

            oportunidades_abertas = (
                Opportunity.query
                .filter(
                    Opportunity.demand_id
                    == demanda.id,

                    Opportunity.status
                    != "inativa"
                )
                .all()
            )

            for oportunidade in oportunidades_abertas:

                oportunidade.status = "inativa"

                houve_alteracao = True

            # ------------------------------------------------------
            # Outras propostas ainda abertas passam para
            # não selecionadas.
            # ------------------------------------------------------

            propostas_concorrentes = (
                Proposal.query
                .filter(
                    Proposal.demand_id
                    == demanda.id,

                    Proposal.id
                    != pedido.proposal_id,

                    Proposal.status.in_(
                        [
                            "rascunho",
                            "enviada",
                            "ajuste_solicitado"
                        ]
                    )
                )
                .all()
            )

            for proposta_concorrente in propostas_concorrentes:

                proposta_concorrente.status = (
                    "nao_selecionada"
                )

                interacao = ProposalInteraction(
                    proposal_id=proposta_concorrente.id,
                    actor_role="sistema",
                    action="nao_selecionada",
                    message=(
                        f"Proposta encerrada durante "
                        f"a consolidação do pedido "
                        f"{pedido.codigo}."
                    )
                )

                db.session.add(
                    interacao
                )

                houve_alteracao = True

        if houve_alteracao:

            db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[CONSOLIDACAO] Falha ao reconciliar "
            "status do marketplace."
        )

    # ==============================================================
    # INDICADORES REAIS DO PORTAL
    # ==============================================================

    # --------------------------------------------------------------
    # Demandas ativas
    #
    # rascunho
    # publicada
    # contratada
    #
    # encerrada e cancelada ficam fora.
    # --------------------------------------------------------------

    demandas_ativas = (
        ProductionRequest.query
        .filter(
            ProductionRequest.user_id
            == usuario.id,

            ProductionRequest.status.in_(
                [
                    "rascunho",
                    "publicada",
                    "contratada"
                ]
            )
        )
        .count()
    )

    # --------------------------------------------------------------
    # Matches encontrados
    # --------------------------------------------------------------

    matches_total = (
        db.session.query(
            DemandMatch.id
        )
        .join(
            ProductionRequest,
            DemandMatch.demand_id
            == ProductionRequest.id
        )
        .filter(
            ProductionRequest.user_id
            == usuario.id,

            DemandMatch.status
            == "ativo"
        )
        .count()
    )

    # --------------------------------------------------------------
    # Propostas recebidas
    #
    # nao_selecionada continua sendo uma proposta que
    # realmente foi recebida, portanto conta no histórico.
    # --------------------------------------------------------------

    propostas_total = (
        db.session.query(
            Proposal.id
        )
        .join(
            ProductionRequest,
            Proposal.demand_id
            == ProductionRequest.id
        )
        .filter(
            ProductionRequest.user_id
            == usuario.id,

            Proposal.status.in_(
                [
                    "enviada",
                    "ajuste_solicitado",
                    "aceita",
                    "recusada",
                    "nao_selecionada"
                ]
            )
        )
        .count()
    )

    # --------------------------------------------------------------
    # Pedidos em andamento
    #
    # Entregues NÃO contam mais como em andamento.
    # --------------------------------------------------------------

    pedidos_total = (
        Order.query
        .filter(
            Order.buyer_user_id
            == usuario.id,

            Order.status.in_(
                [
                    "aguardando_confirmacao",
                    "confirmado",
                    "em_producao",
                    "concluido"
                ]
            )
        )
        .count()
    )

    # --------------------------------------------------------------
    # Renderiza Portal do Comprador
    # --------------------------------------------------------------

    return render_template(
        "painel_comprador.html",
        usuario=usuario,
        perfil=perfil,
        demandas_ativas=demandas_ativas,
        matches_total=matches_total,
        propostas_total=propostas_total,
        pedidos_total=pedidos_total,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Visão Geral dos Matches do Comprador
# --------------------------------------------------------------------

@app.get(
    "/comprador/matches",
    endpoint="matches_comprador"
)
def matches_comprador():

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # TODOS OS MATCHES DAS DEMANDAS DO COMPRADOR
    #
    # Inclui histórico.
    # ==============================================================

    matches = (
        DemandMatch.query
        .join(
            ProductionRequest,
            DemandMatch.demand_id
            == ProductionRequest.id
        )
        .filter(
            ProductionRequest.user_id
            == usuario.id
        )
        .order_by(
            DemandMatch.id.desc()
        )
        .all()
    )

    # ==============================================================
    # IDENTIFICAÇÃO ANÔNIMA
    #
    # O número é reiniciado para cada demanda.
    #
    # Exemplo:
    #
    # ATD-000001
    # - Malharia compatível 1
    # - Malharia compatível 2
    #
    # ATD-000002
    # - Malharia compatível 1
    # ==============================================================

    mapa_por_demanda = {}

    anonimo_por_match = {}

    for match in matches:

        demanda_id = (
            match.demand_id
        )

        empresa_id = (
            match.empresa_id
        )

        if (
            not demanda_id
            or not empresa_id
        ):

            anonimo_por_match[
                match.id
            ] = "Malharia compatível"

            continue

        mapa_demanda = (
            mapa_por_demanda.setdefault(
                demanda_id,
                {}
            )
        )

        if empresa_id not in mapa_demanda:

            numero = (
                len(mapa_demanda)
                + 1
            )

            mapa_demanda[
                empresa_id
            ] = (
                f"Malharia compatível "
                f"{numero}"
            )

        anonimo_por_match[
            match.id
        ] = mapa_demanda[
            empresa_id
        ]

    # ==============================================================
    # INDICADORES
    # ==============================================================

    total_matches = len(
        matches
    )

    demandas_com_matches = len(
        {
            match.demand_id
            for match in matches
            if match.demand_id
        }
    )

    malharias_encontradas = len(
        {
            match.empresa_id
            for match in matches
            if match.empresa_id
        }
    )

    matches_historicos = sum(
        1
        for match in matches
        if (
            match.demanda
            and (
                match.demanda.status
                or ""
            ).strip().lower()
            in {
                "contratada",
                "encerrada"
            }
        )
    )

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "matches_comprador.html",

        usuario=usuario,

        matches=matches,

        anonimo_por_match=
            anonimo_por_match,

        total_matches=
            total_matches,

        demandas_com_matches=
            demandas_com_matches,

        malharias_encontradas=
            malharias_encontradas,

        matches_historicos=
            matches_historicos,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Visão Geral das Propostas do Comprador
# --------------------------------------------------------------------

@app.get(
    "/comprador/propostas",
    endpoint="propostas_comprador"
)
def propostas_comprador():

    # --------------------------------------------------------------
    # Autenticação
    # --------------------------------------------------------------

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role or ""
        ).strip().lower() != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Histórico de propostas recebidas
    # --------------------------------------------------------------

    propostas = (
        Proposal.query
        .join(
            ProductionRequest,
            Proposal.demand_id
            == ProductionRequest.id
        )
        .filter(
            ProductionRequest.user_id
            == usuario.id,

            Proposal.status.in_(
                [
                    "enviada",
                    "ajuste_solicitado",
                    "aceita",
                    "recusada",
                    "nao_selecionada",
                    "cancelada"
                ]
            )
        )
        .order_by(
            Proposal.created_at.desc(),
            Proposal.id.desc()
        )
        .all()
    )

    # --------------------------------------------------------------
    # Indicadores
    # --------------------------------------------------------------

    total_propostas = len(
        propostas
    )

    total_aguardando = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        == "enviada"
    )

    total_ajustes = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        == "ajuste_solicitado"
    )

    total_aceitas = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        == "aceita"
    )

    total_encerradas = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        in {
            "recusada",
            "nao_selecionada",
            "cancelada"
        }
    )

    # --------------------------------------------------------------
    # Valores totais
    # --------------------------------------------------------------

    totais_propostas = {}

    for proposta in propostas:

        try:

            total = (
                proposta.quantidade_kg
                * proposta.preco_por_kg
            )

        except Exception:

            total = None

        totais_propostas[
            proposta.id
        ] = total

    return render_template(
        "propostas_comprador.html",
        usuario=usuario,
        propostas=propostas,
        totais_propostas=totais_propostas,
        total_propostas=total_propostas,
        total_aguardando=total_aguardando,
        total_ajustes=total_ajustes,
        total_aceitas=total_aceitas,
        total_encerradas=total_encerradas,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Meus Pedidos do Comprador
# --------------------------------------------------------------------

@app.get(
    "/comprador/pedidos",
    endpoint="meus_pedidos_comprador"
)
def meus_pedidos_comprador():

    # ==============================================================
    # AUTENTICAÇÃO DO COMPRADOR
    # ==============================================================

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role
            or ""
        ).strip().lower()
        != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # ==============================================================
    # PEDIDOS DO COMPRADOR
    #
    # Mantemos aqui o comportamento atual:
    # pedidos cancelados não entram na listagem operacional.
    # ==============================================================

    pedidos = (
        Order.query
        .filter(
            Order.buyer_user_id
            == usuario.id,

            Order.status
            != "cancelado"
        )
        .order_by(
            Order.created_at.desc(),
            Order.id.desc()
        )
        .all()
    )

    # ==============================================================
    # INDICADORES
    #
    # Cada indicador representa o STATUS ATUAL do pedido.
    #
    # Importante:
    # "concluido" = produção concluída.
    # "entregue"  = ciclo operacional encerrado.
    #
    # Portanto, entregue NÃO entra em total_concluidos.
    # ==============================================================

    total_pedidos = len(
        pedidos
    )

    total_aguardando = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "aguardando_confirmacao"
    )

    total_confirmados = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "confirmado"
    )

    total_em_producao = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "em_producao"
    )

    total_concluidos = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "concluido"
    )

    # --------------------------------------------------------------
    # Entregues
    #
    # Já calculamos mesmo que o template atual não possua
    # um card específico. Isso deixa o backend preparado para
    # a evolução dos indicadores.
    # --------------------------------------------------------------

    total_entregues = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "entregue"
    )

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "meus_pedidos_comprador.html",

        usuario=usuario,

        pedidos=pedidos,

        total_pedidos=
            total_pedidos,

        total_aguardando=
            total_aguardando,

        total_confirmados=
            total_confirmados,

        total_em_producao=
            total_em_producao,

        total_concluidos=
            total_concluidos,

        total_entregues=
            total_entregues,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Detalhes do Pedido para Comprador
# --------------------------------------------------------------------

@app.get(
    "/comprador/pedidos/<int:pedido_id>",
    endpoint="detalhe_pedido_comprador"
)
def detalhe_pedido_comprador(pedido_id):

    # --------------------------------------------------------------
    # Autenticação
    # --------------------------------------------------------------

    user_id = session.get(
        "user_id"
    )

    if not user_id:

        return redirect(
            url_for("login")
        )

    try:

        usuario = db.session.get(
            Usuario,
            int(user_id)
        )

    except Exception:

        usuario = None

    if (
        not usuario
        or usuario.is_active is False
        or (
            usuario.role or ""
        ).strip().lower() != "cliente"
    ):

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Pedido SOMENTE do próprio comprador
    # --------------------------------------------------------------

    pedido = (
        Order.query
        .filter_by(
            id=pedido_id,
            buyer_user_id=usuario.id
        )
        .first()
    )

    if not pedido:

        flash(
            "Pedido não encontrado.",
            "warning"
        )

        return redirect(
            url_for(
                "meus_pedidos_comprador"
            )
        )

    proposta = pedido.proposta
    demanda = pedido.demanda
    empresa = pedido.empresa
    
    historico_operacional = (
        _montar_historico_operacional_pedido(
            pedido
        )
    )
    
    return render_template(
        "detalhe_pedido_comprador.html",
    
        usuario=usuario,
    
        pedido=pedido,
    
        proposta=proposta,
    
        demanda=demanda,
    
        empresa=empresa,
    
        historico_operacional=
            historico_operacional,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Minhas Oportunidades da Malharia
# --------------------------------------------------------------------

@app.get(
    "/malharia/oportunidades",
    endpoint="minhas_oportunidades"
)
def minhas_oportunidades():

    # ==============================================================
    # AUTENTICAÇÃO DA MALHARIA
    # ==============================================================

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

    # ==============================================================
    # TODAS AS OPORTUNIDADES DA MALHARIA
    #
    # Importante:
    # NÃO excluímos mais "inativa".
    #
    # As oportunidades encerradas passam a formar
    # o histórico comercial da empresa.
    # ==============================================================

    oportunidades = (
        Opportunity.query
        .filter(
            Opportunity.empresa_id
            == empresa.id
        )
        .order_by(
            Opportunity.created_at.desc(),
            Opportunity.best_score.desc()
        )
        .all()
    )

    # ==============================================================
    # PROPOSTAS RELACIONADAS ÀS OPORTUNIDADES
    # ==============================================================

    oportunidade_ids = [
        oportunidade.id
        for oportunidade
        in oportunidades
    ]

    propostas = []

    if oportunidade_ids:

        propostas = (
            Proposal.query
            .filter(
                Proposal.opportunity_id.in_(
                    oportunidade_ids
                )
            )
            .all()
        )

    # --------------------------------------------------------------
    # Uma proposta por oportunidade
    # --------------------------------------------------------------

    propostas_por_oportunidade = {
        proposta.opportunity_id:
            proposta
        for proposta
        in propostas
    }

    # ==============================================================
    # PEDIDOS RELACIONADOS ÀS PROPOSTAS
    # ==============================================================

    proposta_ids = [
        proposta.id
        for proposta
        in propostas
    ]

    pedidos = []

    if proposta_ids:

        pedidos = (
            Order.query
            .filter(
                Order.proposal_id.in_(
                    proposta_ids
                )
            )
            .all()
        )

    pedidos_por_proposta = {
        pedido.proposal_id:
            pedido
        for pedido
        in pedidos
    }

    # ==============================================================
    # SEPARAÇÃO:
    # ATIVAS X HISTÓRICO
    # ==============================================================

    STATUS_ATIVOS = {
        "nova",
        "visualizada",
        "interessada"
    }

    oportunidades_ativas = []

    oportunidades_historico = []

    for oportunidade in oportunidades:

        status = (
            oportunidade.status
            or "nova"
        ).strip().lower()

        if status in STATUS_ATIVOS:

            oportunidades_ativas.append(
                oportunidade
            )

        else:

            oportunidades_historico.append(
                oportunidade
            )

    # ==============================================================
    # INDICADORES
    # ==============================================================

    total_ativas = len(
        oportunidades_ativas
    )

    total_historico = len(
        oportunidades_historico
    )

    total_propostas_aceitas = sum(
        1
        for proposta
        in propostas
        if (
            proposta.status
            or ""
        ).strip().lower()
        == "aceita"
    )

    total_pedidos_originados = len(
        pedidos
    )

    # ==============================================================
    # TOTAIS FINANCEIROS DAS PROPOSTAS
    # ==============================================================

    totais_propostas = {}

    for proposta in propostas:

        try:

            total = (
                proposta.quantidade_kg
                * proposta.preco_por_kg
            )

        except Exception:

            total = None

        totais_propostas[
            proposta.id
        ] = total

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(

        "minhas_oportunidades.html",

        empresa=empresa,

        oportunidades_ativas=
            oportunidades_ativas,

        oportunidades_historico=
            oportunidades_historico,

        propostas_por_oportunidade=
            propostas_por_oportunidade,

        pedidos_por_proposta=
            pedidos_por_proposta,

        totais_propostas=
            totais_propostas,

        total_ativas=
            total_ativas,

        total_historico=
            total_historico,

        total_propostas_aceitas=
            total_propostas_aceitas,

        total_pedidos_originados=
            total_pedidos_originados,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Analisar Oportunidade
# --------------------------------------------------------------------

@app.get(
    "/malharia/oportunidades/<int:oportunidade_id>",
    endpoint="analisar_oportunidade"
)
def analisar_oportunidade(oportunidade_id):

    # ==============================================================
    # AUTENTICAÇÃO DA MALHARIA
    # ==============================================================

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

    # ==============================================================
    # OPORTUNIDADE
    #
    # A oportunidade precisa pertencer obrigatoriamente
    # à empresa autenticada.
    # ==============================================================

    oportunidade = (
        Opportunity.query
        .filter_by(
            id=oportunidade_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not oportunidade:

        flash(
            "Oportunidade não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = oportunidade.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # STATUS ATUAIS
    # ==============================================================

    oportunidade_status = (
        oportunidade.status
        or "nova"
    ).strip().lower()

    demanda_status = (
        demanda.status
        or ""
    ).strip().lower()

    # ==============================================================
    # VISUALIZAÇÃO DA OPORTUNIDADE — SOMENTE LEITURA
    #
    # Esta rota utiliza GET.
    #
    # Abrir uma oportunidade não deve modificar o banco de dados.
    # Portanto, o status armazenado é apenas consultado.
    #
    # Alterações comerciais de status serão realizadas somente
    # por ações explícitas da malharia em rotas POST.
    # ==============================================================

    # ==============================================================
    # MATCHES DA PRÓPRIA MALHARIA
    #
    # Preservamos exatamente a lógica que você já possuía:
    #
    # - mesma demanda
    # - mesma empresa
    # - status ativo
    # - maior score primeiro
    # ==============================================================

    matches = (
        DemandMatch.query
        .filter_by(
            demand_id=demanda.id,
            empresa_id=empresa.id,
            status="ativo"
        )
        .order_by(
            DemandMatch.score.desc(),
            DemandMatch.id.asc()
        )
        .all()
    )

    # ==============================================================
    # PROPOSTA COMERCIAL
    #
    # Além da opportunity_id, validamos empresa_id.
    #
    # É uma proteção adicional para garantir que a malharia
    # somente receba sua própria proposta.
    # ==============================================================

    proposta = (
        Proposal.query
        .filter(
            Proposal.opportunity_id
            == oportunidade.id,

            Proposal.empresa_id
            == empresa.id
        )
        .order_by(
            Proposal.id.desc()
        )
        .first()
    )

    # ==============================================================
    # PEDIDO ORIGINADO PELA PROPOSTA
    # ==============================================================

    pedido = None

    if proposta:

        pedido = (
            Order.query
            .filter(
                Order.proposal_id
                == proposta.id,

                Order.empresa_id
                == empresa.id
            )
            .order_by(
                Order.id.desc()
            )
            .first()
        )

    # ==============================================================
    # VALOR TOTAL DA PROPOSTA
    # ==============================================================

    total_proposta = None

    if proposta:

        try:

            total_proposta = (
                proposta.quantidade_kg
                * proposta.preco_por_kg
            )

        except Exception:

            total_proposta = None

    # ==============================================================
    # OPORTUNIDADE OPERACIONAL OU HISTÓRICA?
    #
    # Uma oportunidade deixa de permitir novas ações quando:
    #
    # 1. A própria oportunidade foi recusada/inativada;
    #
    # OU
    #
    # 2. A demanda já avançou para contratação,
    #    encerramento ou cancelamento.
    #
    # Assim também nos protegemos contra registros históricos
    # eventualmente inconsistentes.
    # ==============================================================

    STATUS_OPORTUNIDADE_ENCERRADA = {
        "inativa",
        "recusada"
    }

    STATUS_DEMANDA_ENCERRADA = {
        "contratada",
        "encerrada",
        "cancelada"
    }

    oportunidade_encerrada = (
        oportunidade_status
        in STATUS_OPORTUNIDADE_ENCERRADA
        or
        demanda_status
        in STATUS_DEMANDA_ENCERRADA
    )

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "analisar_oportunidade.html",

        empresa=empresa,

        oportunidade=oportunidade,

        demanda=demanda,

        matches=matches,

        proposta=proposta,

        pedido=pedido,

        total_proposta=
            total_proposta,

        oportunidade_status=
            oportunidade_status,

        demanda_status=
            demanda_status,

        oportunidade_encerrada=
            oportunidade_encerrada,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Malharia demonstra interesse
# --------------------------------------------------------------------

@app.post(
    "/malharia/oportunidades/<int:oportunidade_id>/interesse",
    endpoint="oportunidade_interesse"
)
def oportunidade_interesse(oportunidade_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

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

    # ==============================================================
    # OPORTUNIDADE DA PRÓPRIA EMPRESA
    # ==============================================================

    oportunidade = (
        Opportunity.query
        .filter_by(
            id=oportunidade_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not oportunidade:

        flash(
            "Oportunidade não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = oportunidade.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # STATUS
    # ==============================================================

    status_oportunidade = (
        oportunidade.status
        or ""
    ).strip().lower()

    status_demanda = (
        demanda.status
        or ""
    ).strip().lower()

    # ==============================================================
    # BLINDAGEM DA DEMANDA
    #
    # Somente demanda PUBLICADA aceita novas manifestações.
    # ==============================================================

    if status_demanda != "publicada":

        flash(
            "Esta demanda não está mais aberta para novas manifestações de interesse.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # BLINDAGEM DA OPORTUNIDADE
    #
    # Interesse somente pode ser registrado partindo de:
    #
    # - nova
    # - visualizada
    # ==============================================================

    if status_oportunidade == "interessada":

        flash(
            "Sua malharia já registrou interesse nesta oportunidade.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    if status_oportunidade not in {
        "nova",
        "visualizada"
    }:

        flash(
            "Esta oportunidade não permite mais registrar interesse.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # REGISTRA INTERESSE
    # ==============================================================

    try:

        oportunidade.status = (
            "interessada"
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[OPORTUNIDADE] Falha ao registrar interesse."
        )

        flash(
            "Não foi possível registrar seu interesse agora.",
            "danger"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    flash(
        "Interesse registrado com sucesso.",
        "success"
    )

    return redirect(
        url_for(
            "analisar_oportunidade",
            oportunidade_id=oportunidade.id
        )
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Malharia recusa oportunidade
# --------------------------------------------------------------------

@app.post(
    "/malharia/oportunidades/<int:oportunidade_id>/recusar",
    endpoint="oportunidade_recusar"
)
def oportunidade_recusar(oportunidade_id):

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

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

    # ==============================================================
    # OPORTUNIDADE DA PRÓPRIA EMPRESA
    # ==============================================================

    oportunidade = (
        Opportunity.query
        .filter_by(
            id=oportunidade_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not oportunidade:

        flash(
            "Oportunidade não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = oportunidade.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # STATUS
    # ==============================================================

    status_oportunidade = (
        oportunidade.status
        or ""
    ).strip().lower()

    status_demanda = (
        demanda.status
        or ""
    ).strip().lower()

    # ==============================================================
    # BLINDAGEM DA DEMANDA
    # ==============================================================

    if status_demanda != "publicada":

        flash(
            "Esta demanda não está mais aberta para novas decisões comerciais.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # BLINDAGEM DA OPORTUNIDADE
    #
    # A decisão "Não tenho interesse" só pode ocorrer antes
    # da manifestação de interesse.
    # ==============================================================

    if status_oportunidade == "recusada":

        flash(
            "Sua malharia já informou que não possui interesse nesta oportunidade.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    if status_oportunidade not in {
        "nova",
        "visualizada"
    }:

        flash(
            "Esta oportunidade não permite mais registrar a opção sem interesse.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # PROTEÇÃO ADICIONAL
    #
    # Se existir proposta, não permitimos transformar a
    # oportunidade em recusada.
    # ==============================================================

    proposta_existente = (
        Proposal.query
        .filter(
            Proposal.opportunity_id
            == oportunidade.id,

            Proposal.empresa_id
            == empresa.id
        )
        .first()
    )

    if proposta_existente:

        flash(
            "Esta oportunidade já possui uma proposta comercial e não pode ser marcada como sem interesse.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # REGISTRA RECUSA
    # ==============================================================

    try:

        oportunidade.status = (
            "recusada"
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[OPORTUNIDADE] Falha ao recusar oportunidade."
        )

        flash(
            "Não foi possível registrar sua decisão agora.",
            "danger"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    flash(
        "Oportunidade marcada como sem interesse.",
        "success"
    )

    return redirect(
        url_for("minhas_oportunidades")
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Enviar / Ajustar Proposta Comercial
# --------------------------------------------------------------------

@app.route(
    "/malharia/oportunidades/<int:oportunidade_id>/proposta",
    methods=["GET", "POST"],
    endpoint="enviar_proposta"
)
def enviar_proposta(oportunidade_id):

    # ==============================================================
    # AUTENTICAÇÃO DA MALHARIA
    # ==============================================================

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

    # ==============================================================
    # OPORTUNIDADE - SOMENTE DA MALHARIA LOGADA
    # ==============================================================

    oportunidade = (
        Opportunity.query
        .filter_by(
            id=oportunidade_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not oportunidade:

        flash(
            "Oportunidade não encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # DEMANDA
    # ==============================================================

    demanda = oportunidade.demanda

    if not demanda:

        flash(
            "A demanda vinculada não foi encontrada.",
            "warning"
        )

        return redirect(
            url_for("minhas_oportunidades")
        )

    # ==============================================================
    # STATUS
    # ==============================================================

    status_oportunidade = (
        oportunidade.status
        or ""
    ).strip().lower()

    status_demanda = (
        demanda.status
        or ""
    ).strip().lower()

    # ==============================================================
    # BLINDAGEM DA DEMANDA
    #
    # Uma proposta comercial só pode ser criada, visualizada
    # para negociação ou alterada enquanto a demanda está publicada.
    #
    # Para demandas contratadas/encerradas, o histórico deve
    # ser consultado pelas telas de Oportunidades / Propostas.
    # ==============================================================

    if status_demanda != "publicada":

        flash(
            "Esta demanda não está mais aberta para envio ou alteração de propostas.",
            "warning"
        )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # BLINDAGEM DA OPORTUNIDADE
    #
    # Somente oportunidade INTERESSADA pode acessar
    # o fluxo comercial de proposta.
    # ==============================================================

    if status_oportunidade != "interessada":

        if status_oportunidade in {
            "inativa",
            "recusada"
        }:

            flash(
                "Esta oportunidade não está mais disponível para negociação.",
                "warning"
            )

        else:

            flash(
                "Demonstre interesse na oportunidade antes de enviar uma proposta.",
                "warning"
            )

        return redirect(
            url_for(
                "analisar_oportunidade",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # PROPOSTA EXISTENTE
    #
    # Também validamos empresa_id.
    # ==============================================================

    proposta = (
        Proposal.query
        .filter(
            Proposal.opportunity_id
            == oportunidade.id,

            Proposal.empresa_id
            == empresa.id
        )
        .first()
    )

    # ==============================================================
    # ÚLTIMA SOLICITAÇÃO DE AJUSTE
    # ==============================================================

    ajuste_aberto = None

    if (
        proposta
        and (
            proposta.status
            or ""
        ).strip().lower()
        == "ajuste_solicitado"
    ):

        ajuste_aberto = (
            ProposalInteraction.query
            .filter_by(
                proposal_id=proposta.id,
                action="ajuste_solicitado"
            )
            .order_by(
                ProposalInteraction.created_at.desc()
            )
            .first()
        )

    # ==============================================================
    # GET
    #
    # Neste momento a demanda continua publicada e a
    # oportunidade continua interessada.
    # ==============================================================

    if request.method == "GET":

        return render_template(
            "enviar_proposta.html",
            empresa=empresa,
            oportunidade=oportunidade,
            demanda=demanda,
            proposta=proposta,
            ajuste_aberto=ajuste_aberto
        )

    # ==============================================================
    # POST — BLINDAGEM DO STATUS DA PROPOSTA
    #
    # Somente podem ser gravados:
    #
    # - nova proposta
    # - rascunho existente
    # - ajuste solicitado pelo comprador
    #
    # Qualquer outro estado fica bloqueado.
    # ==============================================================

    if proposta:

        status_proposta = (
            proposta.status
            or ""
        ).strip().lower()

        STATUS_EDITAVEIS = {
            "rascunho",
            "ajuste_solicitado"
        }

        if status_proposta not in STATUS_EDITAVEIS:

            flash(
                "Esta proposta não pode ser alterada no status atual.",
                "warning"
            )

            return redirect(
                url_for(
                    "enviar_proposta",
                    oportunidade_id=oportunidade.id
                )
            )

    # ==============================================================
    # HELPER PARA VALORES DECIMAIS
    #
    # Aceita:
    #
    # 7,80
    # 7.80
    # 1.250,50
    # ==============================================================

    def _decimal_form(valor):

        valor = (
            valor
            or ""
        ).strip()

        if not valor:

            return None

        valor = (
            valor
            .replace("R$", "")
            .replace(" ", "")
        )

        # Formato brasileiro:
        # 1.250,50 -> 1250.50
        if "," in valor:

            valor = (
                valor
                .replace(".", "")
                .replace(",", ".")
            )

        try:

            return Decimal(
                valor
            )

        except (
            InvalidOperation,
            ValueError,
            TypeError
        ):

            return None

    # ==============================================================
    # RECEBE FORMULÁRIO
    # ==============================================================

    quantidade_kg = _decimal_form(
        request.form.get(
            "quantidade_kg"
        )
    )

    preco_por_kg = _decimal_form(
        request.form.get(
            "preco_por_kg"
        )
    )

    prazo_raw = (
        request.form.get(
            "prazo_dias"
        )
        or ""
    ).strip()

    validade_raw = (
        request.form.get(
            "validade_dias"
        )
        or ""
    ).strip()

    condicoes_pagamento = (
        request.form.get(
            "condicoes_pagamento"
        )
        or ""
    ).strip()

    observacoes = (
        request.form.get(
            "observacoes"
        )
        or ""
    ).strip()

    # ==============================================================
    # VALIDAÇÃO DA QUANTIDADE
    # ==============================================================

    if (
        quantidade_kg is None
        or quantidade_kg <= 0
    ):

        flash(
            "Informe uma quantidade válida.",
            "warning"
        )

        return redirect(
            url_for(
                "enviar_proposta",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # NÃO PERMITE PROPOR MAIS QUE A DEMANDA
    # ==============================================================

    try:

        quantidade_demanda = Decimal(
            str(
                demanda.quantidade_kg
            )
        )

    except Exception:

        quantidade_demanda = None

    if (
        quantidade_demanda is not None
        and quantidade_kg
        > quantidade_demanda
    ):

        flash(
            "A quantidade proposta não pode ser maior que a quantidade solicitada na demanda.",
            "warning"
        )

        return redirect(
            url_for(
                "enviar_proposta",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # VALIDAÇÃO DO PREÇO
    # ==============================================================

    if (
        preco_por_kg is None
        or preco_por_kg <= 0
    ):

        flash(
            "Informe um preço por kg válido.",
            "warning"
        )

        return redirect(
            url_for(
                "enviar_proposta",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # PRAZO
    # ==============================================================

    try:

        prazo_dias = int(
            prazo_raw
        )

    except Exception:

        prazo_dias = 0

    if prazo_dias <= 0:

        flash(
            "Informe um prazo de produção válido.",
            "warning"
        )

        return redirect(
            url_for(
                "enviar_proposta",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # VALIDADE
    # ==============================================================

    try:

        validade_dias = int(
            validade_raw
        )

    except Exception:

        validade_dias = 0

    if validade_dias <= 0:

        flash(
            "Informe a validade da proposta.",
            "warning"
        )

        return redirect(
            url_for(
                "enviar_proposta",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # STATUS ANTERIOR
    # ==============================================================

    status_anterior = (
        (
            proposta.status
            or ""
        ).strip().lower()

        if proposta

        else None
    )

    # ==============================================================
    # SALVA PROPOSTA
    # ==============================================================

    try:

        # ----------------------------------------------------------
        # PRIMEIRA PROPOSTA
        # ----------------------------------------------------------

        if not proposta:

            proposta = Proposal(
                opportunity_id=oportunidade.id,
                demand_id=demanda.id,
                empresa_id=empresa.id
            )

            db.session.add(
                proposta
            )

        # ----------------------------------------------------------
        # CONDIÇÕES COMERCIAIS
        # ----------------------------------------------------------

        proposta.quantidade_kg = (
            quantidade_kg
        )

        proposta.preco_por_kg = (
            preco_por_kg
        )

        proposta.prazo_dias = (
            prazo_dias
        )

        proposta.validade_dias = (
            validade_dias
        )

        proposta.condicoes_pagamento = (
            condicoes_pagamento
            or None
        )

        proposta.observacoes = (
            observacoes
            or None
        )

        # ----------------------------------------------------------
        # ENVIO / REENVIO
        # ----------------------------------------------------------

        proposta.status = (
            "enviada"
        )

        proposta.sent_at = (
            datetime.utcnow()
        )

        # ----------------------------------------------------------
        # GARANTE ID
        # ----------------------------------------------------------

        db.session.flush()

        # ----------------------------------------------------------
        # HISTÓRICO
        # ----------------------------------------------------------

        if (
            status_anterior
            == "ajuste_solicitado"
        ):

            acao_interacao = (
                "proposta_reenviada"
            )

            mensagem_interacao = (
                "Proposta ajustada e reenviada pela malharia."
            )

        else:

            acao_interacao = (
                "proposta_enviada"
            )

            mensagem_interacao = (
                "Proposta enviada pela malharia."
            )

        interacao = ProposalInteraction(
            proposal_id=proposta.id,
            actor_role="malharia",
            action=acao_interacao,
            message=mensagem_interacao
        )

        db.session.add(
            interacao
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PROPOSTA] Falha ao enviar/reEnviar proposta."
        )

        flash(
            "Não foi possível salvar a proposta agora.",
            "danger"
        )

        return redirect(
            url_for(
                "enviar_proposta",
                oportunidade_id=oportunidade.id
            )
        )

    # ==============================================================
    # MENSAGEM FINAL
    # ==============================================================

    if (
        status_anterior
        == "ajuste_solicitado"
    ):

        flash(
            (
                f"Proposta ajustada para "
                f"{demanda.codigo} "
                f"reenviada com sucesso."
            ),
            "success"
        )

    else:

        flash(
            (
                f"Proposta para "
                f"{demanda.codigo} "
                f"enviada com sucesso."
            ),
            "success"
        )

    # ==============================================================
    # RETORNO
    # ==============================================================

    return redirect(
        url_for(
            "enviar_proposta",
            oportunidade_id=oportunidade.id
        )
    )

# --- Rota do Painel (vencimento por plano + ajuste p/ próximo dia útil BR) ---

@app.route(
    '/painel_malharia',
    endpoint="painel_malharia"
)
def painel_malharia():

    # ==============================================================
    # AUTENTICAÇÃO
    # ==============================================================

    emp, u = _get_empresa_usuario_da_sessao()

    if not emp or not u:

        return redirect(
            url_for('login')
        )

    # --------------------------------------------------------------
    # Evita objetos antigos na identity map do SQLAlchemy
    # --------------------------------------------------------------

    try:

        db.session.expire_all()

    except Exception:

        pass

    # --------------------------------------------------------------
    # Recarrega empresa com dados atuais
    # --------------------------------------------------------------

    try:

        emp_id = emp.id

        emp = Empresa.query.get(
            emp_id
        )

        if not emp:

            return redirect(
                url_for("login")
            )

    except Exception:

        pass

    # ==============================================================
    # STEP / ONBOARDING
    # ==============================================================

    step = (
        request.args.get("step")
        or _proximo_step(emp)
    )

    # ==============================================================
    # TEARES
    # ==============================================================

    teares = (
        Tear.query
        .filter_by(
            empresa_id=emp.id
        )
        .order_by(
            Tear.id.desc()
        )
        .all()
    )

    teares_total = len(
        teares
    )

    # ==============================================================
    # MARKETPLACE 2.0
    #
    # Indicadores reais da malharia
    # ==============================================================

    # --------------------------------------------------------------
    # OPORTUNIDADES
    #
    # Ativas:
    # - nova
    # - visualizada
    # - interessada
    #
    # Encerradas / histórico:
    # - recusada
    # - inativa
    # --------------------------------------------------------------

    oportunidades_ativas = (
        Opportunity.query
        .filter(
            Opportunity.empresa_id
            == emp.id,

            Opportunity.status.in_(
                [
                    "nova",
                    "visualizada",
                    "interessada"
                ]
            )
        )
        .count()
    )

    oportunidades_total = (
        Opportunity.query
        .filter(
            Opportunity.empresa_id
            == emp.id
        )
        .count()
    )

    # --------------------------------------------------------------
    # PROPOSTAS
    # --------------------------------------------------------------

    propostas_total = (
        Proposal.query
        .filter(
            Proposal.empresa_id
            == emp.id
        )
        .count()
    )

    # Propostas que ainda possuem alguma ação comercial pendente.
    propostas_em_negociacao = (
        Proposal.query
        .filter(
            Proposal.empresa_id
            == emp.id,

            Proposal.status.in_(
                [
                    "rascunho",
                    "enviada",
                    "ajuste_solicitado"
                ]
            )
        )
        .count()
    )

    propostas_aceitas = (
        Proposal.query
        .filter(
            Proposal.empresa_id
            == emp.id,

            Proposal.status
            == "aceita"
        )
        .count()
    )

    # --------------------------------------------------------------
    # PEDIDOS
    #
    # Entregue não é mais pedido em andamento.
    # --------------------------------------------------------------

    pedidos_ativos = (
        Order.query
        .filter(
            Order.empresa_id
            == emp.id,

            Order.status.in_(
                [
                    "aguardando_confirmacao",
                    "confirmado",
                    "em_producao",
                    "concluido"
                ]
            )
        )
        .count()
    )

    pedidos_total = (
        Order.query
        .filter(
            Order.empresa_id
            == emp.id,

            Order.status
            != "cancelado"
        )
        .count()
    )

    pedidos_entregues = (
        Order.query
        .filter(
            Order.empresa_id
            == emp.id,

            Order.status
            == "entregue"
        )
        .count()
    )

    # ==============================================================
    # ASSINATURA
    # ==============================================================

    status_raw = (
        getattr(
            emp,
            "status_pagamento",
            None
        )
        or "pendente"
    ).strip().lower()

    STATUS_ATIVO = {
        "ativo",
        "aprovado",
        "approved",
        "paid",
        "active",
        "trial"
    }

    status_ok = (
        status_raw
        in STATUS_ATIVO
    )

    # --------------------------------------------------------------
    # Vencimento
    # --------------------------------------------------------------

    vencimento_proximo = None
    dias_restantes = None
    ativa_pelo_tempo = False

    try:

        # ----------------------------------------------------------
        # Hoje no Brasil
        # ----------------------------------------------------------

        try:

            from zoneinfo import ZoneInfo

            hoje = datetime.now(
                ZoneInfo(
                    "America/Sao_Paulo"
                )
            ).date()

        except Exception:

            from datetime import date as _date

            hoje = _date.today()

        # ----------------------------------------------------------
        # Normalização das datas
        # ----------------------------------------------------------

        def _to_date(v):

            if not v:

                return None

            if isinstance(
                v,
                datetime
            ):

                return v.date()

            try:

                return v

            except Exception:

                return None

        ult_pgto = _to_date(
            getattr(
                emp,
                "assin_ultimo_pagamento",
                None
            )
        )

        data_pag = _to_date(
            getattr(
                emp,
                "data_pagamento",
                None
            )
        )

        inicio = _to_date(
            getattr(
                emp,
                "assin_data_inicio",
                None
            )
        )

        created = _to_date(
            getattr(
                emp,
                "created_at",
                None
            )
        )

        # ----------------------------------------------------------
        # Data base do ciclo
        # ----------------------------------------------------------

        base_dt = (
            ult_pgto
            or data_pag
            or inicio
            or created
            or hoje
        )

        # ----------------------------------------------------------
        # Plano
        # ----------------------------------------------------------

        plano = (
            getattr(
                emp,
                "plano",
                None
            )
            or "mensal"
        ).strip().lower()

        if "anual" in plano:

            dias_plano = 365

        else:

            dias_plano = 35

        # ----------------------------------------------------------
        # Vencimento
        # ----------------------------------------------------------

        nominal = (
            base_dt
            + timedelta(
                days=dias_plano
            )
        )

        venc = _proximo_dia_util_br(
            nominal
        )

        vencimento_proximo = venc

        dias_restantes = (
            venc
            - hoje
        ).days

        # ----------------------------------------------------------
        # Tolerância
        # ----------------------------------------------------------

        tol = int(
            globals().get(
                "TOLERANCIA_DIAS",
                0
            )
            or 0
        )

        ativa_pelo_tempo = (
            hoje
            <= (
                venc
                + timedelta(
                    days=tol
                )
            )
        )

        # ----------------------------------------------------------
        # ASSINATURA VENCIDA — SOMENTE ESTADO DE APRESENTAÇÃO
        #
        # IMPORTANTE:
        # esta é uma rota GET. Consultar o painel não deve
        # alterar o banco de dados.
        #
        # A validade real da assinatura já é verificada pela
        # regra central de acesso e por Empresa.assinatura_ativa.
        #
        # Aqui apenas ajustamos a variável local utilizada para
        # montar o painel.
        # ----------------------------------------------------------

        if (
            status_ok
            and not ativa_pelo_tempo
            and getattr(
                emp,
                "data_pagamento",
                None
            )
        ):

            status_ok = False

    except Exception as e:

        app.logger.warning(
            (
                "[painel] cálculo de "
                f"vencimento falhou: {e}"
            )
        )

    # ==============================================================
    # ASSINATURA ATIVA
    # ==============================================================

    is_ativa = bool(
        status_ok
        and ativa_pelo_tempo
    )

    # ==============================================================
    # CTA PAGAMENTO
    # ==============================================================

    mostrar_pagamento = (
        not is_ativa
    )

    if (
        is_ativa is True
        and dias_restantes is not None
        and dias_restantes <= 7
    ):

        mostrar_pagamento = True

    # ==============================================================
    # CHECKLIST
    # ==============================================================

    checklist = {

        "perfil_ok":
            all(
                _empresa_basica_completa(
                    emp
                )
            ),

        "teares_ok":
            _conta_teares(
                emp.id
            ) > 0,

        "plano_ok":
            is_ativa
            or DEMO_MODE,

        "step":
            step,
    }

    # ==============================================================
    # NOTIFICAÇÕES / CHAT
    # ==============================================================

    notif_count, notif_lista = (
        _get_notificacoes(
            emp.id
        )
    )

    chat_nao_lidos = 0

    # ==============================================================
    # FOTO
    # ==============================================================

    foto_url = (
        _empresa_avatar_url(
            emp
        )
    )

    # ==============================================================
    # LOG
    # ==============================================================

    app.logger.info({

        "rota":
            "painel_malharia",

        "empresa_id":
            emp.id,

        "status_pagamento":
            getattr(
                emp,
                "status_pagamento",
                None
            ),

        "plano":
            getattr(
                emp,
                "plano",
                None
            ),

        "vencimento_proximo":
            (
                str(
                    vencimento_proximo
                )
                if vencimento_proximo
                else None
            ),

        "dias_restantes":
            dias_restantes,

        "assinatura_ativa":
            is_ativa,

        "foto_url_resolvida":
            foto_url,

        # Marketplace
        "oportunidades_ativas":
            oportunidades_ativas,

        "propostas_total":
            propostas_total,

        "pedidos_ativos":
            pedidos_ativos,

        "teares_total":
            teares_total,
    })

    # ==============================================================
    # RENDER
    # ==============================================================

    resp = make_response(
        render_template(

            "painel_malharia.html",

            # ------------------------------------------------------
            # Dados já existentes
            # ------------------------------------------------------

            empresa=emp,

            teares=teares,

            assinatura_ativa=is_ativa,

            checklist=checklist,

            step=step,

            notificacoes=notif_count,

            notificacoes_lista=notif_lista,

            chat_nao_lidos=chat_nao_lidos,

            foto_url=foto_url,

            vencimento_proximo=
                vencimento_proximo,

            dias_restantes=
                dias_restantes,

            mostrar_pagamento=
                mostrar_pagamento,

            # ------------------------------------------------------
            # Marketplace 2.0
            # ------------------------------------------------------

            oportunidades_ativas=
                oportunidades_ativas,

            oportunidades_total=
                oportunidades_total,

            propostas_total=
                propostas_total,

            propostas_em_negociacao=
                propostas_em_negociacao,

            propostas_aceitas=
                propostas_aceitas,

            pedidos_ativos=
                pedidos_ativos,

            pedidos_total=
                pedidos_total,

            pedidos_entregues=
                pedidos_entregues,

            teares_total=
                teares_total,
        )
    )

    # ==============================================================
    # SEM CACHE
    # ==============================================================

    resp.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0"
    )

    resp.headers[
        "Pragma"
    ] = "no-cache"

    resp.headers[
        "Expires"
    ] = "0"

    return resp

# --------------------------------------------------------------------
# AcheTece 2.0 - Visão Geral das Propostas da Malharia
# --------------------------------------------------------------------

@app.get(
    "/malharia/propostas",
    endpoint="propostas_malharia"
)
def propostas_malharia():

    # --------------------------------------------------------------
    # Autenticação
    # --------------------------------------------------------------

    emp, u = (
        _get_empresa_usuario_da_sessao()
    )

    if not emp or not u:

        return redirect(
            url_for("login")
        )

    # --------------------------------------------------------------
    # Propostas desta malharia
    # --------------------------------------------------------------

    propostas = (
        Proposal.query
        .filter(
            Proposal.empresa_id
            == emp.id
        )
        .order_by(
            Proposal.id.desc()
        )
        .all()
    )

    # --------------------------------------------------------------
    # Indicadores
    # --------------------------------------------------------------

    total_propostas = len(
        propostas
    )

    total_enviadas = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        == "enviada"
    )

    total_ajustes = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        == "ajuste_solicitado"
    )

    total_aceitas = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        == "aceita"
    )

    total_nao_selecionadas = sum(
        1
        for proposta in propostas
        if (
            proposta.status or ""
        ).strip().lower()
        in {
            "recusada",
            "nao_selecionada",
            "cancelada"
        }
    )

    # --------------------------------------------------------------
    # Valor de cada proposta
    # --------------------------------------------------------------

    totais_propostas = {}

    for proposta in propostas:

        try:

            total = (
                proposta.quantidade_kg
                * proposta.preco_por_kg
            )

        except Exception:

            total = None

        totais_propostas[
            proposta.id
        ] = total

    # --------------------------------------------------------------
    # Render
    # --------------------------------------------------------------

    return render_template(
        "propostas_malharia.html",

        empresa=emp,

        propostas=propostas,

        totais_propostas=
            totais_propostas,

        total_propostas=
            total_propostas,

        total_enviadas=
            total_enviadas,

        total_ajustes=
            total_ajustes,

        total_aceitas=
            total_aceitas,

        total_nao_selecionadas=
            total_nao_selecionadas,
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
    """
    Escolhe uma URL segura de retorno após atualização
    da foto da malharia.

    Permite retorno somente para páginas internas conhecidas
    do próprio AcheTece.
    """

    ref = (
        request.referrer
        or ""
    )

    if ref:

        try:

            rp = urlparse(
                ref
            )

            # Somente o próprio domínio.
            if rp.netloc == request.host:

                path = (
                    rp.path
                    or ""
                )

                retorno_permitido = (
                    "painel" in path
                    or "malharia" in path
                    or path.rstrip("/")
                    == "/editar_empresa"
                )

                if retorno_permitido:

                    return redirect(
                        _with_cb(
                            ref,
                            ts
                        )
                    )

        except Exception:

            pass

    return redirect(
        url_for(
            "painel_malharia",
            _cb=ts
        )
    )

def _empresa_avatar_url(emp) -> str | None:
    """
    Resolve a URL da foto da empresa sem alterar o banco.

    Ordem de busca:
    1) usa emp.foto_url quando já estiver preenchido;
    2) procura o arquivo atual em static/avatars;
    3) procura formatos legados em static/uploads/avatars;
    4) procura formatos legados em static/uploads/perfil;
    5) se nada existir, retorna None.

    IMPORTANTE:
    Esta função é somente leitura.

    Ela pode ser chamada durante requisições GET e, por isso,
    não executa UPDATE, INSERT, DELETE, commit ou rollback.

    A persistência de foto_url acontece exclusivamente no fluxo
    explícito de upload da foto.
    """

    if not emp:
        return None

    # ==========================================================
    # 1) URL JÁ PERSISTIDA NO CADASTRO
    # ==========================================================

    url = (
        getattr(
            emp,
            "foto_url",
            None
        )
        or ""
    ).strip()

    if url:
        return url

    # ==========================================================
    # 2) FALLBACK SOMENTE LEITURA PARA ARQUIVOS FÍSICOS
    #
    # Incluímos:
    # - formato atual: static/avatars/empresa_<id>.webp
    # - formatos legados já utilizados pelo AcheTece
    # ==========================================================

    try:

        empresa_id = int(
            emp.id
        )

        candidates = [

            # Fluxo atual de upload
            (
                f"avatars/empresa_{empresa_id}",
                (
                    ".webp",
                    ".jpg",
                    ".jpeg",
                    ".png",
                ),
            ),

            # Fluxo legado
            (
                f"uploads/avatars/empresa_{empresa_id}",
                (
                    ".webp",
                    ".jpg",
                    ".jpeg",
                    ".png",
                ),
            ),

            # Fluxo legado mais antigo
            (
                f"uploads/perfil/emp_{empresa_id}",
                (
                    ".webp",
                    ".jpg",
                    ".jpeg",
                    ".png",
                ),
            ),
        ]

        for base_rel, extensions in candidates:

            for extension in extensions:

                rel_path = (
                    f"{base_rel}{extension}"
                )

                abs_path = os.path.join(
                    app.static_folder,
                    rel_path
                )

                if os.path.isfile(
                    abs_path
                ):

                    return url_for(
                        "static",
                        filename=rel_path
                    )

    except Exception:

        current_app.logger.exception(
            (
                "[AVATAR] Falha ao resolver foto "
                "em modo somente leitura. "
                f"empresa_id="
                f"{getattr(emp, 'id', None)}"
            )
        )

    return None


# --------------------------------------------------------------------
# AcheTece 2.0 - Meus Pedidos da Malharia
# --------------------------------------------------------------------

@app.get(
    "/malharia/pedidos",
    endpoint="meus_pedidos_malharia"
)
def meus_pedidos_malharia():

    # ==============================================================
    # AUTENTICAÇÃO DA MALHARIA
    # ==============================================================

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

    # ==============================================================
    # PEDIDOS DA MALHARIA
    #
    # Mantemos aqui o comportamento atual:
    # pedidos cancelados não entram na listagem operacional.
    # ==============================================================

    pedidos = (
        Order.query
        .filter(
            Order.empresa_id
            == empresa.id,

            Order.status
            != "cancelado"
        )
        .order_by(
            Order.created_at.desc(),
            Order.id.desc()
        )
        .all()
    )

    # ==============================================================
    # INDICADORES
    #
    # Cada indicador representa o STATUS ATUAL do pedido.
    # ==============================================================

    total_pedidos = len(
        pedidos
    )

    total_aguardando = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "aguardando_confirmacao"
    )

    total_confirmados = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "confirmado"
    )

    total_em_producao = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "em_producao"
    )

    total_concluidos = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "concluido"
    )

    # --------------------------------------------------------------
    # Entregues
    # --------------------------------------------------------------

    total_entregues = sum(
        1
        for pedido in pedidos
        if (
            pedido.status
            or ""
        ).strip().lower()
        == "entregue"
    )

    # ==============================================================
    # RENDER
    # ==============================================================

    return render_template(
        "meus_pedidos_malharia.html",

        empresa=empresa,

        pedidos=pedidos,

        total_pedidos=
            total_pedidos,

        total_aguardando=
            total_aguardando,

        total_confirmados=
            total_confirmados,

        total_em_producao=
            total_em_producao,

        total_concluidos=
            total_concluidos,

        total_entregues=
            total_entregues,
    )
    
# --------------------------------------------------------------------
# AcheTece 2.0 - Detalhes do Pedido para Malharia
# --------------------------------------------------------------------

@app.get(
    "/malharia/pedidos/<int:pedido_id>",
    endpoint="detalhe_pedido_malharia"
)
def detalhe_pedido_malharia(pedido_id):

    # --------------------------------------------------------------
    # Autenticação
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
    # Pedido SOMENTE da própria malharia
    # --------------------------------------------------------------

    pedido = (
        Order.query
        .filter_by(
            id=pedido_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not pedido:

        flash(
            "Pedido não encontrado.",
            "warning"
        )

        return redirect(
            url_for(
                "meus_pedidos_malharia"
            )
        )

    proposta = pedido.proposta
    demanda = pedido.demanda
    
    historico_operacional = (
        _montar_historico_operacional_pedido(
            pedido
        )
    )
    
    return render_template(
        "detalhe_pedido_malharia.html",
    
        empresa=empresa,
    
        pedido=pedido,
    
        proposta=proposta,
    
        demanda=demanda,
    
        historico_operacional=
            historico_operacional,
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Malharia confirma Pedido
# --------------------------------------------------------------------

@app.post(
    "/malharia/pedidos/<int:pedido_id>/confirmar",
    endpoint="confirmar_pedido_malharia"
)
def confirmar_pedido_malharia(pedido_id):

    # --------------------------------------------------------------
    # Autenticação
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
    # Pedido da própria malharia
    # --------------------------------------------------------------

    pedido = (
        Order.query
        .filter_by(
            id=pedido_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not pedido:

        flash(
            "Pedido não encontrado.",
            "warning"
        )

        return redirect(
            url_for(
                "meus_pedidos_malharia"
            )
        )

    status_atual = (
        pedido.status or ""
    ).strip().lower()

    # --------------------------------------------------------------
    # Somente aguardando_confirmacao pode ser confirmado
    # --------------------------------------------------------------

    if status_atual == "confirmado":

        flash(
            f"O pedido {pedido.codigo} já está confirmado.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_pedido_malharia",
                pedido_id=pedido.id
            )
        )

    if status_atual != "aguardando_confirmacao":

        flash(
            "Este pedido não pode ser confirmado no status atual.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_pedido_malharia",
                pedido_id=pedido.id
            )
        )

    # --------------------------------------------------------------
    # Confirmação
    # --------------------------------------------------------------

    try:

        pedido.status = "confirmado"
    
        pedido.confirmed_at = (
            datetime.utcnow()
        )
    
        # ==========================================================
        # HISTÓRICO OPERACIONAL
        # ==========================================================
    
        evento = OrderEvent(
            order_id=pedido.id,
            actor_role="malharia",
            action="pedido_confirmado",
            status_anterior="aguardando_confirmacao",
            status_novo="confirmado",
            message=(
                f"A malharia confirmou o recebimento "
                f"do pedido {pedido.codigo}."
            )
        )
    
        db.session.add(
            evento
        )
    
        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PEDIDO] Falha ao confirmar pedido."
        )

        flash(
            "Não foi possível confirmar o pedido agora.",
            "danger"
        )

        return redirect(
            url_for(
                "detalhe_pedido_malharia",
                pedido_id=pedido.id
            )
        )

    flash(
        (
            f"Pedido {pedido.codigo} "
            f"confirmado com sucesso."
        ),
        "success"
    )

    return redirect(
        url_for(
            "detalhe_pedido_malharia",
            pedido_id=pedido.id
        )
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Malharia inicia Produção
# --------------------------------------------------------------------

@app.post(
    "/malharia/pedidos/<int:pedido_id>/iniciar-producao",
    endpoint="iniciar_producao_malharia"
)
def iniciar_producao_malharia(pedido_id):

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
    # Pedido somente da própria malharia
    # --------------------------------------------------------------

    pedido = (
        Order.query
        .filter_by(
            id=pedido_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not pedido:

        flash(
            "Pedido não encontrado.",
            "warning"
        )

        return redirect(
            url_for("meus_pedidos_malharia")
        )

    status_atual = (
        pedido.status or ""
    ).strip().lower()

    # --------------------------------------------------------------
    # Só pode iniciar a partir de CONFIRMADO
    # --------------------------------------------------------------

    if status_atual != "confirmado":

        flash(
            "Somente um pedido confirmado pode entrar em produção.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_pedido_malharia",
                pedido_id=pedido.id
            )
        )

    # --------------------------------------------------------------
    # Atualização
    # --------------------------------------------------------------

    try:

        pedido.status = "em_producao"

        evento = OrderEvent(
            order_id=pedido.id,
            actor_role="malharia",
            action="producao_iniciada",
            status_anterior="confirmado",
            status_novo="em_producao",
            message=(
                f"A produção do pedido "
                f"{pedido.codigo} foi iniciada."
            )
        )

        db.session.add(
            evento
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PEDIDO] Falha ao iniciar produção."
        )

        flash(
            "Não foi possível iniciar a produção agora.",
            "danger"
        )

        return redirect(
            url_for(
                "detalhe_pedido_malharia",
                pedido_id=pedido.id
            )
        )

    flash(
        (
            f"Produção do pedido "
            f"{pedido.codigo} iniciada."
        ),
        "success"
    )

    return redirect(
        url_for(
            "detalhe_pedido_malharia",
            pedido_id=pedido.id
        )
    )

# --------------------------------------------------------------------
# AcheTece 2.0 - Malharia conclui Produção
# --------------------------------------------------------------------

@app.post(
    "/malharia/pedidos/<int:pedido_id>/concluir-producao",
    endpoint="concluir_producao_malharia"
)
def concluir_producao_malharia(pedido_id):

    # --------------------------------------------------------------
    # Autenticação
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
    # Pedido da própria malharia
    # --------------------------------------------------------------

    pedido = (
        Order.query
        .filter_by(
            id=pedido_id,
            empresa_id=empresa.id
        )
        .first()
    )

    if not pedido:

        flash(
            "Pedido não encontrado.",
            "warning"
        )

        return redirect(
            url_for("meus_pedidos_malharia")
        )

    status_atual = (
        pedido.status or ""
    ).strip().lower()

    # --------------------------------------------------------------
    # Só pode concluir se estiver EM PRODUÇÃO
    # --------------------------------------------------------------

    if status_atual != "em_producao":

        flash(
            "Somente um pedido em produção pode ser concluído.",
            "warning"
        )

        return redirect(
            url_for(
                "detalhe_pedido_malharia",
                pedido_id=pedido.id
            )
        )

    try:

        pedido.status = "concluido"

        pedido.completed_at = (
            datetime.utcnow()
        )

        evento = OrderEvent(
            order_id=pedido.id,
            actor_role="malharia",
            action="producao_concluida",
            status_anterior="em_producao",
            status_novo="concluido",
            message=(
                f"A produção do pedido "
                f"{pedido.codigo} foi concluída."
            )
        )

        db.session.add(
            evento
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[PEDIDO] Falha ao concluir produção."
        )

        flash(
            "Não foi possível concluir a produção agora.",
            "danger"
        )

        return redirect(
            url_for(
                "detalhe_pedido_malharia",
                pedido_id=pedido.id
            )
        )

    flash(
        (
            f"Produção do pedido "
            f"{pedido.codigo} concluída."
        ),
        "success"
    )

    return redirect(
        url_for(
            "detalhe_pedido_malharia",
            pedido_id=pedido.id
        )
    )


 
from datetime import datetime


# ==============================================================
# FOTO DE PERFIL DA MALHARIA — UPLOAD SEGURO
# ==============================================================

@app.post(
    "/perfil/foto_upload",
    endpoint="perfil_foto_upload"
)
@limiter.limit(
    "10 per hour",
    key_func=_malharia_rate_limit_key
)
@limiter.limit(
    "30 per day",
    key_func=_malharia_rate_limit_key
)
def perfil_foto_upload():
    """
    Recebe e processa a foto da malharia autenticada.

    Segurança:
    - autenticação central da malharia;
    - protegido pelo CSRF global;
    - Rate Limit por empresa;
    - limite global de 5 MB;
    - não confia em extensão/MIME;
    - conteúdo validado pelo Pillow;
    - imagem reprocessada completamente;
    - dimensões limitadas;
    - EXIF removido;
    - saída sempre WEBP;
    - nome do arquivo é gerado pelo servidor;
    - arquivo temporário é utilizado antes da substituição.
    """

    # ==========================================================
    # IDENTIDADE
    # ==========================================================

    emp, usuario = (
        _get_empresa_usuario_da_sessao()
    )

    if (
        not emp
        or not usuario
    ):

        session.clear()

        return redirect(
            url_for("login")
        )

    # ==========================================================
    # LOCALIZA O ARQUIVO
    #
    # O painel pode possuir mais de um input name="foto".
    # Pegamos o primeiro que realmente contém um arquivo.
    # ==========================================================

    try:

        candidatos = (
            request.files.getlist(
                "foto"
            )
        )

    except Exception:

        candidatos = [
            request.files.get(
                "foto"
            )
        ]

    arquivo = None

    for candidato in candidatos:

        if (
            candidato
            and (
                getattr(
                    candidato,
                    "filename",
                    ""
                )
                or ""
            ).strip()
        ):

            arquivo = candidato
            break

    # ==========================================================
    # NENHUM ARQUIVO
    # ==========================================================

    if arquivo is None:

        flash(
            "Nenhuma foto selecionada.",
            "warning"
        )

        current_app.logger.info(
            (
                "[AVATAR] Upload sem arquivo. "
                f"empresa_id={emp.id}"
            )
        )

        return _back_to_panel(
            int(
                datetime.utcnow()
                .timestamp()
            )
        )

    # ==========================================================
    # NOME ORIGINAL
    #
    # Não utilizamos este nome para salvar o arquivo.
    # Apenas fazemos normalização defensiva.
    # ==========================================================

    original_filename = (
        secure_filename(
            arquivo.filename
            or ""
        )
    )

    if not original_filename:

        flash(
            "O arquivo selecionado é inválido.",
            "warning"
        )

        return _back_to_panel(
            int(
                datetime.utcnow()
                .timestamp()
            )
        )

    # ==========================================================
    # DIRETÓRIO
    # ==========================================================

    avatars_dir = os.path.join(
        app.static_folder,
        "avatars"
    )

    try:

        os.makedirs(
            avatars_dir,
            exist_ok=True
        )

    except Exception:

        current_app.logger.exception(
            (
                "[AVATAR] Falha ao preparar "
                f"diretório. empresa_id={emp.id}"
            )
        )

        flash(
            "Não foi possível preparar o envio da foto.",
            "danger"
        )

        return _back_to_panel(
            int(
                datetime.utcnow()
                .timestamp()
            )
        )

    # ==========================================================
    # NOMES CONTROLADOS PELO SERVIDOR
    #
    # A saída é SEMPRE .webp.
    # ==========================================================

    base_name = (
        f"empresa_{emp.id}"
    )

    final_filename = (
        f"{base_name}.webp"
    )

    final_path = os.path.join(
        avatars_dir,
        final_filename
    )

    temporary_filename = (
        f".{base_name}."
        f"{uuid.uuid4().hex}."
        "tmp.webp"
    )

    temporary_path = os.path.join(
        avatars_dir,
        temporary_filename
    )

    image_info = None

    # ==========================================================
    # VALIDAÇÃO + REPROCESSAMENTO
    # ==========================================================

    try:

        image_info = (
            _save_square_webp(
                arquivo,
                temporary_path,
                side=400,
                quality=85
            )
        )

        # ------------------------------------------------------
        # SUBSTITUIÇÃO ATÔMICA
        #
        # Somente depois de gerar uma imagem WEBP válida,
        # substituímos a foto oficial.
        # ------------------------------------------------------

        os.replace(
            temporary_path,
            final_path
        )

    except ValueError as exc:

        try:

            if os.path.exists(
                temporary_path
            ):

                os.remove(
                    temporary_path
                )

        except OSError:

            pass

        current_app.logger.warning(
            (
                "[SECURITY][AVATAR_REJECTED] "
                f"empresa_id={emp.id} "
                f"motivo={str(exc)[:120]}"
            )
        )

        flash(
            (
                "O arquivo enviado não é uma imagem "
                "válida ou não possui um formato permitido."
            ),
            "warning"
        )

        return _back_to_panel(
            int(
                datetime.utcnow()
                .timestamp()
            )
        )

    except Exception:

        try:

            if os.path.exists(
                temporary_path
            ):

                os.remove(
                    temporary_path
                )

        except OSError:

            pass

        current_app.logger.exception(
            (
                "[AVATAR] Falha ao processar imagem. "
                f"empresa_id={emp.id}"
            )
        )

        flash(
            "Não foi possível processar a foto enviada.",
            "danger"
        )

        return _back_to_panel(
            int(
                datetime.utcnow()
                .timestamp()
            )
        )

    # ==========================================================
    # REMOVE FORMATOS ANTIGOS
    #
    # O novo arquivo .webp só é criado depois da validação.
    # ==============================================================

    for old_ext in (
        ".jpg",
        ".jpeg",
        ".png",
        ".gif",
    ):

        old_path = os.path.join(
            avatars_dir,
            base_name + old_ext
        )

        if os.path.exists(
            old_path
        ):

            try:

                os.remove(
                    old_path
                )

            except OSError:

                current_app.logger.warning(
                    (
                        "[AVATAR] Não foi possível remover "
                        f"arquivo legado. empresa_id={emp.id}"
                    )
                )

    # ==========================================================
    # URL PÚBLICA
    # ==========================================================

    rel_path = (
        f"avatars/{final_filename}"
    )

    novo_url = url_for(
        "static",
        filename=rel_path
    )

    # ==========================================================
    # BANCO
    # ==========================================================

    try:

        emp.foto_url = (
            novo_url
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            (
                "[AVATAR] Falha ao gravar foto_url. "
                f"empresa_id={emp.id}"
            )
        )

        flash(
            "A imagem foi processada, mas não pôde ser salva no cadastro.",
            "danger"
        )

        return _back_to_panel(
            int(
                datetime.utcnow()
                .timestamp()
            )
        )

    # ==========================================================
    # SESSÃO + CACHE BUSTER
    # ==========================================================

    timestamp = int(
        datetime.utcnow()
        .timestamp()
    )

    session[
        "avatar_url"
    ] = (
        f"{novo_url}?v={timestamp}"
    )

    session.modified = True

    # ==========================================================
    # LOG SEGURO
    #
    # Não registramos o nome original do arquivo.
    # ==========================================================

    current_app.logger.info(
        (
            "[AVATAR] Upload concluído. "
            f"empresa_id={emp.id} "
            f"source_format={image_info.get('format')} "
            f"source_width={image_info.get('width')} "
            f"source_height={image_info.get('height')}"
        )
    )

    flash(
        "Foto atualizada com sucesso.",
        "success"
    )

    return _back_to_panel(
        timestamp
    )

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

# ==============================================================
# PARQUE PRODUTIVO — CADASTRAR / LISTAR TEARES
# ==============================================================

@app.route(
    "/teares/cadastrar",
    methods=["GET", "POST"],
    endpoint="cadastrar_teares"
)
@limiter.limit(
    "20 per hour",
    key_func=_malharia_rate_limit_key,
    methods=["POST"]
)
@limiter.limit(
    "60 per day",
    key_func=_malharia_rate_limit_key,
    methods=["POST"]
)
def cadastrar_teares():
    """
    Cadastro de equipamentos da malharia autenticada.

    Segurança:
    - autenticação central da malharia;
    - CSRF global;
    - Rate Limit por malharia;
    - validação server-side dos campos obrigatórios;
    - limites básicos dos valores técnicos;
    - transação com rollback;
    - empresa_id sempre obtido da sessão;
    - nenhum empresa_id é aceito do navegador.
    """

    # ==========================================================
    # IDENTIDADE
    # ==========================================================

    emp, _user = (
        _get_empresa_usuario_da_sessao()
    )

    if not emp:

        flash(
            "Faça login para continuar.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    # ==========================================================
    # CONVERSÃO NUMÉRICA
    # ==========================================================

    def _to_int(
        valor
    ):

        try:

            if valor is None:

                return None

            texto = (
                str(valor)
                .strip()
                .replace(",", ".")
            )

            if not texto:

                return None

            return int(
                float(texto)
            )

        except (
            TypeError,
            ValueError
        ):

            return None

    # ==========================================================
    # GET
    # ==========================================================

    if request.method == "GET":

        teares = (
            Tear.query
            .filter_by(
                empresa_id=emp.id
            )
            .order_by(
                Tear.id.desc()
            )
            .all()
        )

        return render_template(
            "cadastrar_teares.html",
            empresa=emp,
            teares=teares,
            tear=None,
            assinatura_ativa=(
                (
                    emp.status_pagamento
                    or "pendente"
                )
                in (
                    "ativo",
                    "aprovado"
                )
            ),
            erros={}
        )

    # ==========================================================
    # POST — DADOS
    # ==========================================================

    marca = (
        request.form.get("marca")
        or ""
    ).strip()

    modelo = (
        request.form.get("modelo")
        or ""
    ).strip()

    tipo = (
        request.form.get("tipo")
        or ""
    ).strip().upper()

    finura = _to_int(
        request.form.get(
            "finura"
        )
    )

    diametro = _to_int(
        request.form.get(
            "diametro"
        )
    )

    alimentadores = _to_int(
        request.form.get(
            "alimentadores"
        )
    )

    pistas_cilindro = _to_int(
        request.form.get(
            "pistas_cilindro"
        )
    )

    pistas_disco = _to_int(
        request.form.get(
            "pistas_disco"
        )
    )

    elastano_raw = (
        request.form.get(
            "elastano"
        )
        or ""
    ).strip().lower()

    # ==========================================================
    # ELASTANO
    # ==========================================================

    if elastano_raw in {
        "sim",
        "s",
        "1",
        "true",
        "on",
        "yes",
        "y",
        "com",
        "tem",
    }:

        elastano = "Sim"

    elif elastano_raw in {
        "não",
        "nao",
        "n",
        "0",
        "false",
        "off",
        "no",
        "sem",
    }:

        elastano = "Não"

    else:

        elastano = None

    # ==========================================================
    # VALIDAÇÕES
    # ==========================================================

    erros = {}

    # ----------------------------------------------------------
    # Marca
    # ----------------------------------------------------------

    if not marca:

        erros["marca"] = (
            "Informe a marca do tear."
        )

    elif len(marca) > 100:

        erros["marca"] = (
            "A marca deve possuir no máximo 100 caracteres."
        )

    # ----------------------------------------------------------
    # Modelo
    # ----------------------------------------------------------

    if not modelo:

        erros["modelo"] = (
            "Informe o modelo do tear."
        )

    elif len(modelo) > 100:

        erros["modelo"] = (
            "O modelo deve possuir no máximo 100 caracteres."
        )

    # ----------------------------------------------------------
    # Tipo
    # ----------------------------------------------------------

    if tipo not in {
        "MONO",
        "DUPLA",
    }:

        erros["tipo"] = (
            "Selecione um tipo de tear válido."
        )

    # ----------------------------------------------------------
    # Finura
    # ----------------------------------------------------------

    if (
        finura is None
        or finura <= 0
        or finura > 100
    ):

        erros["finura"] = (
            "Informe uma finura válida."
        )

    # ----------------------------------------------------------
    # Diâmetro
    # ----------------------------------------------------------

    if (
        diametro is None
        or diametro <= 0
        or diametro > 200
    ):

        erros["diametro"] = (
            "Informe um diâmetro válido."
        )

    # ----------------------------------------------------------
    # Alimentadores
    # ----------------------------------------------------------

    if (
        alimentadores is None
        or alimentadores <= 0
        or alimentadores > 1000
    ):

        erros["alimentadores"] = (
            "Informe uma quantidade válida de alimentadores."
        )

    # ----------------------------------------------------------
    # Pistas opcionais
    # ----------------------------------------------------------

    if (
        pistas_cilindro is not None
        and (
            pistas_cilindro < 0
            or pistas_cilindro > 100
        )
    ):

        erros["pistas_cilindro"] = (
            "Informe uma quantidade válida de pistas do cilindro."
        )

    if (
        pistas_disco is not None
        and (
            pistas_disco < 0
            or pistas_disco > 100
        )
    ):

        erros["pistas_disco"] = (
            "Informe uma quantidade válida de pistas do disco."
        )

    # ----------------------------------------------------------
    # Elastano
    # ----------------------------------------------------------

    if elastano is None:

        erros["elastano"] = (
            "Informe se o tear possui recurso para elastano."
        )

    # ==========================================================
    # ERROS
    # ==========================================================

    if erros:

        teares = (
            Tear.query
            .filter_by(
                empresa_id=emp.id
            )
            .order_by(
                Tear.id.desc()
            )
            .all()
        )

        return render_template(
            "cadastrar_teares.html",
            empresa=emp,
            teares=teares,
            tear=None,
            assinatura_ativa=(
                (
                    emp.status_pagamento
                    or "pendente"
                )
                in (
                    "ativo",
                    "aprovado"
                )
            ),
            erros=erros,
            marca=marca,
            modelo=modelo,
            tipo=tipo,
            finura=finura,
            diametro=diametro,
            alimentadores=alimentadores,
            pistas_cilindro=pistas_cilindro,
            pistas_disco=pistas_disco,
            elastano=elastano
        )

    # ==========================================================
    # CRIAÇÃO
    # ==========================================================

    try:

        tear = Tear(
            empresa_id=emp.id,
            marca=marca,
            modelo=modelo,
            tipo=tipo,
            finura=finura,
            diametro=diametro,
            alimentadores=alimentadores,
            elastano=elastano,
        )

        # ------------------------------------------------------
        # Campos adicionais existentes no modelo atual
        # ------------------------------------------------------

        if hasattr(
            tear,
            "pistas_cilindro"
        ):

            tear.pistas_cilindro = (
                pistas_cilindro
            )

        if hasattr(
            tear,
            "pistas_disco"
        ):

            tear.pistas_disco = (
                pistas_disco
            )

        db.session.add(
            tear
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            (
                "[SECURITY][TEAR_CREATE] "
                "Falha ao cadastrar tear. "
                f"empresa_id={emp.id}"
            )
        )

        flash(
            (
                "Não foi possível cadastrar o tear agora. "
                "Tente novamente."
            ),
            "danger"
        )

        teares = (
            Tear.query
            .filter_by(
                empresa_id=emp.id
            )
            .order_by(
                Tear.id.desc()
            )
            .all()
        )

        return render_template(
            "cadastrar_teares.html",
            empresa=emp,
            teares=teares,
            tear=None,
            assinatura_ativa=(
                (
                    emp.status_pagamento
                    or "pendente"
                )
                in (
                    "ativo",
                    "aprovado"
                )
            ),
            erros={},
            marca=marca,
            modelo=modelo,
            tipo=tipo,
            finura=finura,
            diametro=diametro,
            alimentadores=alimentadores,
            pistas_cilindro=pistas_cilindro,
            pistas_disco=pistas_disco,
            elastano=elastano
        )

    # ==========================================================
    # LOG
    # ==========================================================

    current_app.logger.info(
        (
            "[SECURITY][TEAR_CREATE] "
            f"empresa_id={emp.id} "
            f"tear_id={tear.id}"
        )
    )

    flash(
        "Tear cadastrado com sucesso!",
        "success"
    )

    # Mantém o comportamento atual:
    # permite cadastrar vários teares em sequência.
    return redirect(
        url_for(
            "teares_form"
        )
    )

# Alias amigável do painel: /painel/teares
@app.route("/painel/teares", methods=["GET", "POST"], endpoint="teares_form")
def teares_form():
    return cadastrar_teares()

# --------------------------------------------------------------------
# Cadastro
# --------------------------------------------------------------------
@app.get(
    "/cadastro",
    endpoint="cadastro_get"
)
def cadastro_get():

    email = (
        request.args.get("email")
        or ""
    ).strip().lower()

    return render_template(
        "cadastro.html",
        email=email
    )

# ==============================================================
# CADASTRO DE MALHARIA — POST
# ==============================================================

@app.post(
    "/cadastro",
    endpoint="cadastro_post"
)
@limiter.limit(
    "5 per hour"
)
@limiter.limit(
    "20 per day"
)
def cadastro_post():
    """
    Cria uma nova conta de malharia.

    Segurança:
    - CSRF global;
    - máximo de 5 cadastros/hora por IP;
    - máximo de 20 cadastros/dia por IP;
    - valida e-mail e senha server-side;
    - impede colisão com Empresa e Usuario;
    - cria Empresa + Usuario na mesma transação;
    - utiliza rollback em caso de falha;
    - abre a sessão pelo helper central da malharia.
    """

    # ==========================================================
    # DADOS DO FORMULÁRIO
    # ==========================================================

    tipo = (
        request.form.get("tipo_pessoa")
        or "pf"
    ).strip().lower()

    cpf_cnpj = (
        request.form.get("cpf_cnpj")
        or ""
    ).strip()

    nome_completo = (
        request.form.get("nome")
        or ""
    ).strip()

    apelido = (
        request.form.get("apelido")
        or ""
    ).strip()

    nascimento = (
        request.form.get("nascimento")
        or ""
    ).strip()

    telefone = re.sub(
        r"\D+",
        "",
        request.form.get("telefone", "")
        or ""
    )

    email = (
        request.form.get("email")
        or ""
    ).strip().lower()

    senha = (
        request.form.get("senha")
        or ""
    )

    # ==========================================================
    # VALIDAÇÕES
    # ==========================================================

    erros = {}

    # ----------------------------------------------------------
    # E-mail
    # ----------------------------------------------------------

    email_valido = (
        bool(email)
        and len(email) <= 255
        and re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email
        )
        is not None
    )

    if not email_valido:

        erros["email"] = (
            "Informe um e-mail válido."
        )

    # ----------------------------------------------------------
    # Nome
    # ----------------------------------------------------------

    if len(nome_completo) < 2:

        erros["nome"] = (
            "Informe seu nome completo."
        )

    # ----------------------------------------------------------
    # Senha
    # ----------------------------------------------------------

    if len(senha) < 6:

        erros["senha"] = (
            "Crie uma senha com pelo menos 6 caracteres."
        )

    elif len(senha) > 128:

        erros["senha"] = (
            "A senha deve possuir no máximo 128 caracteres."
        )

    # ==========================================================
    # COLISÃO DE CONTA
    #
    # Um e-mail não pode existir simultaneamente como:
    #
    # comprador
    # +
    # nova malharia
    #
    # sem um fluxo específico de conversão de conta.
    # ==========================================================

    if email_valido:

        empresa_existente = (
            Empresa.query
            .filter(
                func.lower(
                    Empresa.email
                )
                == email
            )
            .first()
        )

        usuario_existente = (
            Usuario.query
            .filter(
                func.lower(
                    Usuario.email
                )
                == email
            )
            .first()
        )

        if (
            empresa_existente
            or usuario_existente
        ):

            erros["email"] = (
                "Este e-mail já possui uma conta no AcheTece."
            )

    # ==========================================================
    # NOME DA EMPRESA
    # ==============================================================

    nome_empresa = (
        apelido
        or nome_completo
    )

    if nome_empresa:

        empresa_nome_existente = (
            Empresa.query
            .filter(
                func.lower(
                    Empresa.nome
                )
                == nome_empresa.lower()
            )
            .first()
        )

        if empresa_nome_existente:

            erros["nome"] = (
                "Já existe uma empresa cadastrada com este nome."
            )

    # ==========================================================
    # APELIDO
    # ==============================================================

    if apelido:

        apelido_existente = (
            Empresa.query
            .filter(
                func.lower(
                    Empresa.apelido
                )
                == apelido.lower()
            )
            .first()
        )

        if apelido_existente:

            erros["apelido"] = (
                "Este nome de exibição já está em uso."
            )

    # ==========================================================
    # RETORNA O FORMULÁRIO EM CASO DE ERRO
    # ==============================================================

    if erros:

        try:

            return render_template(
                "cadastro.html",
                erros=erros,
                email=email,
                nome=nome_completo,
                apelido=apelido,
                telefone=telefone,
                cpf_cnpj=cpf_cnpj,
                tipo_pessoa=tipo,
                nascimento=nascimento
            )

        except TemplateNotFound:

            flash(
                next(
                    iter(
                        erros.values()
                    )
                ),
                "error"
            )

            return redirect(
                url_for(
                    "cadastro_get",
                    email=email
                )
            )

    # ==========================================================
    # RESPONSÁVEL
    # ==============================================================

    partes = (
        nome_completo
        .split()
    )

    responsavel_nome = (
        partes[0]
    )

    responsavel_sobrenome = (
        " ".join(
            partes[1:]
        )
        if len(partes) > 1
        else None
    )

    # ==========================================================
    # CRIAÇÃO DA CONTA
    #
    # Usuario + Empresa são criados na mesma transação.
    # ==============================================================

    try:

        senha_hash = (
            generate_password_hash(
                senha
            )
        )

        # ------------------------------------------------------
        # USUÁRIO
        # ------------------------------------------------------

        novo_usuario = Usuario(
            email=email,
            senha_hash=senha_hash,
            role="malharia",
            is_active=True
        )

        db.session.add(
            novo_usuario
        )

        db.session.flush()

        # ------------------------------------------------------
        # EMPRESA
        # ------------------------------------------------------

        nova_empresa = Empresa(
            user_id=novo_usuario.id,
            nome=nome_empresa,
            apelido=apelido or None,
            email=email,
            senha=senha_hash,
            cidade=None,
            estado=None,
            telefone=telefone or None,
            status_pagamento="pendente",
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=responsavel_sobrenome
        )

        db.session.add(
            nova_empresa
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[MALHARIA] Falha ao criar conta."
        )

        flash(
            (
                "Não foi possível criar sua conta agora. "
                "Tente novamente."
            ),
            "danger"
        )

        return render_template(
            "cadastro.html",
            erros={},
            email=email,
            nome=nome_completo,
            apelido=apelido,
            telefone=telefone,
            cpf_cnpj=cpf_cnpj,
            tipo_pessoa=tipo,
            nascimento=nascimento
        )

    # ==========================================================
    # SESSÃO CENTRALIZADA
    # ==============================================================

    _abrir_sessao_malharia(
        nova_empresa
    )

    flash(
        (
            "Conta criada! "
            "Complete os dados da sua empresa para continuar."
        ),
        "success"
    )

    return redirect(
        url_for(
            "editar_empresa"
        )
    )

# ==============================================================
# PARQUE PRODUTIVO — EDITAR TEAR
# ==============================================================

@app.route(
    "/editar_tear/<int:id>",
    methods=["GET", "POST"],
    endpoint="editar_tear"
)
@limiter.limit(
    "20 per hour",
    key_func=_malharia_rate_limit_key,
    methods=["POST"]
)
@limiter.limit(
    "60 per day",
    key_func=_malharia_rate_limit_key,
    methods=["POST"]
)
def editar_tear(id):
    """
    Edita um tear pertencente à malharia autenticada.

    Segurança:
    - autenticação central da malharia;
    - CSRF global;
    - Rate Limit por malharia;
    - verifica propriedade do tear;
    - valida os dados antes de alterar o objeto;
    - não aceita empresa_id vindo do navegador;
    - transação com rollback;
    - log seguro da alteração.
    """

    # ==========================================================
    # IDENTIDADE
    # ==========================================================

    emp, _user = (
        _get_empresa_usuario_da_sessao()
    )

    if not emp:

        flash(
            "Faça login para continuar.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    # ==========================================================
    # TEAR
    # ==========================================================

    tear = db.session.get(
        Tear,
        id
    )

    if tear is None:

        abort(404)

    # ==========================================================
    # PROPRIEDADE
    #
    # Uma malharia nunca pode editar tear de outra empresa.
    # ==========================================================

    if tear.empresa_id != emp.id:

        current_app.logger.warning(
            (
                "[SECURITY][TEAR_EDIT_FORBIDDEN] "
                f"empresa_id={emp.id} "
                f"tear_id={id}"
            )
        )

        abort(403)

    # ==========================================================
    # GET
    # ==========================================================

    if request.method == "GET":

        return render_template(
            "editar_tear.html",
            empresa=emp,
            tear=tear
        )

    # ==========================================================
    # CONVERSÃO NUMÉRICA
    # ==========================================================

    def _to_int(
        valor
    ):

        try:

            if valor is None:

                return None

            texto = (
                str(valor)
                .strip()
                .replace(",", ".")
            )

            if not texto:

                return None

            return int(
                float(texto)
            )

        except (
            TypeError,
            ValueError
        ):

            return None

    # ==========================================================
    # POST — DADOS
    # ==========================================================

    marca = (
        request.form.get("marca")
        or ""
    ).strip()

    modelo = (
        request.form.get("modelo")
        or ""
    ).strip()

    tipo = (
        request.form.get("tipo")
        or ""
    ).strip().upper()

    finura = _to_int(
        request.form.get(
            "finura"
        )
    )

    diametro = _to_int(
        request.form.get(
            "diametro"
        )
    )

    alimentadores = _to_int(
        request.form.get(
            "alimentadores"
        )
    )

    pistas_cilindro = _to_int(
        request.form.get(
            "pistas_cilindro"
        )
    )

    pistas_disco = _to_int(
        request.form.get(
            "pistas_disco"
        )
    )

    elastano_raw = (
        request.form.get(
            "elastano"
        )
        or ""
    ).strip().lower()

    # ==========================================================
    # ELASTANO
    # ==========================================================

    if elastano_raw in {
        "sim",
        "s",
        "1",
        "true",
        "on",
        "yes",
        "y",
        "com",
        "tem",
    }:

        elastano = "Sim"

    elif elastano_raw in {
        "não",
        "nao",
        "n",
        "0",
        "false",
        "off",
        "no",
        "sem",
    }:

        elastano = "Não"

    else:

        elastano = None

    # ==========================================================
    # VALIDAÇÕES
    #
    # Validamos tudo ANTES de alterar o objeto Tear.
    # ==========================================================

    erros = []

    # ----------------------------------------------------------
    # Marca
    # ----------------------------------------------------------

    if not marca:

        erros.append(
            "Informe a marca do tear."
        )

    elif len(marca) > 100:

        erros.append(
            "A marca deve possuir no máximo 100 caracteres."
        )

    # ----------------------------------------------------------
    # Modelo
    # ----------------------------------------------------------

    if not modelo:

        erros.append(
            "Informe o modelo do tear."
        )

    elif len(modelo) > 100:

        erros.append(
            "O modelo deve possuir no máximo 100 caracteres."
        )

    # ----------------------------------------------------------
    # Tipo
    # ----------------------------------------------------------

    if tipo not in {
        "MONO",
        "DUPLA",
    }:

        erros.append(
            "Selecione um tipo de tear válido."
        )

    # ----------------------------------------------------------
    # Finura
    # ----------------------------------------------------------

    if (
        finura is None
        or finura <= 0
        or finura > 100
    ):

        erros.append(
            "Informe uma finura válida."
        )

    # ----------------------------------------------------------
    # Diâmetro
    # ----------------------------------------------------------

    if (
        diametro is None
        or diametro <= 0
        or diametro > 200
    ):

        erros.append(
            "Informe um diâmetro válido."
        )

    # ----------------------------------------------------------
    # Alimentadores
    # ----------------------------------------------------------

    if (
        alimentadores is None
        or alimentadores <= 0
        or alimentadores > 1000
    ):

        erros.append(
            "Informe uma quantidade válida de alimentadores."
        )

    # ----------------------------------------------------------
    # Pistas do cilindro
    # ----------------------------------------------------------

    if (
        pistas_cilindro is not None
        and (
            pistas_cilindro < 0
            or pistas_cilindro > 100
        )
    ):

        erros.append(
            "Informe uma quantidade válida de pistas do cilindro."
        )

    # ----------------------------------------------------------
    # Pistas do disco
    # ----------------------------------------------------------

    if (
        pistas_disco is not None
        and (
            pistas_disco < 0
            or pistas_disco > 100
        )
    ):

        erros.append(
            "Informe uma quantidade válida de pistas do disco."
        )

    # ----------------------------------------------------------
    # Elastano
    # ----------------------------------------------------------

    if elastano is None:

        erros.append(
            "Informe se o tear possui recurso para elastano."
        )

    # ==========================================================
    # DADOS INVÁLIDOS
    # ==========================================================

    if erros:

        for mensagem in erros:

            flash(
                mensagem,
                "warning"
            )

        current_app.logger.warning(
            (
                "[SECURITY][TEAR_EDIT_REJECTED] "
                f"empresa_id={emp.id} "
                f"tear_id={tear.id} "
                f"errors={len(erros)}"
            )
        )

        return redirect(
            url_for(
                "editar_tear",
                id=tear.id
            )
        )

    # ==========================================================
    # ALTERAÇÃO
    #
    # Somente agora modificamos o objeto.
    # ==========================================================

    try:

        tear.marca = marca

        tear.modelo = modelo

        tear.tipo = tipo

        tear.finura = finura

        tear.diametro = diametro

        tear.alimentadores = (
            alimentadores
        )

        tear.elastano = (
            elastano
        )

        # ------------------------------------------------------
        # Campos opcionais / compatibilidade
        # ------------------------------------------------------

        if hasattr(
            tear,
            "pistas_cilindro"
        ):

            tear.pistas_cilindro = (
                pistas_cilindro
            )

        if hasattr(
            tear,
            "pistas_disco"
        ):

            tear.pistas_disco = (
                pistas_disco
            )

        # Alguns códigos antigos podem possuir "galga"
        # como espelho da finura.
        if hasattr(
            tear,
            "galga"
        ):

            tear.galga = (
                finura
            )

        # Alguns bancos antigos podem possuir kit_elastano.
        if hasattr(
            tear,
            "kit_elastano"
        ):

            tear.kit_elastano = (
                elastano
            )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            (
                "[SECURITY][TEAR_EDIT] "
                "Falha ao atualizar tear. "
                f"empresa_id={emp.id} "
                f"tear_id={tear.id}"
            )
        )

        flash(
            (
                "Não foi possível atualizar o tear agora. "
                "Tente novamente."
            ),
            "danger"
        )

        return redirect(
            url_for(
                "editar_tear",
                id=tear.id
            )
        )

    # ==========================================================
    # LOG
    # ==========================================================

    current_app.logger.info(
        (
            "[SECURITY][TEAR_EDIT] "
            f"empresa_id={emp.id} "
            f"tear_id={tear.id}"
        )
    )

    flash(
        "Tear atualizado com sucesso!",
        "success"
    )

    return redirect(
        url_for(
            "painel_malharia"
        )
    )

# ==============================================================
# PARQUE PRODUTIVO — EXCLUIR TEAR
# ==============================================================

@app.post(
    "/tear/<int:id>/excluir",
    endpoint="excluir_tear"
)
@limiter.limit(
    "10 per hour",
    key_func=_malharia_rate_limit_key
)
@limiter.limit(
    "30 per day",
    key_func=_malharia_rate_limit_key
)
def excluir_tear(id):
    """
    Exclui um tear da malharia autenticada.

    Segurança:
    - somente POST;
    - CSRF global;
    - Rate Limit por malharia;
    - verifica propriedade do tear;
    - bloqueia exclusão quando houver histórico de matching;
    - transação com rollback;
    - não aceita empresa_id enviado pelo navegador.
    """

    # ==========================================================
    # IDENTIDADE
    # ==========================================================

    empresa = (
        _pegar_empresa_do_usuario(
            required=True
        )
    )

    if not isinstance(
        empresa,
        Empresa
    ):
        return empresa

    # ==========================================================
    # TEAR
    # ==========================================================

    tear = db.session.get(
        Tear,
        id
    )

    if tear is None:
        abort(404)

    # ==========================================================
    # PROPRIEDADE
    # ==========================================================

    if tear.empresa_id != empresa.id:

        current_app.logger.warning(
            (
                "[SECURITY][TEAR_DELETE_FORBIDDEN] "
                f"empresa_id={empresa.id} "
                f"tear_id={id}"
            )
        )

        abort(403)

    # ==========================================================
    # HISTÓRICO DE MATCHING
    # ==========================================================

    total_matches = (
        DemandMatch.query
        .filter(
            DemandMatch.tear_id
            == tear.id
        )
        .count()
    )

    if total_matches > 0:

        current_app.logger.warning(
            (
                "[SECURITY][TEAR_DELETE_BLOCKED] "
                f"empresa_id={empresa.id} "
                f"tear_id={tear.id} "
                f"matches={total_matches}"
            )
        )

        flash(
            (
                "Este tear já participou de processos de matching "
                "e não pode ser excluído definitivamente. "
                "O histórico técnico precisa ser preservado."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "painel_malharia"
            )
        )

    # ==========================================================
    # EXCLUSÃO
    # ==========================================================

    try:

        db.session.delete(
            tear
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            (
                "[SECURITY][TEAR_DELETE] "
                "Falha ao excluir tear. "
                f"empresa_id={empresa.id} "
                f"tear_id={id}"
            )
        )

        flash(
            (
                "Não foi possível excluir o tear agora. "
                "Tente novamente."
            ),
            "danger"
        )

        return redirect(
            url_for(
                "painel_malharia"
            )
        )

    # ==========================================================
    # LOG
    # ==========================================================

    current_app.logger.info(
        (
            "[SECURITY][TEAR_DELETE] "
            f"empresa_id={empresa.id} "
            f"tear_id={id} "
            "resultado=excluido"
        )
    )

    flash(
        "Tear excluído com sucesso!",
        "success"
    )

    return redirect(
        url_for(
            "painel_malharia"
        )
    )

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
# ==============================================================
# CADASTRO COMPLETO DE MALHARIA
# ==============================================================

@app.route(
    "/cadastrar_empresa",
    methods=["GET", "POST"]
)
@limiter.limit(
    "5 per hour",
    methods=["POST"]
)
@limiter.limit(
    "20 per day",
    methods=["POST"]
)
def cadastrar_empresa():
    """
    Cadastro completo de uma nova malharia.

    Segurança:
    - GET apenas exibe o formulário;
    - POST protegido pelo CSRF global;
    - limite de 5 cadastros por hora por IP;
    - limite de 20 cadastros por dia por IP;
    - valida dados novamente no servidor;
    - exige senha entre 6 e 128 caracteres;
    - impede colisão de e-mail com Empresa e Usuario;
    - cria Usuario + Empresa na mesma transação;
    - utiliza rollback em caso de falha;
    - abre sessão pelo helper central da malharia.
    """

    # ==========================================================
    # ESTADOS
    # ==========================================================

    estados = [
        "AC", "AL", "AM", "AP", "BA", "CE", "DF",
        "ES", "GO", "MA", "MG", "MS", "MT", "PA",
        "PB", "PE", "PI", "PR", "RJ", "RN", "RO",
        "RR", "RS", "SC", "SE", "SP", "TO"
    ]

    # ==========================================================
    # GET
    # ==========================================================

    if request.method == "GET":

        email_inicial = (
            request.args.get("email")
            or ""
        ).strip().lower()

        return render_template(
            "cadastrar_empresa.html",
            estados=estados,
            email=email_inicial
        )

    # ==========================================================
    # POST — DADOS RECEBIDOS
    # ==========================================================

    nome = (
        request.form.get("nome")
        or ""
    ).strip()

    apelido = (
        request.form.get("apelido")
        or ""
    ).strip()

    email = (
        request.form.get("email")
        or ""
    ).strip().lower()

    # Não usamos .strip() na senha.
    # A senha deve ser tratada exatamente como foi digitada.
    senha = (
        request.form.get("senha")
        or ""
    )

    cidade = (
        request.form.get("cidade")
        or ""
    ).strip()

    estado = (
        request.form.get("estado")
        or ""
    ).strip().upper()

    telefone = _only_digits(
        request.form.get(
            "telefone",
            ""
        )
    )

    responsavel_nome = (
        request.form.get(
            "responsavel_nome"
        )
        or ""
    ).strip()

    responsavel_sobrenome = (
        request.form.get(
            "responsavel_sobrenome"
        )
        or ""
    ).strip()

    endereco_full = (
        request.form.get("endereco")
        or ""
    ).strip()

    cep_raw = (
        request.form.get("cep")
        or ""
    ).strip()

    cep_digits = re.sub(
        r"\D",
        "",
        cep_raw
    )

    # ==========================================================
    # VALIDAÇÕES
    # ==========================================================

    erros = {}

    # ----------------------------------------------------------
    # Nome
    # ----------------------------------------------------------

    if len(nome) < 2:

        erros["nome"] = (
            "Informe o nome da empresa."
        )

    elif (
        Empresa.query
        .filter(
            func.lower(Empresa.nome)
            == nome.lower()
        )
        .first()
    ):

        erros["nome"] = (
            "Já existe uma empresa cadastrada com este nome."
        )

    # ----------------------------------------------------------
    # Apelido
    # ----------------------------------------------------------

    if apelido:

        apelido_existente = (
            Empresa.query
            .filter(
                func.lower(Empresa.apelido)
                == apelido.lower()
            )
            .first()
        )

        if apelido_existente:

            erros["apelido"] = (
                "Este nome de exibição já está em uso."
            )

    # ----------------------------------------------------------
    # E-mail
    # ----------------------------------------------------------

    email_valido = (
        bool(email)
        and len(email) <= 255
        and re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email
        )
        is not None
    )

    if not email_valido:

        erros["email"] = (
            "Informe um e-mail válido."
        )

    else:

        empresa_email_existente = (
            Empresa.query
            .filter(
                func.lower(Empresa.email)
                == email
            )
            .first()
        )

        usuario_email_existente = (
            Usuario.query
            .filter(
                func.lower(Usuario.email)
                == email
            )
            .first()
        )

        if (
            empresa_email_existente
            or usuario_email_existente
        ):

            erros["email"] = (
                "Este e-mail já possui uma conta no AcheTece."
            )

    # ----------------------------------------------------------
    # Senha
    # ----------------------------------------------------------

    if len(senha) < 6:

        erros["senha"] = (
            "A senha precisa ter pelo menos 6 caracteres."
        )

    elif len(senha) > 128:

        erros["senha"] = (
            "A senha deve possuir no máximo 128 caracteres."
        )

    # ----------------------------------------------------------
    # Telefone
    # ----------------------------------------------------------

    if (
        len(telefone) < 10
        or len(telefone) > 13
    ):

        erros["telefone"] = (
            "Telefone inválido."
        )

    # ----------------------------------------------------------
    # Estado
    # ----------------------------------------------------------

    if estado not in estados:

        erros["estado"] = (
            "Estado inválido."
        )

    # ----------------------------------------------------------
    # Cidade
    # ----------------------------------------------------------

    if not cidade:

        erros["cidade"] = (
            "Selecione a cidade."
        )

    # ----------------------------------------------------------
    # Responsável
    # ----------------------------------------------------------

    if (
        not responsavel_nome
        or len(
            re.sub(
                r"[^A-Za-zÀ-ÿ]",
                "",
                responsavel_nome
            )
        ) < 2
    ):

        erros["responsavel_nome"] = (
            "Informe o nome do responsável."
        )

    # ----------------------------------------------------------
    # Endereço
    # ----------------------------------------------------------

    if not endereco_full:

        erros["endereco"] = (
            "Informe o endereço completo."
        )

    # ----------------------------------------------------------
    # CEP
    # ----------------------------------------------------------

    if not re.fullmatch(
        r"\d{8}",
        cep_digits or ""
    ):

        erros["cep"] = (
            "Informe um CEP válido "
            "(00000-000 ou 00000000)."
        )

    # ==========================================================
    # ERROS DE VALIDAÇÃO
    # ==========================================================

    if erros:

        return render_template(
            "cadastrar_empresa.html",
            erro="Corrija os campos.",
            erros=erros,
            estados=estados,
            nome=nome,
            apelido=apelido,
            email=email,
            cidade=cidade,
            estado=estado,
            telefone=telefone,
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=responsavel_sobrenome,
            endereco=endereco_full,
            cep=cep_raw
        )

    # ==========================================================
    # CRIAÇÃO
    #
    # Usuario e Empresa são criados na mesma transação.
    # ==============================================================

    try:

        senha_hash = (
            generate_password_hash(
                senha
            )
        )

        # ------------------------------------------------------
        # USUARIO
        # ------------------------------------------------------

        novo_usuario = Usuario(
            email=email,
            senha_hash=senha_hash,
            role="malharia",
            is_active=True
        )

        db.session.add(
            novo_usuario
        )

        db.session.flush()

        # ------------------------------------------------------
        # EMPRESA
        # ------------------------------------------------------

        nova_empresa = Empresa(
            user_id=novo_usuario.id,
            nome=nome,
            apelido=apelido or None,
            email=email,
            senha=senha_hash,
            cidade=cidade,
            estado=estado,
            telefone=telefone,
            status_pagamento="pendente",
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=(
                responsavel_sobrenome
                or None
            )
        )

        # ------------------------------------------------------
        # ENDEREÇO / CEP
        # ------------------------------------------------------

        _set_if_has(
            nova_empresa,
            [
                "endereco",
                "logradouro",
                "endereco_completo"
            ],
            endereco_full
        )

        _set_if_has(
            nova_empresa,
            [
                "cep",
                "CEP"
            ],
            cep_digits
        )

        db.session.add(
            nova_empresa
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[MALHARIA] Falha ao concluir cadastro completo."
        )

        flash(
            (
                "Não foi possível concluir o cadastro agora. "
                "Tente novamente."
            ),
            "danger"
        )

        return render_template(
            "cadastrar_empresa.html",
            erro=(
                "Não foi possível concluir o cadastro."
            ),
            erros={},
            estados=estados,
            nome=nome,
            apelido=apelido,
            email=email,
            cidade=cidade,
            estado=estado,
            telefone=telefone,
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=responsavel_sobrenome,
            endereco=endereco_full,
            cep=cep_raw
        )

    # ==========================================================
    # SESSÃO
    # ==============================================================

    _abrir_sessao_malharia(
        nova_empresa
    )

    flash(
        "Cadastro concluído!",
        "success"
    )

    return redirect(
        url_for(
            "painel_malharia"
        )
    )

# ==============================================================
# EDIÇÃO DO PERFIL DA MALHARIA
# ==============================================================

@app.route(
    "/editar_empresa",
    methods=["GET", "POST"],
    endpoint="editar_empresa"
)
@limiter.limit(
    "10 per hour",
    key_func=_malharia_rate_limit_key,
    methods=["POST"]
)
@limiter.limit(
    "30 per day",
    key_func=_malharia_rate_limit_key,
    methods=["POST"]
)
def editar_empresa():
    """
    Edita os dados cadastrais da malharia autenticada.

    Segurança:
    - utiliza identidade centralizada da malharia;
    - POST protegido pelo CSRF global;
    - Rate Limit por empresa autenticada;
    - impede colisão de nome, apelido e e-mail;
    - verifica e-mail também na tabela Usuario;
    - mantém Empresa e Usuario sincronizados;
    - senha nova é gravada nos dois registros;
    - alterações são feitas em uma única transação;
    - rollback em qualquer falha de banco;
    - atualiza os dados correspondentes da sessão.
    """

    # ==========================================================
    # IDENTIDADE DA MALHARIA
    # ==========================================================

    empresa, usuario = (
        _get_empresa_usuario_da_sessao()
    )

    if not empresa:

        session.clear()

        return redirect(
            url_for("login")
        )

    # ==========================================================
    # ESTADOS
    # ==========================================================

    estados = [
        "AC", "AL", "AM", "AP", "BA", "CE", "DF",
        "ES", "GO", "MA", "MG", "MS", "MT", "PA",
        "PB", "PE", "PI", "PR", "RJ", "RN", "RO",
        "RR", "RS", "SC", "SE", "SP", "TO"
    ]

    # ==========================================================
    # GET
    # ==========================================================

    if request.method == "GET":

        endereco_atual = (
            getattr(
                empresa,
                "endereco",
                None
            )
            or getattr(
                empresa,
                "logradouro",
                None
            )
            or getattr(
                empresa,
                "endereco_completo",
                ""
            )
        )

        cep_atual = (
            getattr(
                empresa,
                "cep",
                None
            )
            or getattr(
                empresa,
                "CEP",
                ""
            )
        )

        try:

            cidades = (
                lista_cidades_por_uf(
                    empresa.estado
                )
                if getattr(
                    empresa,
                    "estado",
                    None
                )
                else []
            )

        except Exception:

            cidades = []

        return render_template(
            "editar_empresa.html",
            estados=estados,
            erros={},
            nome=empresa.nome or "",
            apelido=empresa.apelido or "",
            email=empresa.email or "",
            cidade=empresa.cidade or "",
            estado=empresa.estado or "",
            telefone=empresa.telefone or "",
            responsavel_nome=(
                empresa.responsavel_nome
                or ""
            ),
            responsavel_sobrenome=(
                empresa.responsavel_sobrenome
                or ""
            ),
            endereco=endereco_atual or "",
            cep=cep_atual or "",
            cidades=cidades
        )

    # ==========================================================
    # POST — DADOS
    # ==========================================================

    nome = (
        request.form.get("nome")
        or ""
    ).strip()

    apelido = (
        request.form.get("apelido")
        or ""
    ).strip()

    email = (
        request.form.get("email")
        or ""
    ).strip().lower()

    # Nova senha é opcional.
    # Não aplicamos .strip() para não alterar silenciosamente
    # uma senha digitada pelo usuário.
    senha = (
        request.form.get("senha")
        or ""
    )

    cidade = (
        request.form.get("cidade")
        or ""
    ).strip()

    estado = (
        request.form.get("estado")
        or ""
    ).strip().upper()

    telefone = _only_digits(
        request.form.get(
            "telefone",
            ""
        )
    )

    responsavel_nome = (
        request.form.get(
            "responsavel_nome"
        )
        or ""
    ).strip()

    responsavel_sobrenome = (
        request.form.get(
            "responsavel_sobrenome"
        )
        or ""
    ).strip()

    endereco_full = (
        request.form.get("endereco")
        or ""
    ).strip()

    cep_raw = (
        request.form.get("cep")
        or ""
    ).strip()

    cep_digits = re.sub(
        r"\D",
        "",
        cep_raw
    )

    # ==========================================================
    # VALIDAÇÕES
    # ==========================================================

    erros = {}

    # ----------------------------------------------------------
    # Nome da empresa
    # ----------------------------------------------------------

    if len(nome) < 2:

        erros["nome"] = (
            "Informe o nome da empresa."
        )

    elif len(nome) > 100:

        erros["nome"] = (
            "O nome da empresa deve possuir "
            "no máximo 100 caracteres."
        )

    else:

        empresa_mesmo_nome = (
            Empresa.query
            .filter(
                func.lower(
                    Empresa.nome
                )
                == nome.lower(),

                Empresa.id
                != empresa.id
            )
            .first()
        )

        if empresa_mesmo_nome:

            erros["nome"] = (
                "Já existe uma empresa cadastrada "
                "com este nome."
            )

    # ----------------------------------------------------------
    # Apelido
    # ----------------------------------------------------------

    if len(apelido) > 50:

        erros["apelido"] = (
            "O nome de exibição deve possuir "
            "no máximo 50 caracteres."
        )

    elif apelido:

        empresa_mesmo_apelido = (
            Empresa.query
            .filter(
                func.lower(
                    Empresa.apelido
                )
                == apelido.lower(),

                Empresa.id
                != empresa.id
            )
            .first()
        )

        if empresa_mesmo_apelido:

            erros["apelido"] = (
                "Este nome de exibição já está em uso."
            )

    # ----------------------------------------------------------
    # E-mail
    # ----------------------------------------------------------

    email_valido = (
        bool(email)
        and len(email) <= 100
        and re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email
        )
        is not None
    )

    if not email_valido:

        erros["email"] = (
            "Informe um e-mail válido."
        )

    else:

        # ------------------------------------------------------
        # Conflito com outra Empresa
        # ------------------------------------------------------

        outra_empresa_email = (
            Empresa.query
            .filter(
                func.lower(
                    Empresa.email
                )
                == email,

                Empresa.id
                != empresa.id
            )
            .first()
        )

        if outra_empresa_email:

            erros["email"] = (
                "Este e-mail já possui uma conta no AcheTece."
            )

        # ------------------------------------------------------
        # Conflito com outro Usuario
        # ------------------------------------------------------

        query_usuario_email = (
            Usuario.query
            .filter(
                func.lower(
                    Usuario.email
                )
                == email
            )
        )

        if (
            usuario is not None
            and getattr(
                usuario,
                "id",
                None
            )
        ):

            query_usuario_email = (
                query_usuario_email
                .filter(
                    Usuario.id
                    != usuario.id
                )
            )

        outro_usuario_email = (
            query_usuario_email
            .first()
        )

        if outro_usuario_email:

            erros["email"] = (
                "Este e-mail já possui uma conta no AcheTece."
            )

    # ----------------------------------------------------------
    # Nova senha — opcional
    # ----------------------------------------------------------

    if senha:

        if len(senha) < 6:

            erros["senha"] = (
                "A nova senha precisa ter "
                "pelo menos 6 caracteres."
            )

        elif len(senha) > 128:

            erros["senha"] = (
                "A nova senha deve possuir "
                "no máximo 128 caracteres."
            )

    # ----------------------------------------------------------
    # Telefone
    # ----------------------------------------------------------

    if telefone and (
        len(telefone) < 10
        or len(telefone) > 13
    ):

        erros["telefone"] = (
            "Telefone inválido."
        )

    # ----------------------------------------------------------
    # Estado
    # ----------------------------------------------------------

    if estado not in estados:

        erros["estado"] = (
            "Selecione um estado válido."
        )

    # ----------------------------------------------------------
    # Cidade
    # ----------------------------------------------------------

    if not cidade:

        erros["cidade"] = (
            "Selecione a cidade."
        )

    elif len(cidade) > 100:

        erros["cidade"] = (
            "A cidade informada é inválida."
        )

    # ----------------------------------------------------------
    # Responsável
    # ----------------------------------------------------------

    if (
        not responsavel_nome
        or len(
            re.sub(
                r"[^A-Za-zÀ-ÿ]",
                "",
                responsavel_nome
            )
        ) < 2
    ):

        erros["responsavel_nome"] = (
            "Informe o primeiro nome do responsável."
        )

    elif len(responsavel_nome) > 120:

        erros["responsavel_nome"] = (
            "O nome do responsável é muito longo."
        )

    if len(responsavel_sobrenome) > 120:

        erros["responsavel_sobrenome"] = (
            "O sobrenome do responsável é muito longo."
        )

    # ----------------------------------------------------------
    # Endereço
    # ----------------------------------------------------------

    if not endereco_full:

        erros["endereco"] = (
            "Informe o endereço completo."
        )

    elif len(endereco_full) > 240:

        erros["endereco"] = (
            "O endereço informado é muito longo."
        )

    # ----------------------------------------------------------
    # CEP
    # ----------------------------------------------------------

    if not re.fullmatch(
        r"\d{8}",
        cep_digits or ""
    ):

        erros["cep"] = (
            "Informe um CEP válido "
            "(00000-000 ou 00000000)."
        )

    # ==========================================================
    # RETORNO COM ERROS
    # ==========================================================

    if erros:

        try:

            cidades = (
                lista_cidades_por_uf(
                    estado
                )
                if estado
                else []
            )

        except Exception:

            cidades = []

        return render_template(
            "editar_empresa.html",
            erro="Corrija os campos.",
            erros=erros,
            estados=estados,
            nome=nome,
            apelido=apelido,
            email=email,
            cidade=cidade,
            estado=estado,
            telefone=telefone,
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=responsavel_sobrenome,
            endereco=endereco_full,
            cep=cep_raw,
            cidades=cidades
        )

    # ==========================================================
    # ALTERAÇÕES SENSÍVEIS
    # ==========================================================

    email_anterior = (
        empresa.email
        or ""
    ).strip().lower()

    email_alterado = (
        email != email_anterior
    )

    senha_alterada = bool(
        senha
    )

    # ==========================================================
    # TRANSAÇÃO
    # ==========================================================

    try:

        # ------------------------------------------------------
        # Garante Usuario vinculado
        # ------------------------------------------------------

        if usuario is None:

            if getattr(
                empresa,
                "user_id",
                None
            ):

                usuario = db.session.get(
                    Usuario,
                    empresa.user_id
                )

        if usuario is None:

            # Conta legada sem Usuario relacionado.
            #
            # Como os conflitos de e-mail já foram verificados
            # acima, podemos criar o espelho de autenticação.
            usuario = Usuario(
                email=email,
                senha_hash=empresa.senha,
                role="malharia",
                is_active=True
            )

            db.session.add(
                usuario
            )

            db.session.flush()

            empresa.user_id = (
                usuario.id
            )

        # ------------------------------------------------------
        # Integridade do perfil
        # ------------------------------------------------------

        role_usuario = (
            usuario.role
            or ""
        ).strip().lower()

        if role_usuario not in {
            "",
            "malharia"
        }:

            raise RuntimeError(
                "Usuário vinculado possui perfil incompatível."
            )

        usuario.role = (
            "malharia"
        )

        # ------------------------------------------------------
        # Empresa
        # ------------------------------------------------------

        empresa.nome = nome

        empresa.apelido = (
            apelido
            or None
        )

        empresa.email = email

        empresa.cidade = cidade

        empresa.estado = estado

        empresa.telefone = (
            telefone
            or None
        )

        empresa.responsavel_nome = (
            responsavel_nome
        )

        empresa.responsavel_sobrenome = (
            responsavel_sobrenome
            or None
        )

        _set_if_has(
            empresa,
            [
                "endereco",
                "logradouro",
                "endereco_completo"
            ],
            endereco_full
        )

        _set_if_has(
            empresa,
            [
                "cep",
                "CEP"
            ],
            cep_digits
        )

        # ------------------------------------------------------
        # Usuario
        # ------------------------------------------------------

        usuario.email = email

        # ------------------------------------------------------
        # Nova senha
        #
        # O MESMO hash é gravado na Empresa e no Usuario.
        # ------------------------------------------------------

        if senha:

            novo_hash = (
                generate_password_hash(
                    senha
                )
            )

            empresa.senha = (
                novo_hash
            )

            usuario.senha_hash = (
                novo_hash
            )

        # ------------------------------------------------------
        # Caso legado:
        # Usuario ainda não possui senha mas Empresa possui.
        # ------------------------------------------------------

        elif (
            not usuario.senha_hash
            and empresa.senha
        ):

            usuario.senha_hash = (
                empresa.senha
            )

        # ------------------------------------------------------
        # Se o e-mail foi alterado, invalida tokens antigos
        # de recuperação da malharia.
        # ------------------------------------------------------

        if email_alterado:

            now = datetime.utcnow()

            tokens_ativos = (
                PasswordResetToken.query
                .filter(
                    PasswordResetToken.account_type
                    == "malharia",

                    PasswordResetToken.account_id
                    == empresa.id,

                    PasswordResetToken.used_at.is_(None),
                )
                .all()
            )

            for token in tokens_ativos:

                token.used_at = now

        # ------------------------------------------------------
        # Commit único
        # ------------------------------------------------------

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            (
                "[SECURITY][EMPRESA_PROFILE] "
                "Falha ao atualizar perfil da malharia. "
                f"empresa_id={empresa.id}"
            )
        )

        flash(
            (
                "Não foi possível salvar as alterações agora. "
                "Tente novamente."
            ),
            "danger"
        )

        try:

            cidades = (
                lista_cidades_por_uf(
                    estado
                )
                if estado
                else []
            )

        except Exception:

            cidades = []

        return render_template(
            "editar_empresa.html",
            erro=(
                "Não foi possível salvar as alterações."
            ),
            erros={},
            estados=estados,
            nome=nome,
            apelido=apelido,
            email=email,
            cidade=cidade,
            estado=estado,
            telefone=telefone,
            responsavel_nome=responsavel_nome,
            responsavel_sobrenome=responsavel_sobrenome,
            endereco=endereco_full,
            cep=cep_raw,
            cidades=cidades
        )

    # ==========================================================
    # ATUALIZA SESSÃO
    # ==========================================================

    session["empresa_id"] = (
        empresa.id
    )

    session["empresa_apelido"] = (
        empresa.apelido
        or empresa.nome
        or empresa.email.split("@")[0]
    )

    session["empresa_nome"] = (
        empresa.nome
    )

    session["login_email"] = (
        empresa.email
    )

    session["auth_email"] = (
        empresa.email
    )

    if getattr(
        empresa,
        "user_id",
        None
    ):

        session["user_id"] = (
            empresa.user_id
        )

        session["auth_user_id"] = (
            empresa.user_id
        )

    session.modified = True

    # ==========================================================
    # LOG SEGURO
    # ==============================================================

    current_app.logger.info(
        (
            "[SECURITY][EMPRESA_PROFILE] "
            f"empresa_id={empresa.id} "
            f"email_changed={email_alterado} "
            f"password_changed={senha_alterada}"
        )
    )

    flash(
        "Dados da empresa atualizados com sucesso.",
        "success"
    )

    return redirect(
        url_for(
            "editar_empresa",
            ok=1
        )
    )

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

# ==============================================================
# ADMIN LOGIN — GET
# ==============================================================
#
# Apenas exibe a tela de autenticação administrativa.
#
# Esta rota NÃO recebe rate limit porque abrir ou atualizar
# a página não representa tentativa de autenticação.
# ==============================================================

@app.get(
    "/admin/login",
    endpoint="admin_login"
)
def admin_login():
    """
    Exibe a página de login administrativo.

    A validação das credenciais acontece separadamente
    no POST /admin/login.
    """

    # ==========================================================
    # CONFIGURAÇÃO
    # ==========================================================

    if (
        not ADMIN_EMAIL
        or not ADMIN_PASSWORD
    ):

        current_app.logger.error(
            "[ADMIN] Credenciais administrativas "
            "não configuradas."
        )

        return (
            "Administração indisponível.",
            503
        )

    return render_template(
        "admin_login.html"
    )


# ==============================================================
# ADMIN LOGIN — POST
# ==============================================================
#
# Apenas tentativas reais de autenticação passam por esta rota.
#
# Limite:
# 5 tentativas a cada 15 minutos por cliente/IP.
#
# Ao exceder:
# Flask-Limiter
#     ↓
# HTTP 429
#     ↓
# handle_rate_limit_exceeded()
#     ↓
# página amigável do AcheTece.
# ==============================================================

@app.post(
    "/admin/login",
    endpoint="post_admin_login"
)
@limiter.limit("5 per 15 minutes")
def post_admin_login():
    """
    Processa a autenticação administrativa.

    Segurança:
    - CSRF global permanece ativo;
    - máximo de 5 tentativas em 15 minutos por IP;
    - sessão anterior é removida antes de criar
      a sessão administrativa;
    - excesso de tentativas gera HTTP 429.
    """

    # ==========================================================
    # CONFIGURAÇÃO
    # ==========================================================

    if (
        not ADMIN_EMAIL
        or not ADMIN_PASSWORD
    ):

        current_app.logger.error(
            "[ADMIN] Credenciais administrativas "
            "não configuradas."
        )

        return (
            "Administração indisponível.",
            503
        )

    # ==========================================================
    # DADOS RECEBIDOS
    # ==========================================================

    email = (
        request.form.get(
            "email"
        )
        or ""
    ).strip().lower()

    senha = (
        request.form.get(
            "senha"
        )
        or ""
    )

    # ==========================================================
    # CREDENCIAIS VÁLIDAS
    # ==========================================================

    if (
        email == ADMIN_EMAIL
        and senha == ADMIN_PASSWORD
    ):

        session.clear()

        session[
            "admin_authenticated"
        ] = True

        session[
            "admin_email"
        ] = ADMIN_EMAIL

        session.permanent = True

        flash(
            "Login de administrador realizado.",
            "success"
        )

        return redirect(
            url_for(
                "admin_empresas"
            )
        )

    # ==========================================================
    # CREDENCIAIS INVÁLIDAS
    # ==========================================================

    current_app.logger.warning(
        "[ADMIN] Tentativa de login "
        "administrativo inválida. "
        "ip=%s",
        _client_ip(),
    )

    flash(
        "E-mail ou senha incorretos.",
        "error"
    )

    return redirect(
        url_for(
            "admin_login"
        )
    )

@app.post(
    "/admin/logout"
)
@login_admin_requerido
def admin_logout():

    session.clear()

    flash(
        "Você saiu do painel administrativo.",
        "success"
    )

    return redirect(
        url_for("index")
    )

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

# ==============================================================
# ADMIN — ALTERAR STATUS DA MALHARIA
# ==============================================================

@app.post(
    "/admin/editar_status/<int:empresa_id>",
    endpoint="admin_editar_status"
)
@login_admin_requerido
@limiter.limit(
    "30 per hour",
    key_func=_admin_rate_limit_key
)
@limiter.limit(
    "100 per day",
    key_func=_admin_rate_limit_key
)
def admin_editar_status(
    empresa_id
):
    """
    Alterna o status de pagamento da malharia.

    Segurança:
    - somente POST;
    - CSRF global;
    - exige sessão administrativa;
    - Rate Limit por administrador;
    - o novo status não é confiado ao navegador;
    - alteração feita server-side;
    - transação com rollback;
    - log administrativo sem dados sensíveis.
    """

    # ==========================================================
    # EMPRESA
    # ==========================================================

    empresa = db.session.get(
        Empresa,
        empresa_id
    )

    if empresa is None:

        abort(
            404
        )

    # ==========================================================
    # STATUS ATUAL
    # ==========================================================

    status_anterior = (
        empresa.status_pagamento
        or ""
    ).strip().lower()

    # Considera os equivalentes já utilizados pelo AcheTece.
    status_ativo_atual = (
        status_anterior
        in STATUS_ATIVO_EQUIV
    )

    # ==========================================================
    # NOVO STATUS
    #
    # Não utilizamos request.form["status"] para decidir
    # o novo status da empresa.
    #
    # O campo "status" existente no painel administrativo
    # também é utilizado como filtro da listagem.
    # ==============================================================

    if status_ativo_atual:

        novo_status = (
            "pendente"
        )

        nova_data_pagamento = (
            None
        )

    else:

        novo_status = (
            "ativo"
        )

        nova_data_pagamento = (
            datetime.utcnow()
        )

    # ==========================================================
    # TRANSAÇÃO
    # ==============================================================

    try:

        empresa.status_pagamento = (
            novo_status
        )

        empresa.data_pagamento = (
            nova_data_pagamento
        )

        db.session.commit()

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            (
                "[SECURITY][ADMIN_STATUS] "
                "Falha ao alterar status. "
                f"empresa_id={empresa_id}"
            )
        )

        flash(
            (
                "Não foi possível alterar o status "
                "da empresa agora."
            ),
            "danger"
        )

        return redirect(
            url_for(
                "admin_empresas"
            )
        )

    # ==========================================================
    # LOG SEGURO
    # ==============================================================

    current_app.logger.info(
        (
            "[SECURITY][ADMIN_STATUS] "
            f"empresa_id={empresa.id} "
            f"status_anterior={status_anterior or 'vazio'} "
            f"status_novo={novo_status}"
        )
    )

    # ==========================================================
    # MENSAGEM
    # ==============================================================

    nome_exibicao = (
        empresa.apelido
        or empresa.nome
        or f"Empresa {empresa.id}"
    )

    flash(
        (
            f'Status de "{nome_exibicao}" '
            f"atualizado para {novo_status}."
        ),
        "success"
    )

    # ==========================================================
    # PRESERVA OS FILTROS DA TELA ADMINISTRATIVA
    # ==============================================================

    params = {
        "pagina": (
            request.args.get(
                "pagina",
                1
            )
        ),

        "status": (
            request.args.get(
                "status",
                ""
            )
        ),

        "data_inicio": (
            request.args.get(
                "data_inicio",
                ""
            )
        ),

        "data_fim": (
            request.args.get(
                "data_fim",
                ""
            )
        ),

        "plano": (
            request.args.get(
                "plano",
                ""
            )
        ),

        "apelido": (
            request.args.get(
                "apelido",
                ""
            )
        ),

        "cidade": (
            request.args.get(
                "cidade",
                ""
            )
        ),

        "estado": (
            request.args.get(
                "estado",
                ""
            )
        ),
    }

    # Remove parâmetros vazios.
    params = {
        chave: valor
        for chave, valor
        in params.items()
        if valor not in {
            None,
            ""
        }
    }

    return redirect(
        url_for(
            "admin_empresas",
            **params
        )
    )

# ==============================================================
# ADMIN — EXCLUSÃO DEFINITIVA DE MALHARIA
# ==============================================================

@app.post(
    "/admin/empresas/<int:empresa_id>/excluir",
    endpoint="admin_excluir_empresa"
)
@app.post(
    "/admin/empresa_excluir/<int:empresa_id>",
    endpoint="empresa_excluir"
)
@login_admin_requerido
@limiter.limit(
    "5 per 15 minutes",
    key_func=_admin_rate_limit_key
)
@limiter.limit(
    "20 per day",
    key_func=_admin_rate_limit_key
)
def admin_excluir_empresa(
    empresa_id
):
    """
    Exclusão administrativa definitiva.

    Regras:
    - somente POST;
    - CSRF global;
    - administrador autenticado;
    - Rate Limit;
    - bloqueia empresas com histórico no marketplace;
    - exclusão transacional;
    - preserva Usuario quando possuir histórico como comprador;
    - remove arquivos somente após commit.
    """

    empresa = db.session.get(
        Empresa,
        empresa_id
    )

    if empresa is None:

        abort(
            404
        )

    nome_empresa = (
        empresa.apelido
        or empresa.nome
        or f"Empresa {empresa.id}"
    )

    # ==========================================================
    # FILTROS PARA RETORNO AO PAINEL
    # ==========================================================

    params = {
        "pagina":
            request.args.get(
                "pagina",
                1
            ),

        "status":
            request.args.get(
                "status",
                ""
            ),

        "data_inicio":
            request.args.get(
                "data_inicio",
                ""
            ),

        "data_fim":
            request.args.get(
                "data_fim",
                ""
            ),

        "plano":
            request.args.get(
                "plano",
                ""
            ),

        "apelido":
            request.args.get(
                "apelido",
                ""
            ),

        "cidade":
            request.args.get(
                "cidade",
                ""
            ),

        "estado":
            request.args.get(
                "estado",
                ""
            ),
    }

    params = {
        chave: valor

        for chave, valor
        in params.items()

        if valor not in {
            None,
            ""
        }
    }

    # ==========================================================
    # HISTÓRICO
    # ==========================================================

    historico = (
        _empresa_historico_marketplace(
            empresa.id
        )
    )

    if historico[
        "bloqueia_exclusao"
    ]:

        current_app.logger.warning(
            (
                "[SECURITY][ADMIN_DELETE_BLOCKED] "
                f"empresa_id={empresa.id} "
                f"matches={historico['matches']} "
                f"oportunidades={historico['oportunidades']} "
                f"propostas={historico['propostas']} "
                f"pedidos={historico['pedidos']}"
            )
        )

        flash(
            (
                f'A malharia "{nome_empresa}" possui histórico '
                "no marketplace e não pode ser excluída "
                "definitivamente. "
                "Utilize a alteração de status para impedir "
                "novas operações."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "admin_empresas",
                **params
            )
        )

    # ==========================================================
    # EXCLUSÃO
    # ==========================================================

    try:

        resultado = (
            _excluir_empresa_sem_historico(
                empresa
            )
        )

    except RuntimeError as exc:

        if str(
            exc
        ) == "EMPRESA_POSSUI_HISTORICO":

            flash(
                (
                    "A empresa passou a possuir histórico "
                    "e a exclusão foi cancelada."
                ),
                "warning"
            )

            return redirect(
                url_for(
                    "admin_empresas",
                    **params
                )
            )

        current_app.logger.exception(
            (
                "[SECURITY][ADMIN_DELETE] "
                "Falha na exclusão. "
                f"empresa_id={empresa_id}"
            )
        )

        flash(
            "Não foi possível excluir a empresa.",
            "danger"
        )

        return redirect(
            url_for(
                "admin_empresas",
                **params
            )
        )

    except Exception:

        current_app.logger.exception(
            (
                "[SECURITY][ADMIN_DELETE] "
                "Falha na exclusão. "
                f"empresa_id={empresa_id}"
            )
        )

        flash(
            (
                "Não foi possível excluir a empresa agora. "
                "Nenhuma exclusão parcial foi mantida."
            ),
            "danger"
        )

        return redirect(
            url_for(
                "admin_empresas",
                **params
            )
        )

    # ==========================================================
    # LOG
    # ==========================================================

    current_app.logger.info(
        (
            "[SECURITY][ADMIN_DELETE] "
            f"empresa_id={empresa_id} "
            "resultado=excluida "
            f"usuario_removido="
            f"{resultado.get('usuario_removido')} "
            f"usuario_preservado="
            f"{resultado.get('usuario_preservado')}"
        )
    )

    flash(
        (
            f'Empresa "{nome_empresa}" '
            "excluída definitivamente com sucesso."
        ),
        "success"
    )

    return redirect(
        url_for(
            "admin_empresas",
            **params
        )
    )

# ==============================================================
# MALHARIA — EXCLUIR A PRÓPRIA CONTA
# ==============================================================

@app.post(
    "/empresa/<int:empresa_id>/excluir",
    endpoint="empresa_excluir_by_id"
)
@limiter.limit(
    "3 per day",
    key_func=_malharia_rate_limit_key
)
def empresa_excluir_by_id(
    empresa_id
):
    """
    Exclusão solicitada pela própria malharia.

    A empresa somente pode apagar definitivamente sua conta
    quando ainda não possui histórico dentro do marketplace.

    Contas com histórico precisam utilizar posteriormente
    um fluxo de encerramento/desativação, preservando os
    registros comerciais.
    """

    empresa = (
        _pegar_empresa_do_usuario(
            required=True
        )
    )

    if not isinstance(
        empresa,
        Empresa
    ):

        return empresa

    if empresa.id != empresa_id:

        abort(
            403
        )

    # ==========================================================
    # HISTÓRICO
    # ==========================================================

    historico = (
        _empresa_historico_marketplace(
            empresa.id
        )
    )

    if historico[
        "bloqueia_exclusao"
    ]:

        current_app.logger.warning(
            (
                "[SECURITY][SELF_DELETE_BLOCKED] "
                f"empresa_id={empresa.id}"
            )
        )

        flash(
            (
                "Sua conta possui histórico de operações "
                "no AcheTece e não pode ser apagada "
                "definitivamente por este fluxo. "
                "Entre em contato com o suporte para "
                "encerramento da conta."
            ),
            "warning"
        )

        return redirect(
            url_for(
                "painel_malharia"
            )
        )

    # ==========================================================
    # EXCLUSÃO
    # ==========================================================

    try:

        _excluir_empresa_sem_historico(
            empresa
        )

    except Exception:

        current_app.logger.exception(
            (
                "[SECURITY][SELF_DELETE] "
                "Falha ao excluir conta. "
                f"empresa_id={empresa_id}"
            )
        )

        flash(
            (
                "Não foi possível excluir sua conta agora. "
                "Tente novamente."
            ),
            "danger"
        )

        return redirect(
            url_for(
                "painel_malharia"
            )
        )

    session.clear()

    flash(
        "Conta da malharia excluída com sucesso.",
        "success"
    )

    return redirect(
        url_for(
            "index"
        )
    )

# --------------------------------------------------------------------
# Admin: ferramentas de staging / desenvolvimento
# --------------------------------------------------------------------

DEMO_FILTER = or_(
    Empresa.apelido.ilike("%[DEMO]%"),
    Empresa.email.ilike("%@achetece.demo")
)


def admin_tool_requerido(f):

    @wraps(f)
    def wrapper(
        *args,
        **kwargs
    ):

        # ==========================================================
        # FERRAMENTAS ADMINISTRATIVAS HABILITADAS?
        # ==========================================================

        if not ENABLE_ADMIN_TOOLS:

            abort(404)

        # ==========================================================
        # ADMIN AUTENTICADO?
        # ==========================================================

        admin_autenticado = (
            session.get(
                "admin_authenticated"
            )
            is True
        )

        admin_email = (
            session.get(
                "admin_email"
            )
            or ""
        ).strip().lower()

        if (
            not admin_autenticado
            or not ADMIN_EMAIL
            or admin_email != ADMIN_EMAIL
        ):

            flash(
                "Acesso administrativo necessário.",
                "warning"
            )

            return redirect(
                url_for(
                    "admin_login"
                )
            )

        return f(
            *args,
            **kwargs
        )

    return wrapper


# --------------------------------------------------------------------
# Helpers para geração de teares fictícios
# --------------------------------------------------------------------

def _cria_teares_fake(
    empresa,
    n
):

    tipos = [
        "MONO",
        "DUPLA"
    ]

    marcas = [
        "Mayer",
        "Terrot",
        "Santoni",
        "Pilotelli",
        "Unitex"
    ]

    modelos = [
        "Relanit",
        "Inovit",
        "DEMO-01",
        "DEMO-02",
        "DEMO-03"
    ]

    diametros = [
        18,
        20,
        22,
        24,
        26,
        28,
        30,
        32,
        34,
        36
    ]

    galgas = [
        14,
        18,
        20,
        22,
        24,
        26,
        28,
        30,
        32
    ]

    alimentadores_pool = [
        36,
        48,
        60,
        72,
        84,
        90,
        96,
        108
    ]

    novos = []

    for _ in range(
        max(
            0,
            int(
                n
                or 0
            )
        )
    ):

        tear = Tear(
            marca=random.choice(
                marcas
            ),
            modelo=random.choice(
                modelos
            ),
            tipo=random.choice(
                tipos
            ),
            finura=random.choice(
                galgas
            ),
            diametro=random.choice(
                diametros
            ),
            alimentadores=random.choice(
                alimentadores_pool
            ),
            elastano=random.choice(
                [
                    "Sim",
                    "Não"
                ]
            ),
            empresa_id=empresa.id
        )

        novos.append(
            tear
        )

    if novos:

        db.session.bulk_save_objects(
            novos
        )

        db.session.commit()

    return len(
        novos
    )


def _topup(
    empresa,
    minimo
):

    atual = (
        Tear.query
        .filter_by(
            empresa_id=empresa.id
        )
        .count()
    )

    if atual >= (
        minimo
        or 0
    ):

        return 0

    return _cria_teares_fake(
        empresa,
        minimo - atual
    )


# --------------------------------------------------------------------
# ADMIN — Seed de uma empresa
#
# SOMENTE POST.
# Protegido por:
# - ENABLE_ADMIN_TOOLS
# - sessão administrativa
# - CSRF global na etapa seguinte
# --------------------------------------------------------------------

@app.post(
    "/admin/seed_teares"
)
@admin_tool_requerido
def admin_seed_teares():

    empresa_id = request.values.get(
        "empresa_id",
        type=int
    )

    n = request.values.get(
        "n",
        default=5,
        type=int
    )

    if not empresa_id:

        return (
            "Informe empresa_id",
            400
        )

    empresa = (
        Empresa.query
        .get_or_404(
            empresa_id
        )
    )

    quantidade = (
        _cria_teares_fake(
            empresa,
            n
        )
    )

    nome_empresa = (
        empresa.apelido
        or empresa.nome
        or getattr(
            empresa,
            "nome_fantasia",
            None
        )
        or str(
            empresa.id
        )
    )

    return (
        f"OK: +{quantidade} teares "
        f"em {nome_empresa} "
        f"(id={empresa.id})."
    )


# --------------------------------------------------------------------
# ADMIN — Seed em múltiplas empresas
# --------------------------------------------------------------------

@app.post(
    "/admin/seed_teares_all"
)
@admin_tool_requerido
def admin_seed_teares_all():

    escopo = (
        request.values.get(
            "escopo"
        )
        or "demo"
    ).strip().lower()

    uf = (
        request.values.get(
            "uf"
        )
        or ""
    ).strip()

    ids = (
        request.values.get(
            "ids"
        )
        or ""
    ).strip()

    n = request.values.get(
        "n",
        type=int
    )

    minimo = request.values.get(
        "min",
        type=int
    )

    query = Empresa.query

    # ==========================================================
    # IDs ESPECÍFICOS
    # ==========================================================

    if ids:

        lista = [
            int(x)
            for x in ids.split(",")
            if x.strip().isdigit()
        ]

        if not lista:

            return (
                "Nenhum ID válido informado.",
                400
            )

        query = query.filter(
            Empresa.id.in_(
                lista
            )
        )

    # ==========================================================
    # ESCOPO
    # ==========================================================

    else:

        if escopo == "demo":

            query = query.filter(
                DEMO_FILTER
            )

        elif escopo == "pagantes":

            query = query.filter(
                Empresa.status_pagamento
                == "ativo"
            )

        elif escopo != "todas":

            return (
                "Escopo inválido.",
                400
            )

    # ==========================================================
    # UF
    # ==========================================================

    if uf:

        query = query.filter(
            func.upper(
                Empresa.estado
            )
            == uf.upper()
        )

    empresas = (
        query
        .order_by(
            Empresa.id.desc()
        )
        .all()
    )

    if not empresas:

        return (
            "Nenhuma empresa encontrada para o filtro.",
            200
        )

    total_empresas = len(
        empresas
    )

    total_adicionados = 0

    relatorio = []

    for empresa in empresas:

        if minimo:

            adicionados = _topup(
                empresa,
                minimo
            )

        else:

            adicionados = (
                _cria_teares_fake(
                    empresa,
                    n or 5
                )
            )

        total_adicionados += (
            adicionados
        )

        relatorio.append(
            f"{empresa.id}:{adicionados}"
        )

    return (
        f"OK: {total_adicionados} teares "
        f"adicionados em {total_empresas} empresas. "
        f"Detalhe: {'; '.join(relatorio)}"
    )


# --------------------------------------------------------------------
# ADMIN — Consulta auxiliar de empresas
#
# Esta rota NÃO altera dados, portanto continua GET.
# --------------------------------------------------------------------

@app.get(
    "/utils/empresas_json"
)
@admin_tool_requerido
def utils_empresas_json():

    escopo = (
        request.args.get(
            "escopo"
        )
        or "demo"
    ).strip().lower()

    uf = (
        request.args.get(
            "uf"
        )
        or ""
    ).strip()

    query = Empresa.query

    if escopo == "demo":

        query = query.filter(
            DEMO_FILTER
        )

    elif escopo == "pagantes":

        query = query.filter(
            Empresa.status_pagamento
            == "ativo"
        )

    elif escopo != "todas":

        return jsonify(
            {
                "erro":
                    "Escopo inválido."
            }
        ), 400

    if uf:

        query = query.filter(
            func.upper(
                Empresa.estado
            )
            == uf.upper()
        )

    empresas = (
        query
        .order_by(
            Empresa.id.desc()
        )
        .all()
    )

    data = []

    for empresa in empresas:

        quantidade_teares = (
            Tear.query
            .filter_by(
                empresa_id=empresa.id
            )
            .count()
        )

        data.append(
            {
                "id":
                    empresa.id,

                "apelido":
                    (
                        empresa.apelido
                        or empresa.nome
                        or getattr(
                            empresa,
                            "nome_fantasia",
                            ""
                        )
                        or ""
                    ),

                "estado":
                    empresa.estado,

                "cidade":
                    empresa.cidade,

                "status_pagamento":
                    getattr(
                        empresa,
                        "status_pagamento",
                        None
                    ),

                "teares":
                    quantidade_teares
            }
        )

    return jsonify(
        data
    )


# --------------------------------------------------------------------
# ADMIN — Impersonação
# --------------------------------------------------------------------

@app.post(
    "/admin/impersonar/<int:empresa_id>"
)
@admin_tool_requerido
def admin_impersonar(
    empresa_id
):

    empresa = (
        Empresa.query
        .get_or_404(
            empresa_id
        )
    )

    session[
        "admin_impersonando"
    ] = True

    session[
        "perfil"
    ] = "malharia"

    session[
        "empresa_id"
    ] = empresa.id

    session.modified = True

    return redirect(
        url_for(
            "painel_malharia"
        )
    )


# --------------------------------------------------------------------
# ADMIN — Encerrar impersonação
# --------------------------------------------------------------------

@app.post(
    "/admin/desimpersonar"
)
@admin_tool_requerido
def admin_desimpersonar():

    session.pop(
        "admin_impersonando",
        None
    )

    session.pop(
        "perfil",
        None
    )

    session.pop(
        "empresa_id",
        None
    )

    session.modified = True

    return redirect(
        url_for(
            "admin_empresas"
        )
    )


# --------------------------------------------------------------------
# ADMIN — Teste manual de e-mail
# --------------------------------------------------------------------

@app.post(
    "/admin/test-email"
)
@admin_tool_requerido
def admin_test_email():

    to_addr = (
        request.values.get(
            "to"
        )
        or os.getenv(
            "CONTACT_TO"
        )
        or os.getenv(
            "EMAIL_FROM"
        )
        or os.getenv(
            "SMTP_FROM"
        )
        or ""
    ).strip()

    if not to_addr:

        return (
            "Informe o destinatário no campo 'to'.",
            400
        )

    html = (
        "<h3>Teste de e-mail AcheTece</h3>"
        "<p>Se você recebeu isto, "
        "o envio está funcionando.</p>"
    )

    ok, msg = _smtp_send_direct(
        to_addr,
        "Teste AcheTece",
        html,
        "Teste AcheTece"
    )

    if ok:

        return (
            f"OK: {msg}",
            200
        )

    return (
        f"ERRO: {msg}",
        500
    )

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

# ==============================================================
# MERCADO PAGO — ALIAS LEGADO /pagar
# ==============================================================

@app.get(
    "/pagar",
    endpoint="pagar"
)
def pagar():
    """
    Alias legado do início do pagamento.

    Não cria preferência diretamente.
    Redireciona para a rota canônica /checkout,
    preservando somente o plano reconhecido.

    A autenticação, validação e Rate Limiting são
    executados posteriormente pela própria /checkout.
    """

    plano = (
        request.args.get("plano")
        or "mensal"
    ).strip().lower()

    if plano not in {
        "mensal",
        "anual",
    }:

        plano = "mensal"

    return redirect(
        url_for(
            "checkout",
            plano=plano
        ),
        code=302
    )

# --- Checkout Mercado Pago ---------------------------------------------------
def _mp_sdk():
    token = os.environ.get("MP_ACCESS_TOKEN", "")
    if not token:
        raise RuntimeError("MP_ACCESS_TOKEN não definido.")
    return mercadopago.SDK(token)

# ==============================================================
# MERCADO PAGO — VALIDAÇÃO DA ASSINATURA DO WEBHOOK
# ==============================================================

def _validar_assinatura_webhook_mp(req):
    """
    Valida a assinatura HMAC-SHA256 enviada pelo Mercado Pago.

    Para Webhooks de pagamento do AcheTece exigimos:
    - MP_WEBHOOK_SECRET configurado;
    - x-signature;
    - x-request-id;
    - data.id na query string;
    - ts e v1 dentro de x-signature.

    Manifest:
        id:<data.id>;
        request-id:<x-request-id>;
        ts:<ts>;

    O segredo e a assinatura nunca são escritos nos logs.
    """

    # ==========================================================
    # SEGREDO
    # ==========================================================

    secret = (
        os.getenv("MP_WEBHOOK_SECRET")
        or ""
    ).strip()

    if not secret:

        current_app.logger.error(
            "[SECURITY][MP_WEBHOOK] "
            "MP_WEBHOOK_SECRET não configurado."
        )

        return (
            False,
            "secret_missing"
        )

    # ==========================================================
    # HEADERS
    # ==========================================================

    x_signature = (
        req.headers.get("x-signature")
        or ""
    ).strip()

    x_request_id = (
        req.headers.get("x-request-id")
        or ""
    ).strip()

    # ==========================================================
    # DATA.ID ASSINADO
    #
    # Para nosso Webhook de pagamentos, o ID utilizado na
    # assinatura deve ser obrigatoriamente o recebido na query:
    #
    # ?data.id=123456&type=payment
    # ==========================================================

    data_id = (
        req.args.get("data.id")
        or ""
    ).strip()

    if not x_signature:

        return (
            False,
            "signature_missing"
        )

    if not x_request_id:

        return (
            False,
            "request_id_missing"
        )

    if not data_id:

        return (
            False,
            "data_id_missing"
        )

    # ==========================================================
    # EXTRAI ts E v1
    #
    # Exemplo:
    # ts=1742505638683,v1=abcdef...
    # ==========================================================

    ts = ""
    v1 = ""

    for part in x_signature.split(","):

        key_value = part.split(
            "=",
            1
        )

        if len(key_value) != 2:
            continue

        key = (
            key_value[0]
            .strip()
            .lower()
        )

        value = (
            key_value[1]
            .strip()
        )

        if key == "ts":
            ts = value

        elif key == "v1":
            v1 = value

    if not ts or not v1:

        return (
            False,
            "signature_invalid"
        )

    # Timestamp deve ser numérico.
    if not ts.isdigit():

        return (
            False,
            "timestamp_invalid"
        )

    # Normalização recomendada para identificadores.
    data_id = data_id.lower()

    # ==========================================================
    # MANIFEST
    # ==========================================================

    manifest = (
        f"id:{data_id};"
        f"request-id:{x_request_id};"
        f"ts:{ts};"
    )

    # ==========================================================
    # HMAC-SHA256
    # ==========================================================

    expected_signature = hmac.new(
        secret.encode("utf-8"),
        manifest.encode("utf-8"),
        hashlib.sha256
    ).hexdigest()

    # ==========================================================
    # COMPARAÇÃO EM TEMPO CONSTANTE
    # ==========================================================

    assinatura_valida = hmac.compare_digest(
        expected_signature,
        v1
    )

    if not assinatura_valida:

        return (
            False,
            "signature_mismatch"
        )

    return (
        True,
        None
    )

def _extract_payment_id(req):
    """
    Extrai o payment_id de forma vinculada ao valor utilizado
    na assinatura do Mercado Pago.

    Segurança:
    - prioriza obrigatoriamente request.args["data.id"];
    - compara com payload["data"]["id"], quando presente;
    - rejeita divergência entre query e body;
    - nunca processa um ID diferente daquele coberto pela
      assinatura HMAC.
    """

    payload = (
        req.get_json(
            silent=True
        )
        or {}
    )

    # ==========================================================
    # ID ASSINADO — QUERY STRING
    # ==========================================================

    query_payment_id = (
        req.args.get("data.id")
        or ""
    ).strip()

    if not query_payment_id:

        return (
            None,
            payload
        )

    # ==========================================================
    # ID PRESENTE NO BODY — CONFERÊNCIA
    # ==========================================================

    body_payment_id = ""

    if isinstance(
        payload,
        dict
    ):

        data = (
            payload.get("data")
            or {}
        )

        if isinstance(
            data,
            dict
        ):

            body_payment_id = str(
                data.get("id")
                or ""
            ).strip()

    # Se o body também informar ID, ele deve ser exatamente
    # o mesmo da query assinada.
    if (
        body_payment_id
        and body_payment_id != query_payment_id
    ):

        current_app.logger.warning(
            "[SECURITY][MP_WEBHOOK] "
            "Divergência entre data.id da query e do body."
        )

        return (
            None,
            payload
        )

    return (
        query_payment_id,
        payload
    )

# ==============================================================
# MERCADO PAGO — CONSULTA DE PAGAMENTO
# ==============================================================

def _mp_get_payment(
    payment_id: str
):
    """
    Consulta um pagamento diretamente na API do Mercado Pago.

    O SDK mercadopago==2.3.0 retorna um envelope semelhante a:

        {
            "status": 200,
            "response": {
                "id": ...,
                "status": "approved",
                ...
            }
        }

    Esta função:
    - valida o payment_id;
    - consulta o SDK;
    - verifica o status HTTP retornado pelo SDK;
    - devolve somente o objeto real do pagamento;
    - nunca entrega o envelope HTTP para _processar_pagamento().
    """

    # ==========================================================
    # PAYMENT ID
    # ==========================================================

    payment_id = (
        str(
            payment_id
            or ""
        )
        .strip()
    )

    if (
        not payment_id
        or not payment_id.isdigit()
    ):

        raise ValueError(
            "payment_id inválido."
        )

    # ==========================================================
    # SDK
    # ==========================================================

    sdk = _mp_sdk()

    resultado = sdk.payment().get(
        payment_id
    )

    # ==========================================================
    # ESTRUTURA DA RESPOSTA
    # ==========================================================

    if not isinstance(
        resultado,
        dict
    ):

        raise RuntimeError(
            "Resposta inválida do SDK Mercado Pago."
        )

    # O campo status do envelope é o HTTP status:
    # 200, 400, 404 etc.
    http_status = resultado.get(
        "status"
    )

    try:

        http_status = int(
            http_status
        )

    except Exception:

        http_status = 0

    # ==========================================================
    # ERRO DA API
    # ==========================================================

    if (
        http_status < 200
        or http_status >= 300
    ):

        current_app.logger.warning(
            (
                "[MP] Consulta de pagamento recusada "
                f"payment_id={payment_id} "
                f"http_status={http_status}"
            )
        )

        raise RuntimeError(
            (
                "Mercado Pago não retornou "
                "um pagamento válido."
            )
        )

    # ==========================================================
    # OBJETO REAL DO PAGAMENTO
    # ==========================================================

    payment = resultado.get(
        "response"
    )

    if not isinstance(
        payment,
        dict
    ):

        raise RuntimeError(
            "Objeto de pagamento ausente na resposta do Mercado Pago."
        )

    return payment

def _parse_empresa_id_from_external_reference(
    ext_ref: str
):
    """
    Extrai Empresa.id de um external_reference criado
    exclusivamente pelo AcheTece.

    Formato obrigatório:

        achetece:<empresa_id>:<uuid_hex>

    Exemplo:

        achetece:4:d73f2b88132e4aecba8ca6f74589ef19
    """

    ext_ref = (
        ext_ref
        or ""
    ).strip()

    if not ext_ref:

        return None

    parts = ext_ref.split(":")

    # Formato exato:
    # achetece : empresa_id : uuid_hex
    if len(parts) != 3:

        return None

    if parts[0] != "achetece":

        return None

    # ==========================================================
    # EMPRESA.ID
    # ==========================================================

    try:

        empresa_id = int(
            parts[1]
        )

    except Exception:

        return None

    if empresa_id <= 0:

        return None

    # ==========================================================
    # NONCE UUID HEX
    #
    # uuid.uuid4().hex gera exatamente 32 caracteres hex.
    # ==========================================================

    nonce = (
        parts[2]
        or ""
    ).strip()

    if len(nonce) != 32:

        return None

    caracteres_hex = (
        "0123456789"
        "abcdef"
        "ABCDEF"
    )

    if any(
        caractere not in caracteres_hex
        for caractere in nonce
    ):

        return None

    return empresa_id

def _send_email(to_email: str, subject: str, text_body: str, html_body: str):
    # Aceita tanto MAIL_* quanto SMTP_*
    host = os.environ.get("MAIL_HOST") or os.environ.get("SMTP_HOST")
    port = int(os.environ.get("MAIL_PORT") or os.environ.get("SMTP_PORT") or "587")
    user = os.environ.get("MAIL_USER") or os.environ.get("SMTP_USER")
    pwd  = os.environ.get("MAIL_PASS") or os.environ.get("SMTP_PASS")

    mail_from = os.environ.get("MAIL_FROM") or user
    from_name = os.environ.get("MAIL_FROM_NAME", "AcheTece")

    reply_to = (
        os.environ.get("MAIL_REPLY_TO")
        or ADMIN_EMAIL
        or mail_from
    )

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

def _processar_pagamento(
    payment_id: str
):
    """
    Consulta o pagamento diretamente no Mercado Pago e atualiza
    a malharia vinculada pelo external_reference do AcheTece.

    Segurança:
    - nunca confia no status recebido pelo webhook;
    - consulta novamente a API do Mercado Pago;
    - exige external_reference válido;
    - não associa pagamentos por e-mail do pagador;
    - processamento de aprovação é idempotente;
    - webhook repetido não altera novamente data_pagamento;
    - webhook repetido não dispara novo e-mail.
    """

    # ==========================================================
    # PAYMENT ID
    # ==========================================================

    payment_id = (
        str(
            payment_id
            or ""
        )
        .strip()
    )

    if (
        not payment_id
        or not payment_id.isdigit()
    ):

        raise ValueError(
            "payment_id inválido."
        )

    # ==========================================================
    # CONSULTA O MERCADO PAGO
    #
    # Não confia no status recebido no webhook.
    # ==========================================================

    payment = _mp_get_payment(
        payment_id
    )

    if not isinstance(
        payment,
        dict
    ):

        raise RuntimeError(
            "Resposta inválida do Mercado Pago."
        )

    status = (
        payment.get("status")
        or ""
    ).strip().lower()

    detail = (
        payment.get("status_detail")
        or ""
    ).strip().lower()

    ext_ref = (
        payment.get(
            "external_reference"
        )
        or ""
    ).strip()

    current_app.logger.info(
        (
            "[MP] "
            f"payment_id={payment_id} "
            f"status={status} "
            f"detail={detail}"
        )
    )

    # ==========================================================
    # EXTERNAL REFERENCE
    # ==========================================================

    empresa_id = (
        _parse_empresa_id_from_external_reference(
            ext_ref
        )
    )

    if not empresa_id:

        raise RuntimeError(
            "Pagamento sem external_reference válido do AcheTece."
        )

    empresa = db.session.get(
        Empresa,
        empresa_id
    )

    if empresa is None:

        raise RuntimeError(
            "Empresa vinculada ao pagamento não encontrada."
        )

    # ==========================================================
    # STATUS ATUAL
    # ==========================================================

    status_atual = (
        empresa.status_pagamento
        or ""
    ).strip().lower()

    # ==========================================================
    # PAGAMENTO APROVADO
    # ==========================================================

    if status == "approved":

        # ------------------------------------------------------
        # JÁ ESTAVA ATIVO
        #
        # Webhooks podem ser repetidos.
        # Não atualizamos data_pagamento nem reenviamos e-mail.
        # ------------------------------------------------------

        if status_atual == "ativo":

            return {
                "ok": True,
                "empresa_id": empresa.id,
                "ativou": False,
                "status": "approved",
                "idempotente": True,
            }

        # ------------------------------------------------------
        # TRANSIÇÃO PARA ATIVO
        # ------------------------------------------------------

        empresa.status_pagamento = (
            "ativo"
        )

        empresa.data_pagamento = (
            datetime.utcnow()
        )

        db.session.commit()

        # ------------------------------------------------------
        # E-MAIL
        #
        # A ativação já foi confirmada no banco.
        # Falha no e-mail não deve desfazer o pagamento.
        # ------------------------------------------------------

        try:

            link = _make_magic_link(
                empresa.id
            )

            html = _email_ativacao_html(
                empresa,
                link
            )

            _send_email(
                empresa.email,
                "Pagamento aprovado - AcheTece",
                html
            )

        except Exception:

            current_app.logger.exception(
                (
                    "[MP] Pagamento aprovado, "
                    "mas falhou o envio do e-mail "
                    f"para empresa_id={empresa.id}."
                )
            )

        return {
            "ok": True,
            "empresa_id": empresa.id,
            "ativou": True,
            "status": "approved",
            "idempotente": False,
        }

    # ==========================================================
    # DEMAIS STATUS
    #
    # Uma empresa já ativa nunca é rebaixada para pendente por
    # webhook posterior.
    # ==========================================================

    if status_atual != "ativo":

        if status_atual != "pendente":

            empresa.status_pagamento = (
                "pendente"
            )

            db.session.commit()

    return {
        "ok": True,
        "empresa_id": empresa.id,
        "ativou": False,
        "status": status,
        "idempotente": True,
    }

# ==============================================================
# MERCADO PAGO — INICIAR CHECKOUT
# ==============================================================

@app.get(
    "/checkout",
    endpoint="checkout"
)
@limiter.limit(
    "10 per 15 minutes",
    key_func=_malharia_rate_limit_key
)
@limiter.limit(
    "40 per day",
    key_func=_malharia_rate_limit_key
)
def checkout():
    """
    Cria uma preferência de pagamento no Mercado Pago.

    Segurança:
    - exige uma malharia autenticada;
    - utiliza a identidade centralizada da sessão;
    - aceita conta ativa ou conta aguardando regularização;
    - bloqueia conta desativada/inválida;
    - Rate Limit por malharia autenticada;
    - plano validado server-side;
    - utiliza MP_ACCESS_TOKEN somente no servidor;
    - external_reference contém empresa_id + nonce UUID;
    - callbacks utilizam PUBLIC_BASE_URL do ambiente;
    - não registra resposta completa do Mercado Pago nos logs.
    """

    # ==========================================================
    # IDENTIDADE DA MALHARIA
    # ==========================================================

    empresa, usuario = (
        _get_empresa_usuario_da_sessao()
    )

    if not empresa:

        session.clear()

        flash(
            "Faça login para continuar.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    # ==========================================================
    # SITUAÇÃO DA CONTA
    #
    # Uma assinatura inativa NÃO impede checkout,
    # pois justamente precisamos permitir a regularização.
    # ==============================================================

    situacao, mensagem = (
        _avaliar_acesso_conta(
            "malharia",
            empresa,
            usuario
        )
    )

    if situacao in {
        "conta_invalida",
        "conta_inativa",
    }:

        session.clear()

        flash(
            mensagem
            or "A conta não está disponível.",
            "warning"
        )

        return redirect(
            url_for("login")
        )

    # São situações permitidas:
    #
    # ok
    # assinatura_inativa

    if situacao not in {
        "ok",
        "assinatura_inativa",
    }:

        current_app.logger.warning(
            (
                "[SECURITY][CHECKOUT] "
                f"Checkout bloqueado. "
                f"empresa_id={empresa.id} "
                f"situacao={situacao}"
            )
        )

        flash(
            "Não foi possível iniciar o pagamento.",
            "warning"
        )

        return redirect(
            url_for("planos")
        )

    # ==========================================================
    # PLANO
    # ==============================================================

    plano = (
        request.args.get("plano")
        or "mensal"
    ).strip().lower()

    if plano not in {
        "mensal",
        "anual",
    }:

        current_app.logger.warning(
            (
                "[SECURITY][CHECKOUT] "
                f"Plano inválido. "
                f"empresa_id={empresa.id}"
            )
        )

        flash(
            "Selecione um plano válido.",
            "warning"
        )

        return redirect(
            url_for("planos")
        )

    # ==========================================================
    # VALORES
    # ==============================================================

    if plano == "anual":

        titulo_plano = (
            "Assinatura anual AcheTece"
        )

        preco = float(
            PLAN_YEARLY
        )

    else:

        titulo_plano = (
            "Assinatura mensal AcheTece"
        )

        preco = float(
            PLAN_MONTHLY
        )

    if preco <= 0:

        current_app.logger.error(
            (
                "[CHECKOUT] Valor de plano inválido. "
                f"empresa_id={empresa.id} "
                f"plano={plano}"
            )
        )

        flash(
            "O pagamento não pôde ser iniciado.",
            "danger"
        )

        return redirect(
            url_for("planos")
        )

    # ==========================================================
    # BASE PÚBLICA DO AMBIENTE
    # ==============================================================

    try:

        base = _public_base_url()

    except Exception:

        current_app.logger.exception(
            (
                "[CHECKOUT] Falha ao resolver "
                "PUBLIC_BASE_URL."
            )
        )

        flash(
            "O pagamento não pôde ser iniciado agora.",
            "danger"
        )

        return redirect(
            url_for("planos")
        )

    # ==========================================================
    # URLs DE RETORNO
    # ==============================================================

    success_url = (
        f"{base}/pagamento_aprovado"
        f"?plano={plano}"
    )

    failure_url = (
        f"{base}/pagamento_erro"
        f"?plano={plano}"
    )

    pending_url = (
        f"{base}/pagamento_pendente"
        f"?plano={plano}"
    )

    # ==========================================================
    # REFERÊNCIA INTERNA
    #
    # Formato validado posteriormente por:
    # _parse_empresa_id_from_external_reference()
    # ==============================================================

    ext_ref = (
        f"achetece:"
        f"{empresa.id}:"
        f"{uuid.uuid4().hex}"
    )

    # ==========================================================
    # PREFERÊNCIA
    # ==============================================================

    preference_data = {
        "items": [
            {
                "title": titulo_plano,
                "quantity": 1,
                "currency_id": "BRL",
                "unit_price": preco,
            }
        ],

        "payer": (
            {
                "email": empresa.email
            }
            if getattr(
                empresa,
                "email",
                None
            )
            else {}
        ),

        "back_urls": {
            "success": success_url,
            "failure": failure_url,
            "pending": pending_url,
        },

        "auto_return": "approved",

        "external_reference": (
            ext_ref
        ),

        "statement_descriptor": (
            "AcheTece"
        ),
    }

    # ==========================================================
    # CRIAÇÃO NO MERCADO PAGO
    # ==============================================================

    try:

        sdk = _mp_sdk()

        preference_response = (
            sdk.preference()
            .create(
                preference_data
            )
        )

        if not isinstance(
            preference_response,
            dict
        ):

            raise RuntimeError(
                "Resposta inválida ao criar preferência."
            )

        # ------------------------------------------------------
        # HTTP STATUS DO ENVELOPE DO SDK
        # ------------------------------------------------------

        http_status_raw = (
            preference_response.get(
                "status"
            )
        )

        try:

            http_status = int(
                http_status_raw
            )

        except Exception:

            http_status = 0

        if (
            http_status < 200
            or http_status >= 300
        ):

            current_app.logger.warning(
                (
                    "[CHECKOUT] Mercado Pago recusou "
                    "a criação da preferência. "
                    f"empresa_id={empresa.id} "
                    f"plano={plano} "
                    f"http_status={http_status}"
                )
            )

            flash(
                (
                    "Não foi possível iniciar "
                    "o pagamento agora. "
                    "Tente novamente."
                ),
                "danger"
            )

            return redirect(
                url_for("planos")
            )

        # ------------------------------------------------------
        # RESPONSE REAL
        # ------------------------------------------------------

        preference = (
            preference_response.get(
                "response"
            )
            or {}
        )

        if not isinstance(
            preference,
            dict
        ):

            raise RuntimeError(
                "Objeto de preferência inválido."
            )

        init_point = (
            preference.get(
                "init_point"
            )
            or preference.get(
                "sandbox_init_point"
            )
            or ""
        ).strip()

        if not init_point:

            current_app.logger.error(
                (
                    "[CHECKOUT] init_point ausente. "
                    f"empresa_id={empresa.id} "
                    f"plano={plano}"
                )
            )

            flash(
                (
                    "Não foi possível abrir "
                    "o Mercado Pago agora."
                ),
                "danger"
            )

            return redirect(
                url_for("planos")
            )

        # ======================================================
        # LOG SEGURO
        # ======================================================

        current_app.logger.info(
            (
                "[CHECKOUT] Preferência criada. "
                f"empresa_id={empresa.id} "
                f"plano={plano}"
            )
        )

        return redirect(
            init_point
        )

    except Exception:

        current_app.logger.exception(
            (
                "[CHECKOUT] Falha ao iniciar pagamento. "
                f"empresa_id={empresa.id} "
                f"plano={plano}"
            )
        )

        flash(
            (
                "Não foi possível iniciar "
                "o pagamento agora. "
                "Tente novamente."
            ),
            "danger"
        )

        return redirect(
            url_for("planos")
        )

# ==============================================================
# MERCADO PAGO — RETORNO DE PAGAMENTO APROVADO
# ==============================================================

@app.get(
    "/pagamento_aprovado",
    endpoint="pagamento_aprovado"
)
@limiter.limit(
    "20 per 15 minutes",
    key_func=_malharia_rate_limit_key
)
@limiter.limit(
    "100 per day",
    key_func=_malharia_rate_limit_key
)
def pagamento_aprovado():
    """
    Retorno de sucesso do Checkout Pro.

    Segurança:
    - nunca confia somente na URL de retorno;
    - valida o formato do payment_id;
    - consulta o pagamento diretamente no Mercado Pago;
    - empresa é resolvida pelo external_reference validado;
    - Rate Limit reduz abuso da consulta à API externa;
    - acesso_liberado somente ocorre quando a sessão atual
      pertence à mesma empresa do pagamento;
    - resposta não é armazenada em cache;
    - payment_id da URL não é propagado via Referer.
    """

    # ==========================================================
    # ESTADO INICIAL
    # ==========================================================

    acesso_liberado = False
    processamento_ok = False

    # ==========================================================
    # PAYMENT ID
    # ==========================================================

    payment_id_raw = (
        request.args.get("payment_id")
        or request.args.get("collection_id")
        or request.args.get("paymentId")
        or ""
    )

    payment_id = (
        str(payment_id_raw)
        .strip()
    )

    # ==========================================================
    # VALIDAÇÃO BÁSICA
    #
    # IDs de pagamento do Mercado Pago são numéricos.
    # Um valor malformado nem chega à API externa.
    # ==========================================================

    payment_id_valido = (
        bool(payment_id)
        and payment_id.isdigit()
        and len(payment_id) <= 32
    )

    if not payment_id_valido:

        current_app.logger.warning(
            (
                "[SECURITY][BACK_URL] "
                "Retorno de pagamento sem "
                "payment_id válido."
            )
        )

    else:

        try:

            # ==================================================
            # FONTE DE VERDADE
            #
            # _processar_pagamento consulta o Mercado Pago
            # server-side e valida external_reference.
            # ==================================================

            result = (
                _processar_pagamento(
                    payment_id
                )
            )

            if isinstance(
                result,
                dict
            ):

                processamento_ok = (
                    result.get("ok")
                    is True
                )

                empresa_id_resultado = (
                    result.get(
                        "empresa_id"
                    )
                )

                # ==============================================
                # EMPRESA DO PAGAMENTO
                # ==============================================

                try:

                    empresa_id_resultado = int(
                        empresa_id_resultado
                    )

                except (
                    TypeError,
                    ValueError
                ):

                    empresa_id_resultado = None

                empresa_pagamento = None

                if empresa_id_resultado:

                    empresa_pagamento = (
                        db.session.get(
                            Empresa,
                            empresa_id_resultado
                        )
                    )

                # ==============================================
                # SESSÃO
                #
                # Por segurança, ausência de sessão NÃO é
                # considerada correspondência.
                # ==============================================

                empresa_id_sessao = (
                    session.get(
                        "empresa_id"
                    )
                )

                mesma_empresa = False

                if (
                    empresa_id_sessao
                    is not None
                    and empresa_id_resultado
                    is not None
                ):

                    try:

                        mesma_empresa = (
                            int(
                                empresa_id_sessao
                            )
                            == empresa_id_resultado
                        )

                    except (
                        TypeError,
                        ValueError
                    ):

                        mesma_empresa = False

                # ==============================================
                # PAGAMENTO ATIVO
                # ==============================================

                pagamento_ativo = False

                if empresa_pagamento:

                    status_pagamento = (
                        getattr(
                            empresa_pagamento,
                            "status_pagamento",
                            ""
                        )
                        or ""
                    ).strip().lower()

                    pagamento_ativo = (
                        status_pagamento
                        == "ativo"
                    )

                # ==============================================
                # ACESSO LIBERADO NA TELA
                #
                # Exige simultaneamente:
                #
                # - pagamento confirmado server-side;
                # - empresa ativa;
                # - sessão da mesma empresa.
                # ==============================================

                acesso_liberado = (
                    processamento_ok
                    and pagamento_ativo
                    and mesma_empresa
                )

                # ==============================================
                # LOG SEGURO
                # ==============================================

                current_app.logger.info(
                    (
                        "[BACK_URL] "
                        f"payment_id={payment_id} "
                        f"empresa_id={empresa_id_resultado} "
                        f"status={result.get('status')} "
                        f"idempotente={result.get('idempotente')} "
                        f"same_session={mesma_empresa}"
                    )
                )

            else:

                current_app.logger.warning(
                    (
                        "[BACK_URL] "
                        "Resposta interna inesperada "
                        "ao processar pagamento."
                    )
                )

        except Exception:

            current_app.logger.exception(
                (
                    "[BACK_URL] "
                    "Falha ao validar retorno "
                    f"payment_id={payment_id}."
                )
            )

    # ==========================================================
    # RENDER
    # ==========================================================

    response = make_response(
        render_template(
            "pagamento_aprovado.html",
            acesso_liberado=acesso_liberado,
            processamento_ok=processamento_ok
        )
    )

    # ==========================================================
    # PRIVACIDADE
    #
    # A URL pode conter payment_id.
    # Não armazenamos esta página e não propagamos Referer.
    # ==========================================================

    response.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0"
    )

    response.headers[
        "Pragma"
    ] = "no-cache"

    response.headers[
        "Expires"
    ] = "0"

    response.headers[
        "Referrer-Policy"
    ] = "no-referrer"

    return response

# ==============================================================
# MERCADO PAGO — ALIAS LEGADO DE SUCESSO
# ==============================================================

@app.get(
    "/pagamento_sucesso",
    endpoint="pagamento_sucesso"
)
def pagamento_sucesso():
    """
    Alias legado de compatibilidade.

    Nunca exibe diretamente uma mensagem de pagamento aprovado.

    Encaminha os parâmetros reconhecidos para a rota canônica
    /pagamento_aprovado, onde o payment_id é validado novamente
    diretamente no Mercado Pago.
    """

    payment_id = (
        request.args.get("payment_id")
        or request.args.get("collection_id")
        or request.args.get("paymentId")
        or ""
    )

    plano = (
        request.args.get("plano")
        or ""
    ).strip().lower()

    params = {}

    if payment_id:

        params["payment_id"] = (
            str(payment_id).strip()
        )

    if plano in {
        "mensal",
        "anual",
    }:

        params["plano"] = plano

    return redirect(
        url_for(
            "pagamento_aprovado",
            **params
        ),
        code=302
    )

# ==============================================================
# MERCADO PAGO — RETORNO DE ERRO
# ==============================================================

@app.get(
    "/pagamento_erro",
    endpoint="pagamento_erro"
)
def pagamento_erro():
    """
    Página de retorno quando o Checkout Pro não é concluído.

    Esta página não altera status de assinatura e não considera
    qualquer parâmetro da URL como prova de pagamento.
    """

    response = make_response(
        render_template(
            "pagamento_erro.html"
        )
    )

    response.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0"
    )

    response.headers[
        "Pragma"
    ] = "no-cache"

    response.headers[
        "Expires"
    ] = "0"

    response.headers[
        "Referrer-Policy"
    ] = "no-referrer"

    return response


# ==============================================================
# MERCADO PAGO — RETORNO PENDENTE
# ==============================================================

@app.get(
    "/pagamento_pendente",
    endpoint="pagamento_pendente"
)
def pagamento_pendente():
    """
    Página informativa para pagamentos ainda pendentes.

    Não ativa assinatura e não utiliza parâmetros da URL
    como confirmação de pagamento.

    A ativação real continua sendo responsabilidade do
    processamento server-side do Mercado Pago.
    """

    response = make_response(
        render_template(
            "pagamento_pendente.html"
        )
    )

    response.headers[
        "Cache-Control"
    ] = (
        "no-store, no-cache, "
        "must-revalidate, max-age=0"
    )

    response.headers[
        "Pragma"
    ] = "no-cache"

    response.headers[
        "Expires"
    ] = "0"

    response.headers[
        "Referrer-Policy"
    ] = "no-referrer"

    return response

# ==============================================================
# MERCADO PAGO — WEBHOOK
# ==============================================================

@app.route(
    "/webhook",
    methods=[
        "GET",
        "POST",
    ]
)
@csrf.exempt
@limiter.exempt
def webhook():
    """
    Webhook do Mercado Pago.

    GET:
        health check simples.

    POST:
        1. valida assinatura;
        2. aceita somente tópico payment;
        3. extrai o payment_id vinculado à assinatura;
        4. consulta o pagamento novamente no Mercado Pago;
        5. processa a empresa pelo external_reference.

    Não registra payload, assinatura ou query string completa.
    """

    # ==========================================================
    # HEALTH CHECK
    # ==========================================================

    if request.method == "GET":

        return jsonify({
            "ok": True,
            "webhook": "ativo"
        }), 200

    # ==========================================================
    # ASSINATURA
    # ==========================================================

    assinatura_ok, motivo = (
        _validar_assinatura_webhook_mp(
            request
        )
    )

    if not assinatura_ok:

        current_app.logger.warning(
            (
                "[SECURITY][MP_WEBHOOK] "
                "Notificação rejeitada. "
                f"motivo={motivo} "
                f"ip={_client_ip()}"
            )
        )

        return jsonify({
            "ok": False
        }), 401

    # ==========================================================
    # PAYLOAD
    # ==========================================================

    payload = (
        request.get_json(
            silent=True
        )
        or {}
    )

    if not isinstance(
        payload,
        dict
    ):

        payload = {}

    # ==========================================================
    # TIPO DA NOTIFICAÇÃO
    # ==========================================================

    topic = (
        request.args.get("type")
        or payload.get("type")
        or ""
    ).strip().lower()

    # Este endpoint do AcheTece processa somente pagamentos.
    if topic != "payment":

        current_app.logger.info(
            (
                "[MP_WEBHOOK] "
                f"Tópico ignorado: {topic or 'unknown'}"
            )
        )

        return jsonify({
            "ok": True,
            "ignored": True
        }), 200

    # ==========================================================
    # PAYMENT ID
    # ==========================================================

    payment_id, _ = (
        _extract_payment_id(
            request
        )
    )

    if not payment_id:

        current_app.logger.warning(
            "[SECURITY][MP_WEBHOOK] "
            "payment_id ausente ou inconsistente."
        )

        return jsonify({
            "ok": False
        }), 400

    # ==========================================================
    # PROCESSAMENTO
    # ==========================================================

    try:

        result = _processar_pagamento(
            payment_id
        )

        current_app.logger.info(
            (
                "[MP_WEBHOOK] "
                f"payment_id={payment_id} "
                f"empresa_id={result.get('empresa_id')} "
                f"ativou={result.get('ativou')} "
                f"status={result.get('status')}"
            )
        )

        return jsonify({
            "ok": True
        }), 200

    except Exception:

        current_app.logger.exception(
            (
                "[MP_WEBHOOK] "
                "Falha ao processar pagamento "
                f"payment_id={payment_id}."
            )
        )

        # Não retornamos detalhes internos.
        #
        # O erro 500 permite que o Mercado Pago tente novamente
        # em caso de falha temporária de API ou banco.
        return jsonify({
            "ok": False
        }), 500

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

@app.route(
    "/contato",
    methods=["GET", "POST"]
)
@limiter.limit(
    "5 per 15 minutes",
    methods=["POST"]
)
@limiter.limit(
    "20 per day",
    methods=["POST"]
)
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
            except Exception:

                current_app.logger.exception(
                    "[CONTATO] Falha ao enviar formulário."
                )
            
                erro = (
                    "Não foi possível enviar sua mensagem agora. "
                    "Tente novamente mais tarde."
                )
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


# ==============================================================
# RECUPERAÇÃO DE SENHA — CHAVE DE RATE LIMIT POR E-MAIL
# ==============================================================

def _password_reset_email_rate_key():
    """
    Cria a chave utilizada pelo rate limit por endereço de e-mail.

    A regra é aplicada independentemente de o e-mail possuir
    ou não uma conta, evitando que o próprio rate limit revele
    a existência de usuários.
    """

    email = (
        request.form.get("email")
        or ""
    ).strip().lower()

    if email:
        return (
            f"password-reset-email:{email[:255]}"
        )

    return (
        f"password-reset-ip:{_client_ip()}"
    )


# ==============================================================
# RECUPERAÇÃO DE SENHA — SOLICITAÇÃO
# ==============================================================

@app.get(
    "/esqueci_senha",
    endpoint="esqueci_senha"
)
def esqueci_senha():
    """
    Exibe o formulário público de recuperação de senha.

    Nenhuma consulta de conta é realizada no GET.
    """

    return render_template(
        "esqueci_senha.html"
    )


@app.post(
    "/esqueci_senha",
    endpoint="post_esqueci_senha"
)
@limiter.limit(
    "5 per 30 minutes"
)
@limiter.limit(
    "5 per 30 minutes",
    key_func=_password_reset_email_rate_key
)
def post_esqueci_senha():
    """
    Processa uma solicitação de recuperação de senha.

    Segurança:
    - CSRF global;
    - limite por IP;
    - limite adicional por e-mail;
    - comprador e malharia utilizam o mesmo fluxo;
    - resposta pública não revela se a conta existe;
    - falha de envio também não altera a resposta pública.
    """

    # ==========================================================
    # NORMALIZAÇÃO
    # ==========================================================

    email = (
        request.form.get("email")
        or ""
    ).strip().lower()

    # ==========================================================
    # VALIDAÇÃO BÁSICA DO FORMATO
    #
    # Isto não revela existência de conta.
    # Apenas rejeita entrada evidentemente inválida.
    # ==========================================================

    email_valido = (
        bool(email)
        and len(email) <= 255
        and re.fullmatch(
            r"[^@\s]+@[^@\s]+\.[^@\s]+",
            email
        )
        is not None
    )

    if not email_valido:

        return render_template(
            "esqueci_senha.html",
            erro=(
                "Informe um endereço de e-mail válido."
            )
        )

    # ==========================================================
    # TENTA GERAR E ENVIAR A RECUPERAÇÃO
    #
    # IMPORTANTE:
    # O resultado não é revelado ao usuário.
    # ==========================================================

    try:

        enviado = enviar_email_recuperacao(
            email
        )

        if not enviado:

            current_app.logger.info(
                (
                    "[SECURITY][PASSWORD_RESET_REQUEST] "
                    "Solicitação concluída sem envio público. "
                    f"ip={_client_ip()}"
                )
            )

    except Exception:

        # ------------------------------------------------------
        # Mesmo uma falha interna de envio não pode revelar
        # ao navegador se a conta existe.
        # ------------------------------------------------------

        current_app.logger.exception(
            (
                "[SECURITY][PASSWORD_RESET_REQUEST] "
                "Falha interna durante solicitação. "
                f"ip={_client_ip()}"
            )
        )

    # ==========================================================
    # RESPOSTA GENÉRICA — ANTI-ENUMERAÇÃO
    # ==========================================================

    return render_template(
        "esqueci_senha.html",
        mensagem=(
            "Se houver uma conta associada a este e-mail, "
            "você receberá as instruções para redefinir "
            "sua senha."
        )
    )
    

# ==============================================================
# RECUPERAÇÃO DE SENHA — REDEFINIÇÃO
# ==============================================================

@app.get(
    "/redefinir_senha/<token>",
    endpoint="redefinir_senha"
)
def redefinir_senha(token):
    """
    Exibe o formulário de redefinição somente quando o
    PasswordResetToken é válido, não utilizado e não expirado.
    """

    token_record, motivo = _buscar_password_reset_token(
        token
    )

    if token_record is None:

        if motivo == "expired":

            flash(
                "O link de redefinição expirou. "
                "Solicite um novo link.",
                "warning"
            )

        else:

            flash(
                "Este link de redefinição não é válido "
                "ou já foi utilizado.",
                "warning"
            )

        return render_template(
            "erro_token.html"
        )

    return render_template(
        "redefinir_senha.html",
        token_valido=True
    )


@app.post(
    "/redefinir_senha/<token>",
    endpoint="post_redefinir_senha"
)
@limiter.limit(
    "10 per 15 minutes"
)
def post_redefinir_senha(token):
    """
    Salva uma nova senha utilizando PasswordResetToken
    server-side.

    Segurança:
    - valida novamente o token no POST;
    - mínimo de 6 caracteres;
    - exige confirmação da senha;
    - limita tamanho máximo;
    - identifica comprador ou malharia pelo token;
    - verifica se o e-mail da conta ainda corresponde ao token;
    - token e senha são atualizados na mesma transação;
    - token é consumido definitivamente após sucesso.
    """

    # ==========================================================
    # VALIDA TOKEN NOVAMENTE
    # ==========================================================

    token_record, motivo = _buscar_password_reset_token(
        token
    )

    if token_record is None:

        if motivo == "expired":

            flash(
                "O link de redefinição expirou. "
                "Solicite um novo link.",
                "warning"
            )

        else:

            flash(
                "Este link de redefinição não é válido "
                "ou já foi utilizado.",
                "warning"
            )

        return render_template(
            "erro_token.html"
        )

    # ==========================================================
    # RECEBE SENHAS
    # ==========================================================

    nova_senha = (
        request.form.get("senha")
        or ""
    )

    confirmar_senha = (
        request.form.get("confirmar_senha")
        or ""
    )

    # ==========================================================
    # VALIDAÇÃO SERVER-SIDE
    # ==========================================================

    if len(nova_senha) < 6:

        return render_template(
            "redefinir_senha.html",
            token_valido=True,
            mensagem=(
                "A nova senha deve possuir "
                "pelo menos 6 caracteres."
            )
        )

    if len(nova_senha) > 128:

        return render_template(
            "redefinir_senha.html",
            token_valido=True,
            mensagem=(
                "A senha informada é muito longa. "
                "Utilize no máximo 128 caracteres."
            )
        )

    if nova_senha != confirmar_senha:

        return render_template(
            "redefinir_senha.html",
            token_valido=True,
            mensagem=(
                "A confirmação da senha não corresponde "
                "à nova senha."
            )
        )

    # ==========================================================
    # IDENTIFICA TIPO DA CONTA
    # ==========================================================

    account_type = (
        token_record.account_type
        or ""
    ).strip().lower()

    try:

        account_id = int(
            token_record.account_id
        )

    except Exception:

        account_id = 0

    now = datetime.utcnow()

    try:

        # ======================================================
        # COMPRADOR
        # ======================================================

        if account_type == "cliente":

            usuario = db.session.get(
                Usuario,
                account_id
            )

            if usuario is None:

                token_record.used_at = now

                db.session.commit()

                flash(
                    "Não foi possível validar este link "
                    "de redefinição.",
                    "warning"
                )

                return render_template(
                    "erro_token.html"
                )

            email_atual = (
                getattr(
                    usuario,
                    "email",
                    ""
                )
                or ""
            ).strip().lower()

            email_token = (
                token_record.email
                or ""
            ).strip().lower()

            if (
                not email_atual
                or email_atual != email_token
            ):

                token_record.used_at = now

                db.session.commit()

                current_app.logger.warning(
                    (
                        "[SECURITY][PASSWORD_RESET] "
                        "E-mail da conta cliente não corresponde "
                        f"ao token. token_id={token_record.id}"
                    )
                )

                flash(
                    "Não foi possível validar este link "
                    "de redefinição.",
                    "warning"
                )

                return render_template(
                    "erro_token.html"
                )

            novo_hash = generate_password_hash(
                nova_senha
            )

            usuario.senha_hash = novo_hash

        # ======================================================
        # MALHARIA
        # ======================================================

        elif account_type == "malharia":

            empresa = db.session.get(
                Empresa,
                account_id
            )

            if empresa is None:

                token_record.used_at = now

                db.session.commit()

                flash(
                    "Não foi possível validar este link "
                    "de redefinição.",
                    "warning"
                )

                return render_template(
                    "erro_token.html"
                )

            email_atual = (
                getattr(
                    empresa,
                    "email",
                    ""
                )
                or ""
            ).strip().lower()

            email_token = (
                token_record.email
                or ""
            ).strip().lower()

            if (
                not email_atual
                or email_atual != email_token
            ):

                token_record.used_at = now

                db.session.commit()

                current_app.logger.warning(
                    (
                        "[SECURITY][PASSWORD_RESET] "
                        "E-mail da malharia não corresponde "
                        f"ao token. token_id={token_record.id}"
                    )
                )

                flash(
                    "Não foi possível validar este link "
                    "de redefinição.",
                    "warning"
                )

                return render_template(
                    "erro_token.html"
                )

            novo_hash = generate_password_hash(
                nova_senha
            )

            empresa.senha = novo_hash

            # --------------------------------------------------
            # Mantém Usuario vinculado sincronizado,
            # quando a malharia possuir usuário associado.
            # --------------------------------------------------

            usuario_vinculado = getattr(
                empresa,
                "usuario",
                None
            )

            if usuario_vinculado is not None:

                usuario_vinculado.senha_hash = (
                    novo_hash
                )

        # ======================================================
        # TIPO DESCONHECIDO — FAIL CLOSED
        # ======================================================

        else:

            token_record.used_at = now

            db.session.commit()

            current_app.logger.warning(
                (
                    "[SECURITY][PASSWORD_RESET] "
                    "account_type inesperado durante reset. "
                    f"token_id={token_record.id}"
                )
            )

            flash(
                "Não foi possível validar este link "
                "de redefinição.",
                "warning"
            )

            return render_template(
                "erro_token.html"
            )

        # ======================================================
        # CONSOME TOKEN
        #
        # Senha e token são confirmados na mesma transação.
        # ======================================================

        token_record.used_at = now

        db.session.commit()

        current_app.logger.info(
            (
                "[SECURITY][PASSWORD_RESET_SUCCESS] "
                f"account_type={account_type} "
                f"account_id={account_id}"
            )
        )

        flash(
            (
                "Senha redefinida com sucesso. "
                "Faça login com sua nova senha."
            ),
            "success"
        )

        return redirect(
            url_for(
                "login"
            )
        )

    except Exception:

        db.session.rollback()

        current_app.logger.exception(
            "[SECURITY][PASSWORD_RESET] "
            "Falha ao concluir redefinição de senha."
        )

        return render_template(
            "redefinir_senha.html",
            token_valido=True,
            mensagem=(
                "Não foi possível redefinir sua senha agora. "
                "Tente novamente."
            )
        )

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

