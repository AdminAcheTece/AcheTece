import ast
import hmac
import hashlib
import os
from pathlib import Path
from types import SimpleNamespace
from unittest import TestCase, main
from unittest.mock import Mock, patch

ROOT = Path(__file__).resolve().parent
TREE = ast.parse((ROOT / "main.py").read_text(encoding="utf-8"))
FUNC = next(n for n in TREE.body if isinstance(n, ast.FunctionDef) and n.name == "webhook")
FUNC.decorator_list = []
ENV = {"os": os}
exec(compile(ast.Module(body=[FUNC], type_ignores=[]), "main.py", "exec"), ENV)
webhook = ENV["webhook"]


class SignatureIntegrationTests(TestCase):
    def setUp(self):
        self.process = Mock(return_value={"ok": True, "empresa_id": 1})
        self.extract = Mock(return_value=("123456", {"data": {"id": "123456"}}))
        self.logger = SimpleNamespace(info=Mock(), warning=Mock(), error=Mock(), exception=Mock())
        ENV.update(app=SimpleNamespace(logger=self.logger), jsonify=lambda item: item,
                   _processar_pagamento=self.process, _extract_payment_id=self.extract)
        self.env = patch.dict(os.environ, {"ED02_WEBHOOK_SIGNATURE_HOMOLOG": "true",
                           "ED01_HOMOLOGACAO": "true",
                           "ED02_HOMOLOG_DATABASE_NAME": "achetece_ed01_test",
                           "ED02_MP_WEBHOOK_SECRET": "segredo-ficticio"}, clear=False)
        self.env.start()
        self.addCleanup(self.env.stop)

    def req(self, *, valid=True, data_id="123456", body_id="123456", req_id="abc-111"):
        import time
        ts = str(int(time.time()))
        digest = hmac.new(b"segredo-ficticio",
            f"id:{data_id};request-id:{req_id};ts:{ts};".encode(), hashlib.sha256).hexdigest()
        if not valid: digest = "f"*64 if digest != "f"*64 else "e"*64
        return SimpleNamespace(method="POST", args={"type": "payment", "data.id": data_id},
            headers={"x-request-id": req_id, "x-signature": f"ts={ts},v1={digest}"},
            get_json=lambda silent=True: {"type": "payment", "data": {"id": body_id}})

    def test_valid_signature_routes_payment(self):
        ENV["request"] = self.req()
        resp, status = webhook()
        self.assertEqual(status, 200)
        self.process.assert_called_once_with("123456")

    def test_invalid_signature_blocks_before_processing(self):
        ENV["request"] = self.req(valid=False)
        resp, status = webhook()
        self.assertEqual(status, 401)
        self.process.assert_not_called()
        self.extract.assert_not_called()

    def test_mismatched_body_and_query_blocks(self):
        ENV["request"] = self.req(body_id="999999")
        _, status = webhook()
        self.assertEqual(status, 401)
        self.process.assert_not_called()

    def test_missing_secret_fails_closed(self):
        os.environ.pop("ED02_MP_WEBHOOK_SECRET", None)
        ENV["request"] = self.req()
        resp, status = webhook()
        self.assertEqual(status, 503)
        self.process.assert_not_called()

    def test_homologation_guard_blocks_other_database(self):
        os.environ["ED02_HOMOLOG_DATABASE_NAME"] = "achetece_db"
        ENV["request"] = self.req()
        _, status = webhook()
        self.assertEqual(status, 503)
        self.process.assert_not_called()

    def test_disabled_flag_preserves_legacy(self):
        os.environ["ED02_WEBHOOK_SIGNATURE_HOMOLOG"] = "false"
        ENV["request"] = SimpleNamespace(method="POST")
        _, status = webhook()
        self.assertEqual(status, 200)
        self.process.assert_called_once()


if __name__ == "__main__":
    main(verbosity=2)
