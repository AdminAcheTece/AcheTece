"""Testes isolados de assinatura MP, sem rede e sem banco."""
import hashlib
import hmac
import unittest
from types import SimpleNamespace
from ed02_webhook_signature import validate_mp_signature, validate_mp_flask_request

SECRET = 'TEST_SECRET_NOT_REAL'
PID = '123456789'
RID = 'req-abc-123'
TS = '1780000000'

def signed(pid=PID, rid=RID, ts=TS, secret=SECRET):
    msg = f'id:{pid};request-id:{rid};ts:{ts};'
    return f'ts={ts},v1={hmac.new(secret.encode(), msg.encode(), hashlib.sha256).hexdigest()}'

def req(pid=PID, rid=RID, ts=TS, body_id=PID, sig=None):
    return SimpleNamespace(method='POST', args={'type': 'payment', 'data.id': pid},
       headers={'x-signature': signed(pid, rid, ts) if sig is None else sig, 'x-request-id': rid},
       get_json=lambda silent=False: {'type': 'payment', 'data': {'id': body_id}})

class MPAuthenticityTests(unittest.TestCase):
    def test_valid(self):
        self.assertTrue(validate_mp_flask_request(req(), SECRET))
    def test_tampered_query_id(self):
        r=req();r.args['data.id']='999';self.assertFalse(validate_mp_flask_request(r, SECRET))
    def test_tampered_request_id(self):
        r=req();r.headers['x-request-id']='another';self.assertFalse(validate_mp_flask_request(r, SECRET))
    def test_tampered_hash(self):
        self.assertFalse(validate_mp_flask_request(req(sig='ts='+TS+',v1='+'a'*64), SECRET))
    def test_wrong_secret(self):
        self.assertFalse(validate_mp_flask_request(req(), 'OTHER_SECRET'))
    def test_missing_signature(self):
        r=req();r.headers.pop('x-signature');self.assertFalse(validate_mp_flask_request(r, SECRET))
    def test_missing_id(self):
        r=req();r.args.pop('data.id');self.assertFalse(validate_mp_flask_request(r, SECRET))
    def test_body_query_mismatch(self):
        self.assertFalse(validate_mp_flask_request(req(body_id='222'), SECRET))
    def test_wrong_topic(self):
        r=req();r.args['type']='merchant_order';self.assertFalse(validate_mp_flask_request(r, SECRET))
    def test_duplicate_fields(self):
        self.assertFalse(validate_mp_flask_request(req(sig=signed()+',v1='+'a'*64), SECRET))
    def test_missing_secret(self):
        self.assertFalse(validate_mp_signature(secret='', x_signature=signed(), x_request_id=RID, data_id=PID))
    def test_invalid_timestamp(self):
        self.assertFalse(validate_mp_flask_request(req(sig=signed(ts='n/a')), SECRET))

if __name__ == '__main__':
    unittest.main(verbosity=2)
