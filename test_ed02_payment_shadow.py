import unittest
from ed02_payment_shadow import extract_verified_record

OK = {'id':12345,'external_reference':'achetece:1:abc123','status':'approved', 'currency_id':'BRL','transaction_amount':2.00}
class VerifiedRecordTests(unittest.TestCase):
    def test_valid(self):
        row=extract_verified_record(OK,1)
        self.assertEqual(row['payment_id'],'12345')
    def test_foreign_company(self):
        with self.assertRaises(ValueError): extract_verified_record(OK,2)
    def test_email_fallback_is_not_proof(self):
        with self.assertRaises(ValueError): extract_verified_record({**OK,'external_reference':''},1)
    def test_foreign_currency(self):
        with self.assertRaises(ValueError): extract_verified_record({**OK,'currency_id':'USD'},1)
    def test_invalid_id(self):
        with self.assertRaises(ValueError): extract_verified_record({**OK,'id':'nonnumeric'},1)
    def test_negative_amount(self):
        with self.assertRaises(ValueError): extract_verified_record({**OK,'transaction_amount':-2},1)
if __name__=='__main__': unittest.main()
