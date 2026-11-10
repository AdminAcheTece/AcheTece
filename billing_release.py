"""AcheTece release billing helpers — independent from Flask and the database.

No provider request is made on module import. Money always uses integer cents.
Keep callbacks strictly read-only with respect to payment authorization: only a
verified provider GET + local reconciled contract may grant access.
"""
from __future__ import annotations

from calendar import monthrange
from datetime import datetime, timedelta, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
import hashlib
import hmac
import re
import secrets

import requests


class BillingError(ValueError):
    pass


class ProviderError(RuntimeError):
    pass


def money_cents(value) -> int:
    try:
        amount = Decimal(str(value))
    except (InvalidOperation, ValueError, TypeError) as exc:
        raise BillingError('Valor monetário inválido') from exc
    if not amount.is_finite() or amount <= 0 or amount.as_tuple().exponent < -2:
        raise BillingError('Valor monetário não positivo ou com precisão inválida')
    cents = int((amount * 100).to_integral_exact())
    if not 0 < cents < 1000000000:
        raise BillingError('Valor monetário fora de limite')
    return cents


def credential_environment_allowed(access_token: str, mode: str, *,
                                   test_credential_confirmed: bool = False) -> bool:
    """Fail closed for token-environment selection.

    Some Mercado Pago products issue APP_USR-prefixed *test* tokens, so prefix
    alone is not proof of live/test mode. In sandbox, those tokens require an
    explicit operator declaration, made only after checking the Test tab in
    the Mercado Pago developer dashboard. Payment validation must additionally
    reject live_mode=True in sandbox, before granting any entitlement.
    """
    token = str(access_token or '').strip()
    selected = str(mode or '').strip().lower()
    if selected == 'sandbox':
        return token.startswith('TEST-') or (
            token.startswith('APP_USR-') and bool(test_credential_confirmed)
        )
    if selected == 'production':
        return token.startswith('APP_USR-')
    return False


def price_cents_from_environment(env, plan: str) -> int:
    if plan not in ('mensal', 'anual'):
        raise BillingError('Plano inválido')
    key = 'ACHETECE_PRICE_MONTHLY_BRL' if plan == 'mensal' else 'ACHETECE_PRICE_YEARLY_BRL'
    raw = env.get(key)
    if raw is None or not str(raw).strip():
        raise BillingError(f'{key} precisa ser configurada')
    return money_cents(raw)


def fmt_brl(cents: int) -> str:
    return f'{cents / 100:,.2f}'.replace(',', '_').replace('.', ',').replace('_', '.')


def add_months(base: datetime, months: int) -> datetime:
    if months < 1:
        raise BillingError('Período inválido')
    index = base.year * 12 + base.month - 1 + months
    year, month_zero = divmod(index, 12)
    month = month_zero + 1
    return base.replace(year=year, month=month, day=min(base.day, monthrange(year, month)[1]))


def next_expiration(start: datetime, plan: str) -> datetime:
    if plan == 'mensal':
        return add_months(start, 1)
    if plan == 'anual':
        return add_months(start, 12)
    raise BillingError('Plano inválido')


def verify_webhook_signature(signature: str, request_id: str, data_id: str,
                             secret: str, *, now: datetime | None = None,
                             max_age_seconds: int = 900) -> bool:
    """Mercado Pago signature format per current developers docs.

    manifest: id:<data.id lower>;request-id:<X-Request-Id>;ts:<ts>;
    Provider timestamp is milliseconds since epoch; do not log the signature.
    """
    if not all((signature, request_id, data_id, secret)):
        return False
    attrs = dict(fragment.strip().split('=', 1) for fragment in signature.split(',') if '=' in fragment)
    ts, received = attrs.get('ts', ''), attrs.get('v1', '')
    if not ts.isdigit() or not re.fullmatch(r'[0-9a-fA-F]{64}', received):
        return False
    now = now or datetime.now(timezone.utc)
    try:
        ts_sec = int(ts)
        # MP documentation uses milliseconds, with older integration examples
        # using seconds. Accept only exactly documented milliseconds in v1.
        if abs(now.timestamp() - ts_sec / 1000) > max_age_seconds:
            return False
    except (OverflowError, ValueError):
        return False
    manifest = f'id:{data_id.lower()};request-id:{request_id};ts:{ts};'
    expected = hmac.new(secret.encode('utf-8'), manifest.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(received.lower(), expected)


def check_verified_payment(payment: dict, *, expected_payment_id: str,
                           expected_reference: str, expected_cents: int,
                           expected_collector: str, expected_currency: str = 'BRL',
                           expected_subscription: str | None = None,
                           expected_live_mode: bool | None = None) -> str:
    """Validate the response from authenticated GET /v1/payments/{id}.

    Never infer a company from payer email or an untrusted URL. Reject missing
    fields rather than granting access on incomplete provider responses.
    """
    if str(payment.get('id', '')) != str(expected_payment_id):
        raise BillingError('Identificador de pagamento divergente')
    if str(payment.get('external_reference') or '') != str(expected_reference):
        raise BillingError('Referência da contratação divergente')
    if str(payment.get('collector_id') or '') != str(expected_collector):
        raise BillingError('Recebedor da cobrança divergente')
    if expected_live_mode is not None and payment.get('live_mode') is not expected_live_mode:
        raise BillingError('Ambiente do pagamento divergente')
    if str(payment.get('currency_id') or '') != expected_currency:
        raise BillingError('Moeda divergente')
    if money_cents(payment.get('transaction_amount')) != int(expected_cents):
        raise BillingError('Valor da cobrança divergente')
    if expected_subscription:
        found = payment.get('preapproval_id') or payment.get('subscription_id')
        if found and str(found) != str(expected_subscription):
            raise BillingError('Assinatura do pagamento divergente')
    return str(payment.get('status') or '').lower()


class MercadoPagoClient:
    ROOT = 'https://api.mercadopago.com'

    def __init__(self, access_token: str, *, timeout: int = 15):
        if not access_token or not access_token.strip():
            raise BillingError('Credencial Mercado Pago ausente')
        self._token = access_token
        self.timeout = timeout

    def _request(self, method: str, path: str, payload: dict | None = None,
                 idempotency_key: str | None = None) -> dict:
        # Prevent arbitrary outbound host/path requests.
        if not path.startswith(('/preapproval', '/v1/payments/')) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path):
            raise BillingError('Endpoint Mercado Pago inválido')
        headers = {'Authorization': f'Bearer {self._token}', 'Content-Type': 'application/json'}
        if idempotency_key:
            headers['X-Idempotency-Key'] = idempotency_key
        try:
            response = requests.request(method, self.ROOT + path, headers=headers,
                                        json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError('Falha na comunicação com Mercado Pago') from exc
        if not isinstance(data, dict):
            raise ProviderError('Resposta do Mercado Pago inválida')
        return data

    def create_monthly_subscription(self, *, email: str, reference: str,
                                    cents: int, return_url: str) -> dict:
        return self._request('POST', '/preapproval', {
            'reason': 'AcheTece — assinatura mensal',
            'external_reference': reference,
            'payer_email': email,
            'auto_recurring': {'frequency': 1, 'frequency_type': 'months',
                               'transaction_amount': cents / 100, 'currency_id': 'BRL'},
            'back_url': return_url,
            'status': 'pending'
        }, idempotency_key=reference)

    def get_subscription(self, subscription_id: str) -> dict:
        if not re.fullmatch(r'[A-Za-z0-9_-]{4,128}', str(subscription_id)):
            raise BillingError('Assinatura inválida')
        return self._request('GET', f'/preapproval/{subscription_id}')

    def cancel_subscription(self, subscription_id: str) -> dict:
        if not re.fullmatch(r'[A-Za-z0-9_-]{4,128}', str(subscription_id)):
            raise BillingError('Assinatura inválida')
        return self._request('PUT', f'/preapproval/{subscription_id}', {'status':'canceled'})

    def get_payment(self, payment_id: str) -> dict:
        if not re.fullmatch(r'\d{1,32}', str(payment_id)):
            raise BillingError('Identificador de pagamento inválido')
        return self._request('GET', f'/v1/payments/{payment_id}')
