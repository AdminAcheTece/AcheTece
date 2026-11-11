"""V1.9: existing approved annual buyer is authoritative for HML payer.

No network request, e-mail from real accounts, token or DB mutation in tests.
"""
from copy import deepcopy
from pathlib import Path
from unittest.mock import patch
import pytest
from billing_release import (
    BillingError, MercadoPagoClient, verified_monthly_hml_payer_from_payment,
)

SOURCE_PAYMENT_ID='187868512909'
SOURCE_REF='achetece-v1-verified-test-ref'
SELLER='2569815840'
BUYER='2564853871'
VALID_EMAIL='test_user_8310402@testuser.com'
AMOUNT=47940
PAYMENT={
    'id':int(SOURCE_PAYMENT_ID), 'status':'approved',
    'collector_id':int(SELLER), 'payer':{'id':int(BUYER),'email':VALID_EMAIL},
    'currency_id':'BRL', 'transaction_amount':479.40,
    'external_reference':SOURCE_REF,
}


def check(obj=None, **params):
    d={'source_payment_id':SOURCE_PAYMENT_ID,
       'source_reference':SOURCE_REF,
       'source_amount_cents':AMOUNT,
       'expected_collector_id':SELLER,
       'expected_buyer_id':BUYER}
    d.update(params)
    return verified_monthly_hml_payer_from_payment(PAYMENT if obj is None else obj, **d)


def test_valid_buyer_from_approved_transaction():
    assert check()==VALID_EMAIL


def test_email_case_normalized():
    p=deepcopy(PAYMENT)
    p['payer']['email']=VALID_EMAIL.upper()
    assert check(p)==VALID_EMAIL


@pytest.mark.parametrize('attr,value',[
    ('id','187868512910'),('status','pending'),('status','refunded'),
    ('collector_id','12345'),('currency_id','ARS'),
    ('transaction_amount',479.41),('transaction_amount',470.0),
    ('external_reference','another-contract'),
])
def test_mismatched_source_payment_fail_closed(attr,value):
    p=deepcopy(PAYMENT);p[attr]=value
    with pytest.raises(BillingError):check(p)


@pytest.mark.parametrize('email',[
    None,'','test@testuser.com','test@company.com.br','anyname@testuser.com',
    'test_user_8310402@testuser.com.evil',
    'test_user_8310402@testuser.com\nInjected:true',
    'test_user_8310402@testuser.com@evil.com',
    'TESTUSER_8310402@example.com','foo+test@testuser.com',
])
def test_unsafe_or_fake_payer_email_rejected(email):
    p=deepcopy(PAYMENT);p['payer']['email']=email
    with pytest.raises(BillingError):check(p)


def test_unknown_buyer_id_rejected():
    p=deepcopy(PAYMENT);p['payer']['id']=12345
    with pytest.raises(BillingError):check(p)


@pytest.mark.parametrize('obj',[{},[],{'payer':{}}, {'id':'187868512909'}])
def test_missing_source_payload_rejected(obj):
    with pytest.raises(BillingError):check(obj)


def test_none_payment_rejected():
    with pytest.raises(BillingError):
        verified_monthly_hml_payer_from_payment(None,
            source_payment_id=SOURCE_PAYMENT_ID, source_reference=SOURCE_REF,
            source_amount_cents=AMOUNT, expected_collector_id=SELLER,
            expected_buyer_id=BUYER)


def test_invalid_expected_source_payment_id_rejected():
    with pytest.raises(BillingError):check(source_payment_id='not_a_payment')


def test_customer_email_never_modified_or_saved():
    company={'email':'malharia.teste@company.com.br'}
    source=deepcopy(PAYMENT)
    payer=check(source)
    assert company['email']=='malharia.teste@company.com.br'
    assert source['payer']['email']==VALID_EMAIL
    assert payer != company['email']


def test_only_authenticated_get_payment_is_used():
    with patch.object(MercadoPagoClient,'_request', return_value=deepcopy(PAYMENT)) as request:
        remote=MercadoPagoClient('DUMMY').get_payment(SOURCE_PAYMENT_ID)
    assert request.call_args.args==('GET',f'/v1/payments/{SOURCE_PAYMENT_ID}')
    assert check(remote)==VALID_EMAIL


def test_main_guards_prior_annual_payment_and_is_monthly_only():
    text=Path(__file__).resolve().parents[1].joinpath('main.py').read_text()
    assert 'ACHETECE_HML_MP_TEST_PAYER_SOURCE_PAYMENT_ID' in text
    assert "source_payment = BillingPayment.query.filter_by(" in text
    assert "provider_payment_id=source_payment_id, status='approved'" in text
    assert 'source_intent.plano != \'anual\'' in text
    assert "source_intent.empresa_id == empresa.id" in text
    assert 'client.get_payment(source_payment_id)' in text
    assert 'verified_monthly_hml_payer_from_payment(' in text
    assert "'payer':{'email': empresa.email}" in text # annual path remains unchanged
    assert 'payer_email = monthly_payer_email_for_checkout(' in text
