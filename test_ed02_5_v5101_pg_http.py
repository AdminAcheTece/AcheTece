"""ED02.5 V5.10.1 — HTTP original + PostgreSQL real, READ ONLY.

Gatilho protegido, somente homologacao. Nunca realiza webhooks/pagamentos,
INSERTs ou emails. O main.py candidato é gerado do HEAD; não modifica o main.py.
Roda primeiro no modo flags OFF e depois com shadow de observação ligado.
"""
from __future__ import annotations
import argparse
import hashlib
import importlib.util
import json
import os
from pathlib import Path
from urllib.parse import urlparse

HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DATABASE = 'achetece_ed01_test'
TRIGGER = 'ED02_RUN_FLASK_HTTP_ED025_V5101_READONLY'
BRANCH = 'refs/heads/ed02-assinaturas-vigencia-ledger'


def guard() -> str:
    required = {
        'GITHUB_ACTIONS':'true',
        'GITHUB_EVENT_NAME':'push',
        'GITHUB_ACTOR':'AdminAcheTece',
        'GITHUB_REF':BRANCH,
        'ED02_V5101_PG_AUTHORIZED':'true',
        'ED02_V5101_COMMIT_MESSAGE':TRIGGER,
    }
    for k,v in required.items():
        if os.environ.get(k) != v:
            raise RuntimeError('V5101 BLOQUEADO: autorizacao invalida')
    raw = os.environ.get('ED02_HOMOLOG_DATABASE_URL','')
    try: p=urlparse(raw)
    except Exception: raise RuntimeError('V5101 BLOQUEADO: URL invalida') from None
    if not (p.scheme in ('postgresql','postgresql+psycopg','postgresql+psycopg2')
            and p.hostname==HOST and p.path=='/'+DATABASE
            and p.username and p.password and p.port in (None,5432)
            and not p.fragment):
        raise RuntimeError('V5101 BLOQUEADO: banco fora da homologacao')
    return raw


def configure(mode: str):
    if mode not in ('off','shadow'):
        raise RuntimeError('V5101 BLOQUEADO: modo invalido')
    raw=guard()
    from sqlalchemy.engine import make_url
    u=make_url(raw).set(drivername='postgresql+psycopg',
         query={**dict(make_url(raw).query),'options':'-c default_transaction_read_only=on'})
    # NUNCA imprimir URL, credenciais ou cabecalhos.
    for name in ('INTERNAL_DATABASE_URL','DATABASE_URL_INTERNAL'):
        os.environ.pop(name,None)
    os.environ['SQLALCHEMY_DATABASE_URI']=u.render_as_string(hide_password=False)
    os.environ['DATABASE_URL']=os.environ['SQLALCHEMY_DATABASE_URI']
    os.environ.update({
        'ALLOW_SQLITE_FALLBACK':'false','MAIL_SUPPRESS_SEND':'true',
        'ED01_HOMOLOGACAO':'true',
        'ED02_V510_REPLAY_HOMOLOG':'false',
        'ED02_V510_HTTP_SHADOW_HOMOLOG':'true' if mode=='shadow' else 'false',
        'ED02_FINANCIAL_GUARD_HOMOLOG':'false',
        'ED02_EXACT_ONCE_HOMOLOG':'false',
        'ED02_SHADOW_LEDGER':'false',
        'ED02_VIGENCIA_WRITE_HOMOLOG':'false',
        'ED02_CHECKOUT_LEDGER_HOMOLOG':'false',
    })


def require(condition, code):
    if not condition: raise RuntimeError('V5101 FALHOU: '+code)


def main():
    ap=argparse.ArgumentParser()
    ap.add_argument('--mode',choices=('off','shadow'),required=True)
    ap.add_argument('--output',required=True)
    ap.add_argument('--baseline',default=None)
    args=ap.parse_args()
    configure(args.mode)

    from aplicar_ed02_v5101_main import gerar_candidato, digest
    src=Path('main.py').read_text(encoding='utf8')
    can,modo=gerar_candidato(src)
    path=Path('main_V5101_CANDIDATO.py')
    require(path.exists(),'candidato nao foi gerado no job')
    require(path.read_text(encoding='utf8')==can,'candidato e HEAD divergentes')
    require(digest(src)==digest(Path('main.py').read_text(encoding='utf8')),'main alterado')

    # Conta chamadas reais ao comparador; sem substituir sua lógica/SQL.
    import ed02_5_v510_http_shadow as sh
    visited=[]
    real=sh.observar_http
    def audit(*a,**kw):
        r=real(*a,**kw)
        visited.append(a[1] if len(a)>1 else kw.get('endpoint'))
        return r
    sh.observar_http=audit

    spec=importlib.util.spec_from_file_location('achetece_ed02_v5101_protegida',path)
    require(spec is not None and spec.loader is not None,'candidato nao carregavel')
    mod=importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    from sqlalchemy import text
    mod.app.before_request_funcs[None] = [cb for cb in mod.app.before_request_funcs.get(None,[])
        if cb.__name__ not in {'_bootstrap_and_analytics_lazy'}]
    mod.track_event=lambda *a,**k:None  # sem analytics ou escrita incidental
    mod.app.config.update(TESTING=False,PROPAGATE_EXCEPTIONS=False)

    hook_instalado=any(x.__name__=='_v510_shadow_diagnostico'
        for x in mod.app.after_request_funcs.get(None,[]))
    require(hook_instalado==(args.mode=='shadow'),'hook shadow diverge da flag')

    with mod.app.app_context():
        with mod.db.engine.connect() as c:
            require(c.execute(text('SHOW transaction_read_only')).scalar_one()=='on',
                    'conexao deve ser read-only')
            require(c.execute(text('SELECT current_database()')).scalar_one()==DATABASE,
                    'banco nao autorizado')
            before=c.execute(text('SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id')).all()
            require(len(before)==1 and before[0][0]==1 and
                    (before[0][1] or '').strip().lower()=='pendente' and before[0][2] is None,
                    'inventario historico divergiu')
        client=mod.app.test_client()
        checks=[('/',200),('/?estado=SC&cidade=Jaragua%20do%20Sul&pagina=2',200),
                ('/exportar',200),('/exportar?estado=SC&tipo=circular',200),
                ('/utils/empresas_json?escopo=pagantes',200),
                ('/utils/empresas_json?escopo=pagantes&uf=SC',200),
                ('/empresa/1',404),('/empresa/999999',404)]
        out=[]
        for route,code in checks:
            resp=client.get(route)
            require(resp.status_code==code,'resposta HTTP de rota original divergiu')
            item={'endpoint':route.split('?')[0], 'status_http':resp.status_code}
            if route.startswith('/exportar'):
                rows=resp.get_data().decode('utf-8-sig').splitlines()
                require(len(rows)==1,'CSV expos empresa pendente')
                item['linhas_csv']=len(rows)
            if route.startswith('/utils/empresas_json'):
                require(resp.get_json()==[],'JSON expos empresa pendente')
                item['registros_json']=0
            if route=='/':
                require(b'ED01 Malharia Teste' not in resp.data,'home publicou empresa pendente')
                item['empresa_pendente_visivel']=False
            out.append(item)
        with mod.db.engine.connect() as c:
            after=c.execute(text('SELECT id, status_pagamento, data_pagamento FROM empresa ORDER BY id')).all()
            require(c.execute(text('SHOW transaction_read_only')).scalar_one()=='on',
                    'conexao final deve ser read-only')
            require(after==before,'empresa original foi alterada')
    if args.mode=='shadow':
        require({'index','exportar','utils_empresas_json','empresa_perfil'}.issubset(set(visited)),
                'shadow nao observou todos os endpoints com SQL real')
    else:
        require(not visited,'shadow executado com flag OFF')

    summary={'etapa':'ED02.5 V5.10.1','modo':args.mode,
             'estado':'PASSOU','respostas_http':out,'rotas_observadas':sorted(set(visited)),
             'main_operacional_alterado':False,'migracoes':0,'pagamentos_reais':0,
             'gravacoes':0,'politica_ed02_publica_ativada':False}
    if args.baseline:
        base=json.loads(Path(args.baseline).read_text(encoding='utf8'))
        require(out==base['respostas_http'],'shadow alterou respostas HTTP')
    Path(args.output).write_text(json.dumps(summary,indent=2,ensure_ascii=False)+'\n',encoding='utf8')
    print('PASSOU: HTTP original + PostgreSQL real READ ONLY; modo='+args.mode)
    print('PASSOU: home, CSV, JSON, perfil e consultas preservadas; zero escrita')
    print('PASSOU: shadow='+str(len(set(visited)))+' rotas; gate ED02 ainda BLOQUEADO')
    print('LIMITACAO: 1 malharia pendente; nao testa webhook real, renovacao real nem clientes legados')

if __name__=='__main__': main()
