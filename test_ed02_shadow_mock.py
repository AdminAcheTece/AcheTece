"""ED02.3B: testes totalmente isolados, sem rede, banco ou Mercado Pago."""
import unittest
from unittest.mock import patch
from ed02_payment_shadow import shadow_record_payment, extract_verified_record

PAYMENT = {
    'id': 987654321, 'external_reference': 'achetece:1:teste-ed02',
    'status': 'approved', 'currency_id': 'BRL', 'transaction_amount': '2.00',
}

class _Result:
    def __init__(self, value): self.value = value
    def scalar_one(self): return self.value
    def first(self): return self.value

class _Conn:
    def __init__(self, database):
        self.database = database
        self.events = {}
        self.commands = []
    def execute(self, stmt, params=None):
        sql = str(stmt)
        self.commands.append(sql)
        if 'current_database()' in sql:
            return _Result(self.database)
        if 'INSERT INTO public.pagamento_evento' in sql:
            key = (params['payment_id'], params['empresa_id'], params['external_reference'])
            old = self.events.get(key, 0)
            self.events[key] = old + 1
            return _Result((1, old + 1))
        raise AssertionError('SQL inesperado no teste')

class _Session:
    def __init__(self, conn): self.conn = conn; self.commits = 0
    def connection(self): return self.conn
    def commit(self): self.commits += 1

class _Db:
    def __init__(self, database):
        self.conn = _Conn(database)
        self.session = _Session(self.conn)

class ShadowMockTests(unittest.TestCase):
    def test_duas_notificacoes_mesmo_identificador_uma_chave(self):
        db = _Db('achetece_ed01_test')
        first = shadow_record_payment(db, PAYMENT, 1)
        second = shadow_record_payment(db, PAYMENT, 1)
        self.assertEqual(first['recebimentos'], 1)
        self.assertEqual(second['recebimentos'], 2)
        self.assertEqual(len(db.conn.events), 1)
        self.assertTrue(first['somente_observacao'])
        self.assertEqual(db.session.commits, 2)
        self.assertTrue(all('UPDATE public.empresa' not in s for s in db.conn.commands))
        self.assertTrue(all('INSERT INTO public.assinatura_ciclo' not in s for s in db.conn.commands))

    def test_producao_bloqueada_sem_gravacao(self):
        db = _Db('achetece_db')
        with self.assertRaises(RuntimeError):
            shadow_record_payment(db, PAYMENT, 1)
        self.assertEqual(db.conn.events, {})
        self.assertEqual(db.session.commits, 0)

    def test_empresa_incompatível_bloqueada(self):
        db = _Db('achetece_ed01_test')
        with self.assertRaises(ValueError):
            shadow_record_payment(db, PAYMENT, 2)
        self.assertEqual(db.session.commits, 0)

    def test_moeda_invalida_bloqueada(self):
        with self.assertRaises(ValueError):
            extract_verified_record({**PAYMENT, 'currency_id': 'USD'}, 1)

    def test_id_invalido_bloqueado(self):
        with self.assertRaises(ValueError):
            extract_verified_record({**PAYMENT, 'id': 'abc'}, 1)

    def test_sem_envio_http(self):
        # O módulo usa apenas SQLAlchemy; não faz nenhuma chamada HTTP.
        import inspect, ed02_payment_shadow
        source = inspect.getsource(ed02_payment_shadow)
        self.assertNotIn('requests.get(', source)
        self.assertNotIn('requests.post(', source)

if __name__ == '__main__': unittest.main()
