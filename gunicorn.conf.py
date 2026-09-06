# ==============================================================
# GUNICORN — LOGS DE ACESSO SEGUROS
# ==============================================================
#
# Não registramos:
# - caminho completo da URL;
# - query string;
# - Referer.
#
# Isso evita exposição de informações sensíveis, especialmente
# tokens de recuperação de senha presentes em URLs.
#
# O Flask registra separadamente endpoint/status/IP sem expor
# tokens através do logger seguro definido em main.py.
# ==============================================================

accesslog = "-"
errorlog = "-"

access_log_format = (
    '%(h)s '
    '%(t)s '
    'method=%(m)s '
    'status=%(s)s '
    'size=%(b)s '
    'duration=%(L)s '
    'user_agent="%(a)s"'
)
