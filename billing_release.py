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
    """Error with redacted provider metadata, never raw payloads or credentials."""

    def __init__(self, message: str, *, http_status=None,
                 provider_code: str = '', cause_code: str = '',
                 message_hint: str = 'indisponivel',
                 cause_hint: str = 'indisponivel',
                 response_kind: str = 'desconhecido'):
        super().__init__(message)
        self.http_status = http_status
        self.provider_code = provider_code
        self.cause_code = cause_code
        # Apenas vocabulario controlado; mensagens originais nunca saem do cliente.
        self.message_hint = message_hint
        self.cause_hint = cause_hint
        self.response_kind = response_kind


def _safe_provider_code(value) -> str:
    """Only allow short machine-readable codes in logs. Never log API messages."""
    if isinstance(value, bool) or not isinstance(value, (int, str)):
        return 'unspecified'
    candidate = str(value)
    if (not candidate.upper().startswith(('APP_USR', 'TEST-', 'BEARER'))
            and re.fullmatch(r'[A-Za-z0-9_.-]{1,48}', candidate)):
        return candidate
    return 'unspecified'


# V1.7: somente termos tecnicos predeterminados podem aparecer nos logs.
# A API pode inserir dados pessoais, emails e tokens dentro de "message" e
# "cause.description". NUNCA registrar esses campos integralmente.
_MP_SAFE_DIAGNOSTIC_WORDS = frozenset("""
access account accounts active allowed already amount api app application associated
at authentication authorization authorized available bad back billing blocked brl
business canceled cannot cant card cards category charge checkout client code
configuration configured conflict country create created creation credential
credentials currency customer data date denied disabled document domain duplicate
email enabled environment error expired failure field forbidden format frequency
from invalid invoice is key limit live merchant method missing mode month months
must no not null operation parameter payer payer_email payment payments pending
permission permissions plan preapproval preapproval_plan_id present production
provider recurring rejected required request resource restricted risk sandbox
seller should site stage status subscription subscriptions test token transaction
type unauthorized unavailable unsupported user users valid validation value with
without wrong
nao pode permitido invalido obrigatorio assinatura assinaturas credencial
credenciais usuario vendedor comprador conta teste pagamentos permissao restrito
""".split())


def safe_mp_message_hint(value) -> str:
    """Summarize provider prose using a strict static vocabulary (no free text).

    Unknown words, URLs, emails, names, IDs, tokens and error body contents
    cannot be written. The result is a technical hint, not a verbatim quote.
    """
    if not isinstance(value, str) or not value:
        return 'indisponivel'
    stripped = value[:4000]
    stripped = re.sub(r'https?://[^\s]+', ' ', stripped, flags=re.IGNORECASE)
    stripped = re.sub(r'[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}', ' ', stripped)
    stripped = re.sub(r'(?:APP_USR|TEST)-[^\s,;]+', ' ', stripped, flags=re.IGNORECASE)
    stripped = re.sub(r'[0-9]{5,}', ' ', stripped)
    words = re.findall(r'[A-Za-z][A-Za-z0-9_-]{1,60}', stripped.lower())
    approved = [word for word in words if word in _MP_SAFE_DIAGNOSTIC_WORDS]
    hint = ' '.join(approved[:32])[:180].strip()
    return hint if hint else 'indisponivel'


def _safe_provider_response_details(response) -> dict:
    """Discard provider error details after extracting non-sensitive metadata."""
    try:
        raw = response.json()
    except (ValueError, TypeError):
        raw = None
    if isinstance(raw, dict):
        details = raw
        kind = 'json_object'
    elif isinstance(raw, list):
        details = next((x for x in raw if isinstance(x, dict)), {})
        kind = 'json_list'
    else:
        details = {}
        content_type = str(response.headers.get('Content-Type') or '').lower()
        kind = 'text_plain' if content_type.startswith('text/plain') else 'non_json'
    causes = details.get('cause')
    first_cause = next((x for x in causes if isinstance(x, dict)), {}) if isinstance(causes, list) else {}
    nested_error = details.get('error') if isinstance(details.get('error'), dict) else {}
    errors = details.get('errors')
    first_error = next((x for x in errors if isinstance(x, dict)), {}) if isinstance(errors, list) else {}
    provider_code = (nested_error.get('code') or details.get('error')
                     or details.get('code') or first_error.get('code'))
    if isinstance(provider_code, dict):
        provider_code = None
    message = (details.get('message') or details.get('description')
               or nested_error.get('message') or first_error.get('message'))
    cause_message = (first_cause.get('description') or first_cause.get('message')
                     or first_error.get('description'))
    if kind == 'text_plain':
        # Plain-text messages are still parsed ONLY through the same vocabulary.
        message = response.text[:4000]
    return {
        'provider_code': _safe_provider_code(provider_code),
        'cause_code': _safe_provider_code(first_cause.get('code') or first_error.get('code')),
        'message_hint': safe_mp_message_hint(message),
        'cause_hint': safe_mp_message_hint(cause_message),
        'response_kind': kind,
    }


# V1.8: separate MP test payer from knitting mill's registered contact.
# The official Subscriptions API uses test@testuser.com in test examples.
# It is a fixture, not an asserted e-mail inbox of the buyer TESTUSER.
_MP_HML_TEST_PAYER_EMAILS = re.compile(
    r'(?:test|testuser[0-9]{6,15})@testuser\.com', re.IGNORECASE)


def monthly_payer_email_for_checkout(registered_email: str, env,
                                     *, actual_database_name: str) -> str:
    """Choose payer only for tightly guarded monthly sandbox homologation.

    No company record is changed. Missing opt-in preserves historical behavior.
    A configured override with an unexpected HML environment fails closed.
    Production always uses the actual client's registered e-mail.
    """
    original = str(registered_email or '').strip()
    if not original or '@' not in original:
        raise BillingError('E-mail registrado invalido')

    if str(env.get('ACHETECE_BILLING_ENV') or '').strip().lower() != 'sandbox':
        return original

    configured = str(env.get('ACHETECE_HML_MP_TEST_PAYER_EMAIL') or '').strip()
    if not configured:
        return original

    buyer_id = str(env.get('ACHETECE_HML_TEST_BUYER_USER_ID') or '').strip()
    seller_id = str(env.get('MP_COLLECTOR_ID') or '').strip()
    guards_ok = (
        env.get('ACHETECE_HML_EXPECTED_DB_NAME') == 'achetece_release_v1_test'
        and actual_database_name == 'achetece_release_v1_test'
        and env.get('ACHETECE_MP_TEST_TOKEN_CONFIRMED') == '1'
        and str(env.get('ACHETECE_BILLING_V1_ENABLED') or '').strip().lower()
            in ('1','true','yes','sim')
        and env.get('ACHETECE_BILLING_CALLBACK_BASE_URL')
            == 'https://achetece-assinaturas-hml.onrender.com'
        and bool(re.fullmatch(r'[0-9]{6,16}', buyer_id))
        and bool(re.fullmatch(r'[0-9]{6,16}', seller_id))
        and buyer_id != seller_id
    )
    if not guards_ok:
        raise BillingError('Bloqueio de seguranca do pagador HML')

    if configured.lower() != configured or not _MP_HML_TEST_PAYER_EMAILS.fullmatch(configured):
        raise BillingError('Pagador ficticio nao permitido no teste HML')
    return configured



# V1.9 — Verify the monthly HML test payer from an earlier authorized
# sandbox payment of the SAME test buyer and seller. The API's generic
# test@testuser.com example is NOT an identity; never invent a buyer email.
_HML_REAL_TEST_PAYER = re.compile(
    r'(?:test_user_[a-z0-9]{4,24}|testuser[0-9]{6,15})@testuser\.com',
    re.IGNORECASE,
)


def verified_monthly_hml_payer_from_payment(payment: dict, *,
                                            source_payment_id: str,
                                            source_reference: str,
                                            source_amount_cents: int,
                                            expected_collector_id: str,
                                            expected_buyer_id: str) -> str:
    """Only return an email attached to an already approved TESTUSER payment.

    No raw provider information is logged or persisted. A missing or
    inconsistent attribute fails closed before POST /preapproval. Verified
    identity and country use seller ID + buyer ID + BRL payment, not an
    assumed email constructed from a nickname or API documentation fixture.
    """
    if not isinstance(payment, dict):
        raise BillingError('Pagador HML: pagamento anterior indisponivel')
    if (not re.fullmatch(r'[0-9]{6,32}', str(source_payment_id))
            or not str(expected_buyer_id).isdigit()
            or not str(expected_collector_id).isdigit()):
        raise BillingError('Pagador HML: identificadores invalidos')
    if (str(payment.get('id') or '') != str(source_payment_id)
            or str(payment.get('status') or '') != 'approved'
            or str(payment.get('collector_id') or '') != str(expected_collector_id)
            or str(payment.get('currency_id') or '') != 'BRL'
            or str(payment.get('external_reference') or '') != str(source_reference)):
        raise BillingError('Pagador HML: pagamento de origem divergente')
    try:
        cents = money_cents(payment.get('transaction_amount'))
    except BillingError as exc:
        raise BillingError('Pagador HML: valor da origem invalido') from exc
    if cents != source_amount_cents:
        raise BillingError('Pagador HML: valor da origem divergente')
    payer = payment.get('payer')
    if not isinstance(payer, dict) or str(payer.get('id') or '') != str(expected_buyer_id):
        raise BillingError('Pagador HML: comprador anterior divergente')
    email = payer.get('email')
    if not isinstance(email, str):
        raise BillingError('Pagador HML: email do comprador nao disponivel')
    email = email.strip().lower()
    if not _HML_REAL_TEST_PAYER.fullmatch(email):
        raise BillingError('Pagador HML: email de teste nao reconhecido')
    return email

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
    if not all((signature, data_id, secret)):
        return False
    attrs = dict(fragment.strip().split('=', 1) for fragment in signature.split(',') if '=' in fragment)
    ts, received = attrs.get('ts', ''), attrs.get('v1', '')
    if not ts.isdigit() or not re.fullmatch(r'[0-9a-fA-F]{64}', received):
        return False
    now = now or datetime.now(timezone.utc)
    try:
        # Mercado Pago documenta exemplos com segundos (10 digitos) e
        # milissegundos (13 digitos). Ambos exigem HMAC correto e janela curta.
        if len(ts) == 13:
            timestamp_seconds = int(ts) / 1000
        elif len(ts) == 10:
            timestamp_seconds = int(ts)
        else:
            return False
        if abs(now.timestamp() - timestamp_seconds) > max_age_seconds:
            return False
    except (OverflowError, ValueError):
        return False
    manifest = f'id:{data_id.lower()};' + (f'request-id:{request_id};' if request_id else '') + f'ts:{ts};'
    expected = hmac.new(secret.encode('utf-8'), manifest.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(received.lower(), expected)


def check_verified_payment(payment: dict, *, expected_payment_id: str,
                           expected_reference: str, expected_cents: int,
                           expected_collector: str, expected_currency: str = 'BRL',
                           expected_subscription: str | None = None,
                           expected_live_mode: bool | None = None,
                           expected_hml_test_buyer_id: str | None = None,
                           verified_invoice_reference: str | None = None) -> str:
    """Validate the response from authenticated GET /v1/payments/{id}.

    Never infer a company from payer email or an untrusted URL. Reject missing
    fields rather than granting access on incomplete provider responses.
    """
    if str(payment.get('id', '')) != str(expected_payment_id):
        raise BillingError('Identificador de pagamento divergente')
    # Em faturas recorrentes o pagamento pode omitir external_reference.
    # O chamador so pode usar a referencia da FATURA consultada via API
    # depois de validar id, preapproval_id, valor, moeda e payment.id.
    reference = str(payment.get('external_reference') or '')
    if reference and reference != str(expected_reference):
        raise BillingError('Referência da contratação divergente')
    if not reference and str(verified_invoice_reference or '') != str(expected_reference):
        raise BillingError('Referência da contratação ausente')
    if str(payment.get('collector_id') or '') != str(expected_collector):
        raise BillingError('Recebedor da cobrança divergente')
    if expected_live_mode is not None and payment.get('live_mode') is not expected_live_mode:
        # Checkout Pro legado: algumas compras com comprador ficticio podem
        # aparecer na API como live_mode=True. NUNCA remover a checagem geral.
        # Somente a HML, com conta de teste explicitamente identificada pelo
        # operador, pode aceitar essa divergencia. Os outros checks de id,
        # referencia, collector, valor, moeda e status continuam obrigatorios.
        payer = payment.get('payer') or {}
        test_buyer_matches = (
            expected_live_mode is False
            and payment.get('live_mode') is True
            and isinstance(payer, dict)
            and str(expected_hml_test_buyer_id or '').isdigit()
            and str(payer.get('id') or '') == str(expected_hml_test_buyer_id)
        )
        if not test_buyer_matches:
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


def verify_authorized_invoice(invoice: dict, *, invoice_id: str,
                              subscription_id: str, reference: str,
                              payment_id: str, cents: int,
                              currency: str = 'BRL') -> bool:
    """Validate a subscription invoice fetched from MP's authenticated API.

    A signed event is only a trigger: it cannot grant access or determine which
    knitting mill owns the payment. Match invoice + subscription + payment first.
    """
    if not isinstance(invoice, dict) or str(invoice.get('id') or '') != str(invoice_id):
        raise BillingError('Identificador da fatura divergente')
    if str(invoice.get('preapproval_id') or '') != str(subscription_id):
        raise BillingError('Assinatura da fatura divergente')
    if str(invoice.get('external_reference') or '') != str(reference):
        raise BillingError('Referencia da fatura divergente')
    if str(invoice.get('currency_id') or '') != currency:
        raise BillingError('Moeda da fatura divergente')
    if money_cents(invoice.get('transaction_amount')) != int(cents):
        raise BillingError('Valor da fatura divergente')
    paid = invoice.get('payment')
    if not isinstance(paid, dict) or str(paid.get('id') or '') != str(payment_id):
        raise BillingError('Pagamento da fatura divergente')
    if str(paid.get('status') or '').lower() != 'approved':
        raise BillingError('Pagamento da fatura nao aprovado')
    return True


def is_legacy_ipn_payment_hint(args, payload: dict) -> bool:
    """Classify the OLD IPN shape, not a signed Webhook.

    IPN origin cannot be authenticated by x-signature. This only grants the
    right to trigger an authenticated provider GET; the event itself is never
    proof of payment. Activation is still authorized by check_verified_payment.
    """
    if not isinstance(payload, dict):
        return False
    topic = str(args.get('topic') or '').strip().lower()
    payment_id = str(args.get('id') or '').strip()
    if topic != 'payment' or not re.fullmatch(r'[0-9]{1,32}', payment_id):
        return False
    # Modern signed events use type=payment and data.id. Never downgrade them.
    if args.get('type') or args.get('data.id'):
        return False
    body_topic = str(payload.get('type') or '').strip().lower()
    if body_topic and body_topic != 'payment':
        return False
    details = payload.get('data')
    if details is not None:
        if not isinstance(details, dict) or str(details.get('id') or '') != payment_id:
            return False
    return True


def hml_legacy_ipn_hint_enabled(env) -> bool:
    """Fail closed: IPN hinting allowed only in an explicitly isolated HML DB."""
    return (
        env.get('ACHETECE_HML_LEGACY_IPN_HINTS') == '1'
        and env.get('ACHETECE_BILLING_ENV') == 'sandbox'
        and env.get('ACHETECE_HML_EXPECTED_DB_NAME') == 'achetece_release_v1_test'
        and env.get('ACHETECE_MP_TEST_TOKEN_CONFIRMED') == '1'
        and str(env.get('ACHETECE_HML_TEST_BUYER_USER_ID') or '').isdigit()
    )


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
        if not path.startswith(('/preapproval', '/v1/payments/', '/authorized_payments/')) or not re.fullmatch(r'/[A-Za-z0-9_./-]+', path):
            raise BillingError('Endpoint Mercado Pago inválido')
        headers = {'Authorization': f'Bearer {self._token}', 'Content-Type': 'application/json'}
        if idempotency_key:
            headers['X-Idempotency-Key'] = idempotency_key
        try:
            response = requests.request(method, self.ROOT + path, headers=headers,
                                        json=payload, timeout=self.timeout)
            response.raise_for_status()
            data = response.json()
        except requests.HTTPError as exc:
            response = exc.response
            http_status = response.status_code if response is not None else None
            diagnostic = (_safe_provider_response_details(response)
                          if response is not None else {})
            raise ProviderError(
                'Falha na comunicação com Mercado Pago',
                http_status=http_status,
                **diagnostic,
            ) from exc
        except (requests.RequestException, ValueError) as exc:
            raise ProviderError('Falha na comunicação com Mercado Pago') from exc
        if not isinstance(data, dict):
            raise ProviderError('Resposta do Mercado Pago inválida')
        return data

    def subscription_api_access_status(self) -> str:
        """HML-only read probe. Status only: never log response, token or payer.

        A 200 confirms read access to Subscriptions API, not the right to POST
        nor compatibility of every request field. The caller gates this probe
        on the isolated sandbox environment and only after checkout failure.
        """
        try:
            result = requests.request(
                'GET', self.ROOT + '/preapproval/search',
                headers={'Authorization': f'Bearer {self._token}',
                         'Accept': 'application/json'},
                timeout=min(self.timeout, 8),
            )
            status = int(result.status_code)
            return str(status) if 100 <= status <= 599 else 'invalid_http'
        except requests.RequestException:
            return 'falha_rede'

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

    def get_authorized_payment(self, invoice_id: str) -> dict:
        if not re.fullmatch(r'[0-9]{1,32}', str(invoice_id)):
            raise BillingError('Identificador de fatura invalido')
        return self._request('GET', f'/authorized_payments/{invoice_id}')

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
