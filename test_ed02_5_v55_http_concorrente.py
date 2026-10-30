"""ED02.5 V5.5 - concorrencia HTTP na rota webhook REAL extraida do main.py.
Requisicoes HMAC reais via Flask test_client em threads independentes.
O processador financeiro e um double transacional deterministico, NAO PostgreSQL.
A evidencia de isolamento no PostgreSQL permanece na V5.4.
"""
import ast
import hashlib
import hmac
import os
from pathlib import Path
import threading
import unittest
from contextlib import nullcontext
from concurrent.futures import ThreadPoolExecutor
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask, jsonify, request

ROOT = Path(__file__).resolve().parent
SECRET = 'ED02_V55_SECRET_TEST_ONLY'
PAYMENT_ID = '550055001'


def build_app(processor):
    tree = ast.parse((ROOT / 'main.py').read_text(encoding='utf-8'))
    node = next(x for x in tree.body if isinstance(x, ast.FunctionDef) and x.name == 'webhook')
    node.decorator_list = []
    app = Flask('ed02_v55_http')
    app.config['TESTING'] = True
    fake_db = SimpleNamespace(
        engine=SimpleNamespace(dialect=SimpleNamespace(name='postgresql')),
        session=SimpleNamespace(no_autoflush=nullcontext(),
                                execute=lambda *a: SimpleNamespace(scalar_one=lambda: 'achetece_ed01_test')))
    scope = {'os':os, 'request':request, 'jsonify':jsonify, 'app':app, 'db':fake_db,
             '_processar_pagamento':processor,
             '_extract_payment_id':lambda req:(str(req.get_json()['data']['id']),req.get_json())}
    exec(compile(ast.Module(body=[node],type_ignores=[]), str(ROOT/'main.py'),'exec'),scope)
    app.add_url_rule('/webhook','ed02_v55_webhook',scope['webhook'],methods=['GET','POST'])
    return app


def send(app, *, valid=True, payment_id=PAYMENT_ID):
    request_id = 'ED02V55REQ'
    ts = '1720000000000'
    digest = hmac.new(SECRET.encode(),
                      f'id:{payment_id};request-id:{request_id};ts:{ts};'.encode(),
                      hashlib.sha256).hexdigest()
    if not valid:
        digest = '0'*64
    with app.test_client() as client:
        response = client.post('/webhook?type=payment&data.id='+payment_id,
            json={'type':'payment','data':{'id':payment_id}},
            headers={'x-request-id':request_id,'x-signature':f'ts={ts},v1={digest}'})
        return response.status_code,response.get_json()


class V55HTTPConcurrency(unittest.TestCase):
    def setUp(self):
        env = patch.dict(os.environ, {
            'ED02_WEBHOOK_SIGNATURE_HOMOLOG':'true',
            'ED01_HOMOLOGACAO':'true',
            'ED02_HOMOLOG_DATABASE_NAME':'achetece_ed01_test',
            'ED02_MP_WEBHOOK_SECRET': SECRET,
        })
        env.start(); self.addCleanup(env.stop)

    def test_duas_requisicoes_validas_um_efeito_simulado(self):
        lock=threading.Lock(); barrier=threading.Barrier(2,timeout=5)
        inside=threading.Barrier(2,timeout=5)
        state={'count':0,'calls':0}
        def processor(pid):
            inside.wait()
            with lock:
                state['calls'] += 1
                duplicated=state['count'] > 0
                if not duplicated: state['count'] += 1
            return {'ok':True,'duplicado':duplicated,'ativou':False,'modo':'v55_simulado'}
        app=build_app(processor)
        def worker(_):
            barrier.wait()
            return send(app)
        with ThreadPoolExecutor(max_workers=2) as pool:
            results=list(pool.map(worker,range(2)))
        self.assertEqual([code for code,_ in results],[200,200])
        self.assertEqual(state,{'count':1,'calls':2})
        self.assertEqual(sorted(body['duplicado'] for _,body in results),[False,True])
        self.assertTrue(all(not body['ativou'] for _,body in results))

    def test_assinatura_invalida_antes_de_processar(self):
        calls=[]
        app=build_app(lambda pid:calls.append(pid) or {'ok':True})
        code,body=send(app,valid=False)
        self.assertEqual(code,401)
        self.assertEqual(calls,[])
        self.assertEqual(body['error'],'unauthorized')

    def test_falha_financeira_responde_503_sem_segredo(self):
        def fail(_): raise RuntimeError('V55_SENHA_PRIVADA_NAO_EXIBIR')
        code,body=send(build_app(fail))
        self.assertEqual(code,503)
        self.assertNotIn('V55_SENHA_PRIVADA_NAO_EXIBIR',str(body))

    def test_sem_confirmacao_retorna_503(self):
        code,body=send(build_app(lambda _:{'ok':False}))
        self.assertEqual(code,503)
        self.assertEqual(body['error'],'temporarily_unavailable')

if __name__=='__main__': unittest.main(verbosity=2)
