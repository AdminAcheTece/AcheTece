"""ED02.3G.7 - contrato financeiro isolado; NAO altera pagamentos.

A origem de payment deve ser consulta autenticada ao provedor. O ID esperado,
referencia e plano devem provir de checkout registrado pela plataforma,
nao de parametros enviados pelo webhook ou pelo navegador.
"""
from decimal import Decimal, InvalidOperation
import hmac
import re

REF_RE = re.compile(r"^achetece:([1-9][0-9]*):([a-fA-F0-9]{32})$")

class FinancialValidationError(ValueError):
    pass

def _money(value):
    if isinstance(value, bool):
        raise FinancialValidationError("valor invalido")
    try:
        v = Decimal(str(value))
    except (ValueError, TypeError, InvalidOperation):
        raise FinancialValidationError("valor invalido") from None
    if not v.is_finite() or v <= 0 or v.as_tuple().exponent < -2:
        raise FinancialValidationError("valor invalido")
    return v

def validate_financial_payment(payment, *, company_id, payment_id, expected_reference,
                               plan, monthly_price, yearly_price):
    """Rejeita qualquer discrepancia; sem efeitos em DB, MP, Flask ou email."""
    if not isinstance(payment, dict):
        raise FinancialValidationError("pagamento invalido")
    pid = str(payment_id).strip()
    if not pid.isascii() or not pid.isdecimal() or not pid or len(pid) > 160:
        raise FinancialValidationError("identificador invalido")
    if str(payment.get('id', '')).strip() != pid:
        raise FinancialValidationError("identificador divergente")
    if plan not in ('mensal', 'anual'):
        raise FinancialValidationError("plano invalido")
    reference = str(payment.get('external_reference') or '')
    match = REF_RE.fullmatch(reference)
    if not match or int(match.group(1)) != int(company_id):
        raise FinancialValidationError("referencia invalida")
    if not isinstance(expected_reference, str) or not hmac.compare_digest(reference, expected_reference):
        raise FinancialValidationError("referencia divergente")
    if str(payment.get('currency_id') or '').upper() != 'BRL':
        raise FinancialValidationError("moeda divergente")
    if str(payment.get('status') or '').lower() != 'approved':
        raise FinancialValidationError("pagamento nao aprovado")
    expected = _money(monthly_price if plan == 'mensal' else yearly_price)
    amount = _money(payment.get('transaction_amount'))
    if amount != expected:
        raise FinancialValidationError("valor divergente")
    if payment.get('transaction_amount_refunded') is not None and _money_refunded(payment.get('transaction_amount_refunded')) > 0:
        raise FinancialValidationError("estorno identificado")
    return {'payment_id': pid, 'empresa_id': int(company_id), 'plano': plan,
            'valor': amount, 'moeda': 'BRL', 'status': 'approved', 'external_reference': reference}

def _money_refunded(value):
    try:
        v = Decimal(str(value))
    except (TypeError, ValueError, InvalidOperation):
        raise FinancialValidationError("valor estornado invalido") from None
    if not v.is_finite() or v < 0 or v.as_tuple().exponent < -2:
        raise FinancialValidationError("valor estornado invalido")
    return v
