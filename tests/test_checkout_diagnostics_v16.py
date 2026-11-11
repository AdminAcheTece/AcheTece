"""HML V1.6 — provider diagnostics without secrets or payer information."""
from unittest.mock import patch
import requests
import pytest
from billing_release import MercadoPagoClient, ProviderError, _safe_provider_code


def _resp(code, payload):
    r = requests.Response()
    r.status_code = code
    r.url = 'https://api.mercadopago.com/preapproval'
    r._content = payload.encode('utf-8')
    return r


def test_sanitizes_machine_codes():
    assert _safe_provider_code('bad_request') == 'bad_request'
    assert _safe_provider_code(123) == '123'
    assert _safe_provider_code('the email test@domain.com was rejected') == 'unspecified'
    assert _safe_provider_code('APP_USR-secret') == 'unspecified'
    assert _safe_provider_code(None) == 'unspecified'
    assert _safe_provider_code({'email': 'person@site.com'}) == 'unspecified'
    assert _safe_provider_code('x'*70) == 'unspecified'


def test_http_error_captures_only_machine_codes():
    r = _resp(400, '{"error":"bad_request","message":"Test payer_email test@site.com token TEST-SECRET","cause":[{"code":"invalid_payer_email","description":"PRIVATE"}]}')
    client = MercadoPagoClient('dummy')
    with patch('billing_release.requests.request', return_value=r):
        with pytest.raises(ProviderError) as info:
            client.create_monthly_subscription(email='test@site.com',reference='test123',cents=4700,return_url='https://hml.example.org/pagamento_pendente')
    e = info.value
    assert e.http_status == 400
    assert e.provider_code == 'bad_request'
    assert e.cause_code == 'invalid_payer_email'
    for dangerous in ('test@site.com','TEST-SECRET','PRIVATE'):
        assert dangerous not in str(e)
        assert dangerous not in str(e.__dict__)


def test_no_json_http_response_is_safe():
    r = _resp(403, '<html>PRIVATE HTML</html>')
    with patch('billing_release.requests.request', return_value=r):
        with pytest.raises(ProviderError) as info:
            MercadoPagoClient('dummy').create_monthly_subscription(
                email='fake@example.com',reference='test456',cents=4700,
                return_url='https://hml.example.org/pagamento_pendente')
    assert info.value.http_status == 403
    assert info.value.provider_code == 'unspecified'
    assert 'PRIVATE' not in str(info.value)


def test_network_error_contains_no_exception_details():
    with patch('billing_release.requests.request', side_effect=requests.ConnectionError('secret=TOKEN&email=test@example.org')):
        with pytest.raises(ProviderError) as info:
            MercadoPagoClient('dummy').create_monthly_subscription(
                email='fake@example.com',reference='test789',cents=4700,
                return_url='https://hml.example.org/pagamento_pendente')
    assert info.value.http_status is None
    assert 'TOKEN' not in str(info.value)
