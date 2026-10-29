import os
import unittest
from unittest.mock import patch, Mock
from types import SimpleNamespace
from decimal import Decimal
from ed02_payment_contract_guard import verify_payment_against_contract, ContractGuardError

REF = 'achetece:1:' + 'a'*32

def payment(**changes):
    p = dict(id=123456, external_reference=REF, status='approved',
             currency_id='BRL', transaction_amount=2.0)
    p.update(changes)
    return p

class Db:
    def __init__(self, row=None, name='achetece_ed01_test', dialect='postgresql'):
        self.engine = SimpleNamespace(dialect=SimpleNamespace(name=dialect))
        self.row = row if row is not None else dict(empresa_id=1, external_reference=REF,
            plano='mensal', valor_esperado=Decimal('2.00'), moeda='BRL',
            status='preferencia_criada', provedor='mercadopago', preference_id='pref-test')
        self.name = name
        self.calls = 0
        self.session = self
    def execute(self, sql, args=None):
        self.calls += 1
        if 'current_database()' in str(sql):
            return SimpleNamespace(scalar_one=lambda: self.name)
        return SimpleNamespace(mappings=lambda: SimpleNamespace(one_or_none=lambda: self.row))

class GuardTests(unittest.TestCase):
    def setUp(self):
        self.patcher=patch.dict(os.environ, {'ED01_HOMOLOGACAO':'true'})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)
    def verify(self, p=None, db=None):
        return verify_payment_against_contract(db or Db(), p if p is not None else payment(), '123456')
    def test_valid(self):
        self.assertEqual(self.verify()['empresa_id'], 1)
    def test_bad_database(self):
        with self.assertRaises(ContractGuardError): self.verify(db=Db(name='achetece_db'))
    def test_bad_dialect(self):
        with self.assertRaises(ContractGuardError): self.verify(db=Db(dialect='sqlite'))
    def test_bad_environment(self):
        os.environ['ED01_HOMOLOGACAO']='false'
        db=Db()
        with self.assertRaises(ContractGuardError): self.verify(db=db)
        self.assertEqual(db.calls, 0)
    def test_missing_reference(self):
        with self.assertRaises(ContractGuardError): self.verify(payment(external_reference=''))
    def test_unregistered_reference(self):
        db=Db(); db.row=None
        with self.assertRaises(ContractGuardError): self.verify(db=db)
    def test_wrong_company(self):
        db=Db();db.row['empresa_id']=2
        with self.assertRaises(ContractGuardError): self.verify(db=db)
    def test_wrong_amount(self):
        with self.assertRaises(ContractGuardError): self.verify(payment(transaction_amount=1.99))
    def test_wrong_currency(self):
        with self.assertRaises(ContractGuardError): self.verify(payment(currency_id='USD'))
    def test_not_approved(self):
        with self.assertRaises(ContractGuardError): self.verify(payment(status='pending'))
    def test_wrong_payment_id(self):
        with self.assertRaises(ContractGuardError): self.verify(payment(id=987))
    def test_refunded(self):
        with self.assertRaises(ContractGuardError): self.verify(payment(transaction_amount_refunded=2))
    def test_wrong_status(self):
        db=Db();db.row['status']='criada'
        with self.assertRaises(ContractGuardError): self.verify(db=db)
    def test_missing_preference(self):
        db=Db();db.row['preference_id']=None
        with self.assertRaises(ContractGuardError): self.verify(db=db)
    def test_wrong_provider(self):
        db=Db();db.row['provedor']='other'
        with self.assertRaises(ContractGuardError): self.verify(db=db)
    def test_uses_contract_not_current_price(self):
        self.assertEqual(self.verify(payment(transaction_amount='2.00'))['valor'], Decimal('2.00'))

if __name__ == '__main__': unittest.main()
