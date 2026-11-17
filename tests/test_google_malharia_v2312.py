"""HML V2.3.12: isolated Google-mill auth tests; no network or database."""
import ast
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

SOURCE = Path(__file__).resolve().parents[1] / 'main.py'
parsed = ast.parse(SOURCE.read_text(encoding='utf-8'), filename=str(SOURCE))
functions = [n for n in parsed.body if isinstance(n, ast.FunctionDef) and n.name in {'_google_malharia_identity_ok','oauth_google_callback'}]
assert len(functions) == 2
for n in functions: n.decorator_list=[]
code=compile(ast.fix_missing_locations(ast.Module(body=functions,type_ignores=[])),str(SOURCE),'exec')

class AuthTests(unittest.TestCase):
    def setUp(self):
        self.claims={'email':'gestao.achetece@gmail.com','email_verified':True,'sub':'google-verified-3','name':'Julia'}
        self.emp=SimpleNamespace(id=3,email=self.claims['email'],user_id=3,nome='Malharia HML 03')
        self.user=SimpleNamespace(id=3,email=self.claims['email'],google_id=None,role=None,is_active=True)
        self.other=None
        self.sess={'oauth_ctx':'empresa','oauth_next':'https://hostile.invalid'}
        self.flash=Mock()
        self.commit=Mock()
        self.rollback=Mock()
        self.oauth=SimpleNamespace(google=SimpleNamespace(authorize_access_token=Mock(return_value={'access_token':'secret'}),get=Mock(return_value=SimpleNamespace(json=Mock(return_value=self.claims)))))
        self.company_query=Mock()
        self.company_query.filter.return_value.first.side_effect=lambda:self.emp
        self.user_query=Mock()
        self.user_query.filter_by.return_value.first.side_effect=lambda:self.other
        self.db=SimpleNamespace(session=SimpleNamespace(get=Mock(side_effect=lambda cls,k:self.user),commit=self.commit,rollback=self.rollback))
        self.scope={
            'oauth':self.oauth,'session':self.sess,'flash':self.flash,'db':self.db,
            'Empresa':SimpleNamespace(query=self.company_query,email='email_field'), 'Usuario':SimpleNamespace(query=self.user_query),
            'func':SimpleNamespace(lower=Mock(return_value='lower')), 'email':None,
            'request':SimpleNamespace(), 'current_app':SimpleNamespace(logger=SimpleNamespace(warning=Mock(),exception=Mock())),
            'url_for':lambda name,**kw:'/route/'+name,
            'redirect':lambda dest:('redirect',dest),
        }
        exec(code,self.scope)
    def allowed(self):
        return self.scope['_google_malharia_identity_ok'](self.claims,self.emp,self.user,self.other)
    def callback(self):
        return self.scope['oauth_google_callback']()
    def assertDenied(self):
        self.assertFalse(self.allowed())
        result=self.callback()
        self.assertEqual(result,('redirect','/route/login'))
        self.assertNotIn('empresa_id',self.sess)
        self.commit.assert_not_called()
    def test_first_login_links_verified_identity(self):
        self.assertTrue(self.allowed())
        self.assertEqual(self.callback(),('redirect','/route/painel_malharia'))
        self.assertEqual(self.user.google_id,'google-verified-3')
        self.assertEqual(self.sess['empresa_id'],3)
        self.commit.assert_called_once()
    def test_repeat_login_no_second_write(self):
        self.user.google_id='google-verified-3'
        self.assertTrue(self.allowed())
        self.assertEqual(self.callback(),('redirect','/route/painel_malharia'))
        self.commit.assert_not_called()
    def test_unverified_email_denied(self): self.claims['email_verified']=False;self.assertDenied()
    def test_missing_email_verified_denied(self): self.claims.pop('email_verified');self.assertDenied()
    def test_unverified_string_true_denied(self): self.claims['email_verified']='true';self.assertDenied()
    def test_missing_subject_denied(self): self.claims['sub']='';self.assertDenied()
    def test_subject_bound_other_user_denied(self): self.other=SimpleNamespace(id=7);self.assertDenied()
    def test_different_subject_existing_user_denied(self): self.user.google_id='different';self.assertDenied()
    def test_inactive_user_denied(self): self.user.is_active=False;self.assertDenied()
    def test_admin_role_denied(self): self.user.role='admin';self.assertDenied()
    def test_client_role_denied(self): self.user.role='cliente';self.assertDenied()
    def test_no_user_binding_denied(self): self.emp.user_id=None;self.assertDenied()
    def test_wrong_user_binding_denied(self): self.emp.user_id=72;self.assertDenied()
    def test_user_email_mismatch_denied(self): self.user.email='other@example.org';self.assertDenied()
    def test_company_email_mismatch_denied(self): self.emp.email='other@example.org';self.assertDenied()
    def test_google_email_mismatch_denied(self): self.claims['email']='other@example.org';self.assertDenied()
    def test_invalid_google_email_denied(self): self.claims['email']='not-an-email';self.assertDenied()
    def test_not_found_company_does_not_create_session(self):
        self.emp=None
        self.assertEqual(self.callback(),('redirect','/route/cadastrar_empresa'))
        self.assertNotIn('empresa_id',self.sess)
    def test_commit_failure_does_not_create_session(self):
        self.commit.side_effect=RuntimeError('db down')
        self.assertEqual(self.callback(),('redirect','/route/login'))
        self.assertNotIn('empresa_id',self.sess)
        self.rollback.assert_called_once()
    def test_redirect_external_ignored(self):
        self.assertEqual(self.callback(),('redirect','/route/painel_malharia'))
    def test_no_override_verified_identity(self):
        self.user.google_id='google-verified-3'
        self.assertEqual(self.callback(),('redirect','/route/painel_malharia'))
        self.commit.assert_not_called()

if __name__ == '__main__': unittest.main()
