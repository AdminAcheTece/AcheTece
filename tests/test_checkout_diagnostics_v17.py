"""V1.7: safe reason and read-only credential/Subscriptions API check."""
import json
from unittest.mock import patch
import requests
import pytest
from billing_release import (
    MercadoPagoClient, ProviderError, safe_mp_message_hint,
    _safe_provider_response_details,
)


def fake_response(status, body, content_type='application/json'):
    r = requests.Response()
    r.status_code = status
    r.url = 'https://api.mercadopago.com/preapproval'
    r.headers['Content-Type'] = content_type
    r._content = (json.dumps(body) if isinstance(body, (dict,list)) else body).encode()
    return r


def call_failed_checkout(response):
    with patch('billing_release.requests.request', return_value=response):
        with pytest.raises(ProviderError) as err:
            MercadoPagoClient('TOKEN-SUPER-SECRETO').create_monthly_subscription(
                email='pessoa@dominio.com.br', reference='achetece-v1-secret-ID',
                cents=4700, return_url='https://hml.exemplo.test/pagamento_pendente'
            )
    return err.value


def test_filtered_message_finds_specific_payer_email_problem():
    err = call_failed_checkout(fake_response(400, {
        'message':'Invalid payer_email: pessoa@dominio.com.br cannot create subscription for APP_USR-SECRET',
        'cause':[{'code':'invalid_payer_email', 'description':'Payer email is not valid for this test account TESTUSER-SECRET'}],
    }))
    assert err.http_status == 400
    assert 'payer_email' in err.message_hint
    assert 'invalid' in err.message_hint
    assert 'test' in err.cause_hint
    assert err.cause_code == 'invalid_payer_email'
    assert err.response_kind == 'json_object'
    for secret in ['pessoa','dominio','TOKEN','APP_USR','SECRET','TESTUSER','achetece-v1','exemplo.test']:
        assert secret.lower() not in repr(err.__dict__).lower()


def test_error_nested_and_plain_text_hints():
    e = call_failed_checkout(fake_response(400, {'error': {'code':'bad_request','message':'Operation not allowed to create preapproval with test credentials'}}))
    assert e.provider_code == 'bad_request'
    assert 'preapproval' in e.message_hint
    e2 = call_failed_checkout(fake_response(403, 'Access denied - seller account disabled [PRIVATE_NAME] email abc@dominio.com', 'text/plain'))
    assert e2.response_kind == 'text_plain'
    assert 'seller account disabled' in e2.message_hint
    assert 'PRIVATE_NAME' not in repr(e2.__dict__)


def test_never_logs_raw_unknown_names_ids_or_secrets():
    raw = 'Vandre Stein +5547999999999 12345678909 user@site.com token=TEST-999 APP_USR-12345 kind ATTACKER_PRIVATE'
    assert safe_mp_message_hint(raw) == 'token'
    assert safe_mp_message_hint('') == 'indisponivel'
    assert safe_mp_message_hint({'message':'private'}) == 'indisponivel'
    assert safe_mp_message_hint('testuser.com financeiro.empresa@gmail.com') == 'indisponivel'


def test_provider_can_return_array_error():
    e = call_failed_checkout(fake_response(400, [{'code':'bad_request','message':'Invalid currency in subscription'}]))
    assert e.response_kind == 'json_list'
    assert e.provider_code == 'bad_request'
    assert e.message_hint == 'invalid currency subscription'


def test_malformed_response_fails_closed():
    e = call_failed_checkout(fake_response(400, '<h1>Invalid payer name SAMPLE</h1>', 'text/html'))
    assert e.message_hint == 'indisponivel'
    assert e.response_kind == 'non_json'
    assert 'SAMPLE' not in repr(e.__dict__)


def test_read_only_probe_returns_only_http_code():
    response = fake_response(200, {'results':[{'payer_email':'secret@site.com','id':'SECRET123'}]})
    with patch('billing_release.requests.request', return_value=response) as req:
        status = MercadoPagoClient('PRIVATE-TOKEN').subscription_api_access_status()
    assert status == '200'
    assert req.call_args.args[0] == 'GET'
    assert req.call_args.args[1] == 'https://api.mercadopago.com/preapproval/search'
    assert req.call_args.kwargs['timeout'] <= 8
    assert 'PRIVATE-TOKEN' not in status
    for key in ('json','data','params'):
        assert key not in req.call_args.kwargs


@pytest.mark.parametrize('http', [400,401,403,404,429,500])
def test_probe_http_status(http):
    with patch('billing_release.requests.request', return_value=fake_response(http, {'message':'PRIVATE'})):
        assert MercadoPagoClient('dummy').subscription_api_access_status() == str(http)


def test_probe_network_failure_is_safe():
    with patch('billing_release.requests.request', side_effect=requests.Timeout('KEY=PRIVATE')):
        assert MercadoPagoClient('dummy').subscription_api_access_status() == 'falha_rede'


def test_diagnostic_does_not_affect_payment_verification():
    from billing_release import check_verified_payment
    with pytest.raises(Exception):
        check_verified_payment({'id':'1','status':'approved'}, expected_payment_id='1',
            expected_reference='x',expected_cents=4700, expected_collector='9')
