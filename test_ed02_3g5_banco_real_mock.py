"""ED02.3G.5: testes isolados da garantia do banco REAL antes do webhook.
Nunca conecta a PostgreSQL nem ao Mercado Pago.
"""
import unittest
from unittest.mock import Mock
import test_ed02_3g4_assinatura_webhook_mock as prior
ENV = prior.ENV
webhook = prior.webhook
from types import SimpleNamespace

class RealDatabaseGuardTests(unittest.TestCase):
    def setUp(self):
        prior.SignatureIntegrationTests.setUp(self)

    req = prior.SignatureIntegrationTests.req
    def test_real_db_wrong_blocks_with_matching_env(self):
        ENV["db"].session.execute.return_value.scalar_one = lambda: "achetece_db"
        ENV["request"] = self.req()
        payload, status = webhook()
        self.assertEqual(status, 503)
        self.assertEqual(payload["error"], "temporarily_unavailable")
        self.process.assert_not_called()

    def test_real_db_sqlite_blocks(self):
        ENV["db"].engine.dialect.name = "sqlite"
        ENV["request"] = self.req()
        _, status = webhook()
        self.assertEqual(status, 503)
        self.process.assert_not_called()

    def test_real_db_query_error_blocks(self):
        ENV["db"].session.execute.side_effect = RuntimeError("internal sensitive error")
        ENV["request"] = self.req()
        result, status = webhook()
        self.assertEqual(status, 503)
        self.assertNotIn("internal sensitive error", str(result))
        self.process.assert_not_called()

    def test_database_checked_before_signature_and_processing(self):
        ENV["db"].session.execute.return_value.scalar_one = lambda: "achetece_ed01_test"
        ENV["request"] = self.req()
        _, status = webhook()
        self.assertEqual(status, 200)
        ENV["db"].session.execute.assert_called_once()
        self.process.assert_called_once_with("123456")

if __name__ == '__main__':
    unittest.main(verbosity=2)
