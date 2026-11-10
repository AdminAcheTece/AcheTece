"""Static invariants: testable without provider credentials or Flask import."""
import ast
import re
import unittest
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
SOURCE=(ROOT/'main.py').read_text(encoding='utf-8')
TREE=ast.parse(SOURCE)

def func(name):
    return next(n for n in TREE.body if isinstance(n,(ast.FunctionDef,ast.AsyncFunctionDef)) and n.name==name)

class TestReleaseStructure(unittest.TestCase):
    def test_app_syntax(self):
        self.assertIsNotNone(TREE)
    def test_no_dev_secret_fallback(self):
        self.assertNotIn("os.getenv('SECRET_KEY', 'dev-key-unsafe')",SOURCE)
        self.assertIn("len(_release_secret) < 32",SOURCE)
    def test_checkout_requires_csrf_post_for_provider_creation(self):
        checkout=func('checkout')
        self.assertIn("request.method == 'GET'",ast.get_source_segment(SOURCE, checkout))
        self.assertIn('csrf_token()', (ROOT/'templates/checkout_confirmacao.html').read_text())
    def test_webhook_checks_signature_and_provider(self):
        hook=func('webhook')
        code=ast.get_source_segment(SOURCE,hook)
        self.assertIn('verify_webhook_signature(',code)
        self.assertIn('_processar_pagamento(',code)
    def test_callback_cannot_grant_rights(self):
        for name in ('pagamento_aprovado','pagamento_sucesso'):
            self.assertNotIn('_processar_pagamento',ast.get_source_segment(SOURCE,func(name)))
    def test_admin_status_only_post_and_forbidden(self):
        node=func('admin_editar_status')
        decorators=' '.join(ast.get_source_segment(SOURCE,d) for d in node.decorator_list)
        self.assertNotIn('GET',decorators)
        self.assertIn('abort(403)',ast.get_source_segment(SOURCE,node))
    def test_ledger_schema_tables(self):
        for name in ('BillingIntent','BillingPayment'):
            self.assertTrue(any(isinstance(n,ast.ClassDef) and n.name==name for n in TREE.body))
    def test_no_staging_guard_import(self):
        self.assertNotIn('p03d_staging_guard',SOURCE)
    def test_city_filter_has_no_browser_ibge_dependency(self):
        front=(ROOT/'templates/index.html').read_text()
        self.assertNotIn('servicodados.ibge.gov.br',front)
        self.assertIn('cidades_ativas',front)
    def test_all_post_forms_have_csrf(self):
        # opening/closing form count is reasonably regular in these templates
        for p in (ROOT/'templates').glob('*.html'):
            s=p.read_text()
            for match in re.finditer(r'<form\b[^>]*>.*?</form>',s,re.I|re.S):
                open_tag=re.match(r'<form\b[^>]*>',match.group(0),re.I|re.S).group(0)
                if re.search(r'\bmethod\s*=\s*["\']?post\b',open_tag,re.I):
                    with self.subTest(file=p.name):
                        self.assertIn('csrf_token()',match.group(0))

if __name__=='__main__':unittest.main()
