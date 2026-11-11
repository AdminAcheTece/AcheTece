"""V1.8 — monthly TESTUSER payer separation without changing company records."""
from unittest.mock import patch
import pytest
from billing_release import (
    BillingError, MercadoPagoClient, monthly_payer_email_for_checkout,
)

REGISTERED='malharia.exemplo@empresa.com.br'
DB='achetece_release_v1_test'
BASE={
    'ACHETECE_BILLING_ENV':'sandbox',
    'ACHETECE_HML_EXPECTED_DB_NAME':DB,
    'ACHETECE_MP_TEST_TOKEN_CONFIRMED':'1',
    'ACHETECE_BILLING_V1_ENABLED':'1',
    'ACHETECE_BILLING_CALLBACK_BASE_URL':'https://achetece-assinaturas-hml.onrender.com',
    'ACHETECE_HML_TEST_BUYER_USER_ID':'2564853871',
    'MP_COLLECTOR_ID':'2569815840',
    'ACHETECE_HML_MP_TEST_PAYER_EMAIL':'test@testuser.com',
}


def select(env=None, original=REGISTERED, db=DB):
    return monthly_payer_email_for_checkout(original, BASE if env is None else env,
                                             actual_database_name=db)


def test_switch_to_documented_mp_test_address():
    assert select() == 'test@testuser.com'


def test_original_email_remains_unchanged():
    customer={'email':REGISTERED}
    result=select(original=customer['email'])
    assert result=='test@testuser.com'
    assert customer['email']==REGISTERED


def test_no_override_keeps_historical_behavior():
    cfg={k:v for k,v in BASE.items() if k!='ACHETECE_HML_MP_TEST_PAYER_EMAIL'}
    assert select(cfg)==REGISTERED


@pytest.mark.parametrize('mode', ['production','PRODUCTION','', 'other'])
def test_production_and_other_non_sandbox_never_use_override(mode):
    cfg=dict(BASE, ACHETECE_BILLING_ENV=mode)
    assert select(cfg,db='achetece_db')==REGISTERED


@pytest.mark.parametrize('key,val', [
    ('ACHETECE_HML_EXPECTED_DB_NAME','other_db'),
    ('ACHETECE_HML_EXPECTED_DB_NAME',''),
    ('ACHETECE_MP_TEST_TOKEN_CONFIRMED','0'),
    ('ACHETECE_MP_TEST_TOKEN_CONFIRMED',''),
    ('ACHETECE_BILLING_V1_ENABLED','0'),
    ('ACHETECE_BILLING_CALLBACK_BASE_URL','https://www.achetece.com.br'),
    ('ACHETECE_BILLING_CALLBACK_BASE_URL','http://achetece-assinaturas-hml.onrender.com'),
    ('ACHETECE_HML_TEST_BUYER_USER_ID',''),
    ('ACHETECE_HML_TEST_BUYER_USER_ID','not-digits'),
    ('MP_COLLECTOR_ID',''),
    ('MP_COLLECTOR_ID','2564853871'),
])
def test_env_guards_fail_closed(key,val):
    cfg=dict(BASE,**{key:val})
    with pytest.raises(BillingError):select(cfg)


@pytest.mark.parametrize('db', ['','achetece_db','achetece_ed01_test','achetece2_staging_p03'])
def test_real_database_name_must_match(db):
    with pytest.raises(BillingError):select(db=db)


@pytest.mark.parametrize('email',[
    'test@gmail.com','test@company.com.br','TEST@testuser.com',
    'testuser@testuser.com','testuser@fake.com',
    'testuser123@other.com','testuser123@testuser.com',
    'testuser123456789@testuser.com.evil','test@testuser.com\nInjected: x',
    'test@testuser.com?to=evil','test@testuser.com/anything',
    'test@testuser.com@evil.com','random@testuser.com','test+secret@testuser.com',
    '', 'x'*100+'@testuser.com'
])
def test_disallowed_email_fails_closed(email):
    cfg=dict(BASE,ACHETECE_HML_MP_TEST_PAYER_EMAIL=email)
    if not email:
        assert select(cfg)==REGISTERED
    else:
        with pytest.raises(BillingError):select(cfg)


@pytest.mark.parametrize('email',['test@testuser.com','testuser123456789@testuser.com'])
def test_official_test_email_formats(email):
    cfg=dict(BASE,ACHETECE_HML_MP_TEST_PAYER_EMAIL=email)
    assert select(cfg)==email


def test_monthly_client_submits_only_selected_payer():
    payer=select()
    with patch.object(MercadoPagoClient,'_request',return_value={'id':'abc','init_point':'https://www.mercadopago.com.br/subscriptions/checkout'}) as req:
        MercadoPagoClient('DUMMY').create_monthly_subscription(
            email=payer, reference='achetece-v1-ref', cents=4700,
            return_url='https://achetece-assinaturas-hml.onrender.com/pagamento_pendente')
    args,kwargs=req.call_args
    assert args==('POST','/preapproval',{
        'reason':'AcheTece — assinatura mensal',
        'external_reference':'achetece-v1-ref',
        'payer_email':'test@testuser.com',
        'auto_recurring':{'frequency':1,'frequency_type':'months',
                          'transaction_amount':47.0,'currency_id':'BRL'},
        'back_url':'https://achetece-assinaturas-hml.onrender.com/pagamento_pendente',
        'status':'pending'
    })
    assert kwargs['idempotency_key']=='achetece-v1-ref'
    assert REGISTERED not in str(req.call_args)


def test_annual_not_touched():
    # The replacement is called only in monthly checkout. No annual SDK change.
    from pathlib import Path
    src=Path(__file__).resolve().parents[1].joinpath('main.py').read_text()
    assert "'payer':{'email': empresa.email}" in src
    assert 'monthly_payer_email_for_checkout' in src
    assert 'SELECT current_database()' in src


def test_registered_email_required():
    with pytest.raises(BillingError):select(original='')
