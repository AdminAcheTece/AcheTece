"""AcheTece ED02.5 V5.10.5 — HTTP PostgreSQL READ ONLY após merge.

Executa diretamente o main.py operacional do HEAD aprovado. NÃO produz candidato,
não altera main.py, não ativa pagamento, não faz requests de webhook e não
executa SQL DML. O segredo de conexão jamais é impresso.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib.util
import json
import logging
import os
from pathlib import Path
from urllib.parse import urlparse

HOST = 'dpg-dbgfj8l9fdbs73depvd0-a.oregon-postgres.render.com'
DATABASE = 'achetece_ed01_test'
BRANCH = 'refs/heads/ed02-assinaturas-vigencia-ledger'
TRIGGER = 'ED02_RUN_HTTP_PG_ED025_V5105_READONLY'
MAIN_SHA = '7e5725b80b0f5c50f3b78b4a6f8ca930c74de0b4d97949ebf1b1794f79275200'
SHADOW_SHA = '45fe37566c9efedcd109eda56499e5ef0c48b978ef3a596003dcc30ec5973e44'
ROUTES = (
    ('/', 200),
    ('/?estado=SC&cidade=Jaragua%20do%20Sul&pagina=2', 200),
    ('/exportar', 200),
    ('/exportar?estado=SC&tipo=circular', 200),
    ('/utils/empresas_json?escopo=pagantes', 200),
    ('/utils/empresas_json?escopo=pagantes&uf=SC', 200),
    ('/empresa/1', 404),
    ('/empresa/999999', 404),
)
ALVOS = frozenset(('index', 'exportar', 'utils_empresas_json', 'empresa_perfil'))
FINANCIAL_FLAGS = (
    'ED02_V510_REPLAY_HOMOLOG', 'ED02_EXACT_ONCE_HOMOLOG',
    'ED02_FINANCIAL_GUARD_HOMOLOG', 'ED02_VIGENCIA_WRITE_HOMOLOG',
    'ED02_CHECKOUT_LEDGER_HOMOLOG', 'ED02_SHADOW_LEDGER',
    'ED02_V5_GATE_HOMOLOG', 'ED02_V5_ACTIVATE_HOMOLOG',
)


class GateError(RuntimeError):
    """Erro de segurança com rótulo não sensível."""


def demand(condition: bool, code: str) -> None:
    if not condition:
        raise GateError(code)


def _hash(path: Path) -> str:
    demand(path.is_file() and not path.is_symlink(), 'ARQUIVO_AUSENTE_OU_SYMLINK')
    return hashlib.sha256(path.read_bytes()).hexdigest()


def verify_files(root: Path) -> None:
    demand(_hash(root / 'main.py') == MAIN_SHA, 'MAIN_SHA_DIFERENTE')
    demand(_hash(root / 'ed02_5_v510_http_shadow.py') == SHADOW_SHA,
           'SHADOW_SHA_DIFERENTE')
    manifest = json.loads((root / 'ED02_V5103_MANIFESTO.json').read_text('utf-8'))
    demand(manifest['sha256']['main.py'] == MAIN_SHA, 'MANIFESTO_MAIN_DIVERGENTE')
    demand(manifest['sha256']['ed02_5_v510_http_shadow.py'] == SHADOW_SHA,
           'MANIFESTO_SHADOW_DIVERGENTE')
    demand(manifest.get('producao_autorizada') is False and
           manifest.get('checkout_pagamentos_reais_autorizado') is False,
           'MANIFESTO_LIBERACAO_INDEVIDA')


def validate_database_url(raw: str) -> None:
    try:
        parsed = urlparse(raw)
        good = (parsed.scheme in ('postgresql', 'postgresql+psycopg',
                                  'postgresql+psycopg2') and
                parsed.hostname == HOST and parsed.path == '/' + DATABASE and
                bool(parsed.username) and bool(parsed.password) and
                parsed.port in (None, 5432) and not parsed.fragment)
    except (ValueError, TypeError):
        good = False
    demand(bool(good), 'BANCO_NAO_AUTORIZADO')


def verify_execution_context(env: dict | None = None) -> str:
    e = os.environ if env is None else env
    required = {
        'GITHUB_ACTIONS': 'true',
        'GITHUB_EVENT_NAME': 'push',
        'GITHUB_ACTOR': 'AdminAcheTece',
        'GITHUB_REF': BRANCH,
        'ED02_V5105_READONLY_AUTHORIZED': 'true',
        'ED02_V5105_COMMIT_MESSAGE': TRIGGER,
    }
    for name, value in required.items():
        demand(e.get(name) == value, 'CONTEXTO_NAO_AUTORIZADO')
    for flag in FINANCIAL_FLAGS:
        demand(str(e.get(flag, '')).strip().lower() not in ('1', 'true', 'on', 'yes'),
               'FLAG_FINANCEIRA_ATIVA')
    raw = e.get('ED02_HOMOLOG_DATABASE_URL', '')
    validate_database_url(raw)
    return raw


def configure(mode: str) -> None:
    demand(mode in ('off', 'shadow'), 'MODO_NAO_AUTORIZADO')
    raw = verify_execution_context()
    from sqlalchemy.engine import make_url

    url = make_url(raw)
    # Independentemente dos parametros da URL original, o default da sessao e READ ONLY.
    safe_query = dict(url.query)
    safe_query['options'] = '-c default_transaction_read_only=on'
    safe_query['sslmode'] = 'require'
    protected = url.set(drivername='postgresql+psycopg', query=safe_query)

    # Nao usar quaisquer URLs ou credenciais herdadas do ambiente da aplicacao.
    for key in ('INTERNAL_DATABASE_URL', 'DATABASE_URL_INTERNAL',
                'DATABASE_URL', 'SQLALCHEMY_DATABASE_URI', 'MP_ACCESS_TOKEN',
                'MERCADOPAGO_ACCESS_TOKEN', 'MERCADO_PAGO_ACCESS_TOKEN',
                'RESEND_API_KEY', 'SMTP_PASS', 'GOOGLE_CLIENT_SECRET'):
        os.environ.pop(key, None)
    os.environ['SQLALCHEMY_DATABASE_URI'] = protected.render_as_string(hide_password=False)
    os.environ['DATABASE_URL'] = os.environ['SQLALCHEMY_DATABASE_URI']
    for flag in FINANCIAL_FLAGS:
        os.environ[flag] = 'false'
    os.environ.update({
        'ED01_HOMOLOGACAO': 'true',
        'ALLOW_SQLITE_FALLBACK': 'false',
        'OTP_DEV_FALLBACK': 'false',
        'MAIL_SUPPRESS_SEND': 'true',
        'ED02_V510_HTTP_SHADOW_HOMOLOG': 'true' if mode == 'shadow' else 'false',
    })


def snapshot(mod) -> tuple:
    from sqlalchemy import text
    with mod.db.engine.connect() as conn:
        demand(mod.db.engine.dialect.name == 'postgresql', 'DIALETO_INVALIDO')
        demand(conn.execute(text('SHOW default_transaction_read_only')).scalar_one()
               == 'on', 'SESSAO_NAO_READONLY')
        demand(conn.execute(text('SHOW transaction_read_only')).scalar_one()
               == 'on', 'TRANSACAO_NAO_READONLY')
        demand(conn.execute(text('SELECT current_database()')).scalar_one()
               == DATABASE, 'NOME_BANCO_INVALIDO')
        return tuple(conn.execute(text(
            'SELECT id, status_pagamento, data_pagamento '
            'FROM public.empresa ORDER BY id')).all())


def run(mode: str, output: Path, baseline: Path | None = None) -> None:
    root = Path(__file__).resolve().parent
    verify_files(root)
    configure(mode)
    # main.py inclui caminho legado que poderia escrever a URL inteira em logger
    # caso falhe a conexao. Impedir QUALQUER log durante esta execucao protegida.
    logging.disable(logging.CRITICAL)

    import ed02_5_v510_http_shadow as shadow
    observed: list[tuple[str, dict]] = []
    real_observer = shadow.observar_http

    def observe(*args, **kwargs):
        result = real_observer(*args, **kwargs)
        endpoint = args[1] if len(args) > 1 else kwargs.get('endpoint')
        demand(endpoint in ALVOS, 'ENDPOINT_SHADOW_INVALIDO')
        demand(result.get('publicacoes_autorizadas') == 0 and
               result.get('modifica_consultas_publicas') is False,
               'SHADOW_TENTOU_PUBLICAR')
        demand(shadow.contar_divergencias_reais(result) == 0,
               'DIVERGENCIA_SHADOW_REAL')
        observed.append((endpoint, result))
        return result

    shadow.observar_http = observe
    path = root / 'main.py'
    spec = importlib.util.spec_from_file_location('achetece_v5105_main_integrado', path)
    demand(spec is not None and spec.loader is not None, 'MAIN_NAO_CARREGAVEL')
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)

    # O bootstrap e o rastreamento analytics da aplicacao possuem SQL de escrita.
    # Este ensaio NAO os exercita, nem os substitui no arquivo original.
    callbacks = mod.app.before_request_funcs.get(None, [])
    bootstrap = [cb for cb in callbacks if cb.__name__ == '_bootstrap_and_analytics_lazy']
    demand(len(bootstrap) == 1, 'BOOTSTRAP_INESPERADO')
    mod.app.before_request_funcs[None] = [cb for cb in callbacks if cb not in bootstrap]
    mod.track_event = lambda *args, **kwargs: None
    mod.app.config.update(TESTING=False, PROPAGATE_EXCEPTIONS=False)

    hooks = [cb for cb in mod.app.after_request_funcs.get(None, [])
             if cb.__name__ == '_v510_shadow_diagnostico']
    demand(len(hooks) == (1 if mode == 'shadow' else 0), 'HOOK_SHADOW_INVALIDO')

    with mod.app.app_context():
        before = snapshot(mod)
        demand(len(before) == 1 and before[0][0] == 1 and
               (before[0][1] or '').strip().lower() == 'pendente' and
               before[0][2] is None, 'FIXTURE_REAL_ALTERADA')
        client = mod.app.test_client()
        responses = []
        for url, expected in ROUTES:
            resp = client.get(url)
            demand(resp.status_code == expected, 'HTTP_STATUS_DIVERGENTE')
            item = {'rota': url, 'status': resp.status_code}
            if url.startswith('/exportar'):
                csv_lines = resp.get_data().decode('utf-8-sig').splitlines()
                demand(len(csv_lines) == 1, 'CSV_EXPOS_EMPRESA_PENDENTE')
                item['csv_header_sha256'] = hashlib.sha256(csv_lines[0].encode()).hexdigest()
                item['linhas_csv'] = 1
            elif url.startswith('/utils/empresas_json'):
                demand(resp.get_json() == [], 'JSON_EXPOS_EMPRESA_PENDENTE')
                item['registros_json'] = 0
            elif url.startswith('/') and '?' in url or url == '/':
                demand(b'ED01 Malharia Teste' not in resp.data, 'HOME_EXPOS_EMPRESA_PENDENTE')
                item['empresa_pendente_exposta'] = False
            responses.append(item)
        after = snapshot(mod)
        demand(before == after, 'EMPRESA_HOMOLOG_ALTERADA')
        mod.db.engine.dispose()

    if mode == 'shadow':
        demand(ALVOS.issubset({key for key, _ in observed}),
               'QUATRO_ROTAS_SHADOW_NAO_OBSERVADAS')
    else:
        demand(not observed, 'SHADOW_ACIONADO_COM_FLAG_OFF')

    demand(_hash(root / 'main.py') == MAIN_SHA and
           _hash(root / 'ed02_5_v510_http_shadow.py') == SHADOW_SHA,
           'ARQUIVOS_OPERACIONAIS_ALTERADOS')
    if baseline:
        prior = json.loads(baseline.read_text('utf-8'))
        demand(prior['modo'] == 'off' and prior['respostas_http'] == responses,
               'RESPOSTAS_HTTP_DIFERENTES_COM_SHADOW')

    report = {
        'etapa': 'ED02.5 V5.10.5', 'estado': 'PASSOU', 'modo': mode,
        'sha256_main': MAIN_SHA, 'sha256_shadow': SHADOW_SHA,
        'respostas_http': responses,
        'endpoints_shadow': sorted({x for x, _ in observed}),
        'empresa_pendente_preservada': True,
        'consultas_postgresql_readonly': True,
        'gravacoes_permitidas': False,
        'pagamentos_reais': False,
        'webhook_real': False,
        'gate_financeiro_ativado': False,
        'deploy_executado': False,
    }
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', 'utf-8')
    print('PASSOU: V5.10.5 main.py POS-MERGE integrado; hashes aprovados')
    print('PASSOU: PostgreSQL homologacao READ ONLY; empresa original preservada')
    print(f'PASSOU: {len(responses)} respostas HTTP, modo={mode}; shadow={len(set(report["endpoints_shadow"]))} rotas')
    print('PASSOU: zero DML permitido, sem webhook, e-mail, pagamento ou deploy')
    print('LIMITACAO: coorte de uma malharia pendente; renovacao real/legado nao homologados')


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument('--mode', required=True, choices=('off', 'shadow'))
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--baseline', type=Path)
    args = parser.parse_args()
    try:
        run(args.mode, args.output, args.baseline)
    except GateError as exc:
        print('V5105 BLOQUEADO: ' + str(exc))
        raise SystemExit(1) from None
    except Exception:
        # Nunca despejar stacktraces de psycopg/SQLAlchemy que possam conter URLs.
        print('V5105 FALHOU: erro externo protegido; detalhes omitidos')
        raise SystemExit(1) from None


if __name__ == '__main__':
    main()
