import os, sys, socket, pathlib, secrets, hmac, base64
from urllib.parse import urlsplit
from datetime import datetime
import logging
from werkzeug.security import generate_password_hash
# Block all IP networking in this disposable process, before importing application.
# No network exceptions, including localhost. SQLite requires no network.
orig_connect=socket.socket.connect
orig_connect_ex=socket.socket.connect_ex
orig_sendto=socket.socket.sendto
def denied(*args,**kwargs):
    raise RuntimeError('Rede externa bloqueada na prova local')
socket.socket.connect=denied
socket.socket.connect_ex=denied
socket.socket.sendto=denied
socket.getaddrinfo=denied
socket.gethostbyname=denied
socket.gethostbyname_ex=denied
# Fail closed before importing the operational application.
if os.environ.get('ACHETECE_REVISAO_PROTEGIDA') != '1':
    raise RuntimeError('Inicializador exclusivo de revisao')
if os.environ.get('ED01_HOMOLOGACAO','').lower() != 'true' or os.environ.get('MAIL_SUPPRESS_SEND','').lower() != 'true':
    raise RuntimeError('Modo de revisao obrigatorio')
if os.environ.get('PLAN_MONTHLY') != '37.00' or os.environ.get('PLAN_YEARLY') != '377.40':
    raise RuntimeError('Precos divergentes')
_db_path=pathlib.Path(os.environ['PO038B3B_DB']).resolve()
if os.environ.get('SQLALCHEMY_DATABASE_URI') != 'sqlite:///'+_db_path.as_posix():
    raise RuntimeError('URI de banco divergente')
if not _db_path.is_absolute() or _db_path.name != 'achetece-pr9.sqlite':
    raise RuntimeError('Banco de revisao obrigatorio')
if os.name != 'nt' and _db_path != pathlib.Path('/tmp/achetece-pr9.sqlite'):
    raise RuntimeError('Banco remoto fora do escopo')
if os.environ.get('RENDER') == 'true' and os.environ.get('RENDER_SERVICE_NAME') != 'achetece-pr9-revisao':
    raise RuntimeError('Servico Render divergente')
_gate=os.environ.get('REVISAO_ACCESS_PASSWORD','')
_demo=os.environ.get('PR9_DEMO_PASSWORD','')
if min(len(_gate),len(_demo),len(os.environ.get('SECRET_KEY',''))) < 24 or _gate == _demo:
    raise RuntimeError('Credenciais exclusivas de revisao obrigatorias')
import dotenv
dotenv.load_dotenv=lambda *a,**k:False
for key in ('MP_ACCESS_TOKEN','MERCADO_PAGO_TOKEN','MERCADO_PAGO_ACCESS_TOKEN','DATABASE_URL','INTERNAL_DATABASE_URL','DATABASE_URL_INTERNAL','RESEND_API_KEY','SMTP_PASS','GOOGLE_CLIENT_SECRET','SMTP_USER','GOOGLE_CLIENT_ID','MP_PUBLIC_KEY','MERCADO_PAGO_PUBLIC_KEY'):
    if os.environ.get(key): raise RuntimeError('Configuracao proibida: '+key)
import mercadopago
class DeniedSDK:
    def __getattr__(self,name):
        raise RuntimeError('Mercado Pago bloqueado: '+name)
# The original code constructs an SDK on import. Replace it BEFORE importing main.
def deny_sdk(*args,**kwargs): return DeniedSDK()
mercadopago.SDK=deny_sdk
import main as m
from flask import request
from sqlalchemy import inspect
app=m.app
app.config.update(TESTING=False, DEBUG=False, MAIL_SUPPRESS_SEND=True)
assert m.MP_ACCESS_TOKEN==''
assert m.PLAN_MONTHLY==37.0 and m.PLAN_YEARLY==377.4
assert app.config['MAIL_SUPPRESS_SEND'] is True
assert app.config['SESSION_COOKIE_DOMAIN'] is None
# Guard must precede every Flask before_request hook, including app-specific hooks.
# Prototype only: later deployment must use a separate entrypoint and a secret access gate.
BLOCK=('checkout','webhook','pagamento','/pagar','/magic/','/oauth/','/admin/seed','/admin/test-email','/admin/imperson','/login/codigo','/login/código','/esqueci_senha','/redefinir_senha')
def blocked():
    path=request.path.lower().rstrip('/')
    endpoint=(request.endpoint or '').lower()
    if any(x in path for x in BLOCK) or any(x in endpoint for x in ('checkout','webhook','pagamento','preapproval','subscription')):
        return 'Operacao desabilitada na revisao.',503
app.before_request_funcs.setdefault(None,[]).insert(0,blocked)
def sqlite_columns(spec):
    with m.db.engine.begin() as conn:
        if conn.dialect.name != 'sqlite': raise RuntimeError('Adaptador restrito a SQLite')
        cols={c['name'] for c in inspect(conn).get_columns('empresa')}
        for name,ddl in spec.items():
            if name not in cols: conn.exec_driver_sql('ALTER TABLE empresa ADD COLUMN '+name+' '+ddl)
        if not set(spec).issubset({c['name'] for c in inspect(conn).get_columns('empresa')}):
            raise RuntimeError('Colunas SQLite ausentes')
def plano_sqlite():
    sqlite_columns({'plano':"VARCHAR(16) DEFAULT 'mensal'"})
    with m.db.engine.begin() as conn:
        conn.exec_driver_sql("UPDATE empresa SET plano='mensal' WHERE plano IS NULL OR TRIM(plano)=''")
def pagamento_sqlite():
    sqlite_columns({'assinatura_status':"VARCHAR(20) DEFAULT 'pending'",'assinatura_expira_em':'DATETIME NULL'})
def foto_sqlite():
    sqlite_columns({'foto_url':'VARCHAR(255)'})
for name,fn in {'_ensure_empresa_plano_column':plano_sqlite,'_ensure_pagamento_cols':pagamento_sqlite,'_ensure_empresa_foto_column':foto_sqlite}.items():
    if not callable(getattr(m,name,None)): raise RuntimeError('Rotina ausente: '+name)
    setattr(m,name,fn)

with app.app_context():
    url=m.db.engine.url
    if url.get_backend_name()!='sqlite' or pathlib.Path(url.database).resolve()!=_db_path:
        raise RuntimeError('Banco efetivo divergente')
    m.db.create_all()
    plano_sqlite(); pagamento_sqlite(); foto_sqlite()
    e=m.Empresa.query.filter_by(email='revisao@example.invalid').first()
    if e is None:
        e=m.Empresa(nome='Malharia Revisao Ficticia',apelido='Malharia Revisao Ficticia',email='revisao@example.invalid',senha=generate_password_hash(_demo),status_pagamento='ativo',plano='mensal',data_pagamento=datetime.utcnow(),cidade='Jaraguá do Sul',estado='SC',telefone='47000000000')
        m.db.session.add(e); m.db.session.flush()
        m.db.session.add(m.Tear(empresa_id=e.id,marca='Marca Ficticia',modelo='Modelo 01',tipo='MONO',diametro=30,finura=28,alimentadores=90,pistas_cilindro=4,pistas_disco=0,elastano='Sim',ativo=True))
    else:
        e.senha=generate_password_hash(_demo)
    m.db.session.commit()
# WSGI gate protects all URLs, including static assets, before Flask dispatch.
_original=app.wsgi_app
_expected=('Basic '+base64.b64encode(('revisao:'+_gate).encode()).decode()).encode()
def access_gate(environ,start_response):
    supplied=environ.get('HTTP_AUTHORIZATION','').encode('utf-8')
    if not hmac.compare_digest(supplied,_expected):
        start_response('401 Unauthorized',[('WWW-Authenticate','Basic realm="AcheTece Revisao"'),('Content-Type','text/plain; charset=utf-8'),('Cache-Control','no-store'),('X-Robots-Tag','noindex, nofollow')])
        return [b'Acesso restrito a revisao.']
    def secured(status,headers,exc_info=None):
        headers=[(k,v) for k,v in headers if k.lower() not in ('cache-control','x-robots-tag')]
        headers += [('Cache-Control','no-store'),('X-Robots-Tag','noindex, nofollow')]
        return start_response(status,headers,exc_info)
    return _original(environ,secured)
app.wsgi_app=access_gate
