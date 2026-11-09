"""Bloqueios específicos do AcheTece 2 Staging — P0.3-D.

NÃO ativa nas demais aplicações. Não contém credenciais e não toca no Render.
O nome lógico, hostname e ID do serviço são allowlists fixas deste ambiente.
"""
from urllib.parse import urlsplit, unquote
import os

SERVICE_ID = 'srv-daaof4qd0e5s73dlg8a0'
POSTGRES_ID = 'dpg-dbn21tc9v7es73a16270-a'
DB_NAME = 'achetece2_staging_p03'
STAGING_SITE = 'https://achetece-2-staging.onrender.com'
DB_KEYS = ('INTERNAL_DATABASE_URL','DATABASE_URL_INTERNAL','SQLALCHEMY_DATABASE_URI','DATABASE_URL')
DENIED_SECRETS = (
    'MP_ACCESS_TOKEN','MERCADO_PAGO_TOKEN','MP_WEBHOOK_SECRET',
    'RESEND_API_KEY','SMTP_PASS','MAIL_PASS','SENDGRID_API_KEY','MAILGUN_API_KEY',
    'ACHETECE_R2_SECRET_ACCESS_KEY','R2_SECRET_ACCESS_KEY',
    'ACHETECE_R2_ACCESS_KEY_ID','R2_ACCESS_KEY_ID','MAILGUN_API_KEY',
    'GOOGLE_CLIENT_SECRET','GOOGLE_OAUTH_CLIENT_SECRET'
)

class StagingSafetyError(RuntimeError):
    pass


def is_target_staging(env=None):
    env = os.environ if env is None else env
    return str(env.get('RENDER_SERVICE_ID', '')).strip() == SERVICE_ID


def _fail(code):
    # Códigos estáticos: nunca registrar URL, senha ou exceção de conexão.
    raise StagingSafetyError('P03D_CONFIG_REJECTED:' + code)


def _db_identity(raw):
    try:
        u=urlsplit(raw.strip())
        host=(u.hostname or '').lower().rstrip('.')
        name=unquote(u.path.lstrip('/'))
        if (u.scheme not in ('postgres','postgresql','postgresql+psycopg')
            or not u.username or not u.password or not host or not name
            or u.port not in (None,5432)):
            _fail('invalid_postgres_url')
        return (host,name,unquote(u.username),u.port or 5432)
    except (ValueError,AttributeError):
        _fail('malformed_postgres_url')


def validate_staging_config(env=None, *, expected_host=POSTGRES_ID, expected_database=DB_NAME):
    """Pure preflight, no DB connections. Tests can supply synthetic host+DB."""
    env = os.environ if env is None else env
    if str(env.get('RENDER_SERVICE_ID','')).strip() != SERVICE_ID:
        _fail('wrong_render_service')
    if str(env.get('RENDER_GIT_BRANCH','')).strip() not in ('','achetece-2'):
        _fail('unexpected_branch')
    for key, wanted in (
        ('ALLOW_SQLITE_FALLBACK',('0','false','False')),
        ('MAIL_SUPPRESS_SEND',('1','true','True')),
        ('OTP_DEV_FALLBACK',('0','false','False')),
        ('ENABLE_ADMIN_TOOLS',('0','false','False')),
        ('DEMO_MODE',('0','false','False')),
    ):
        if str(env.get(key,'')).strip() not in wanted:
            _fail('unsafe_setting_'+key.lower())
    if str(env.get('ACHETECE_P03D_ALLOW_SCHEMA_BOOTSTRAP','')).strip().lower() not in ('','0','false'):
        _fail('schema_bootstrap_not_approved')
    for key in DENIED_SECRETS:
        if str(env.get(key,'')).strip():
            _fail('external_secret_present_'+key.lower())
    for key in ('PUBLIC_BASE_URL','SITE_URL'):
        if str(env.get(key,'')).strip().rstrip('/') != STAGING_SITE:
            _fail('unexpected_base_url_'+key.lower())
    # Falha fechada: apenas hostname exato verificado. Nao aceitar sufixos arbitrarios.
    # Confirmar no Render o hostname interno antes de qualquer deploy remoto.
    main_url=str(env.get('INTERNAL_DATABASE_URL','')).strip()
    if not main_url:
        _fail('missing_internal_db_url')
    primary = _db_identity(main_url)
    host,name,username,port = primary
    expected_host=expected_host.lower()
    if host != expected_host or name != expected_database:
        _fail('wrong_database_identity')
    for key in DB_KEYS:
        other=str(env.get(key,'')).strip()
        if other and _db_identity(other) != primary:
            _fail('conflicting_db_alias_'+key.lower())
    return main_url


def block_side_effect_path(path,method):
    """Gate de rotas; gateways no próprio código bloqueiam SDK/email/R2."""
    path = '/' + str(path or '').lstrip('/')
    method = str(method or '').upper()
    # Webhook GET também deve mostrar indisponível, não afirmar estar ativo.
    if path == '/webhook' or path.startswith('/webhook/'):
        return 503
    if path == '/oauth/google' or path.startswith('/oauth/google/'):
        return 403  # Sem OIDC externo durante a homologação sintética.
    if path in ('/checkout','/pagar','/admin/test-email','/perfil/foto_upload'):
        return 403
    if method != 'GET' and path.startswith('/checkout/'):
        return 403
    return None
