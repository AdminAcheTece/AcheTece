"""ED02.3G.7D: validacao financeira fail-closed, SEM efeitos colaterais.

Nao habilitar no processamento Flask sem revisao do caminho de ativacao.
"""
import os
from sqlalchemy import text
from ed02_financial_guard import validate_financial_payment, FinancialValidationError

class ContractGuardError(ValueError):
    pass

def verify_payment_against_contract(db, payment, payment_id):
    """Consulta contrato persistido e confronta MP; nunca modifica empresa ou contrato.

    `payment` deve ser o resultado de consulta autenticada ao Mercado Pago,
    nunca JSON recebido diretamente do webhook.
    """
    if os.environ.get('ED01_HOMOLOGACAO', '').strip().lower() != 'true':
        raise ContractGuardError('ambiente nao autorizado')
    if db.engine.dialect.name != 'postgresql':
        raise ContractGuardError('tipo de banco nao autorizado')
    if db.session.execute(text('SELECT current_database()')).scalar_one() != 'achetece_ed01_test':
        raise ContractGuardError('banco nao autorizado')
    if not isinstance(payment, dict):
        raise ContractGuardError('pagamento invalido')
    ref = payment.get('external_reference')
    if not isinstance(ref, str) or not ref:
        raise ContractGuardError('contratacao nao identificada')
    row = db.session.execute(text('''
      SELECT empresa_id, external_reference, plano, valor_esperado, moeda,
             status, provedor, preference_id
        FROM public.contratacao_checkout
       WHERE external_reference = :ref
    '''), {'ref':ref}).mappings().one_or_none()
    if row is None:
        raise ContractGuardError('contratacao inexistente')
    if (row['status'] != 'preferencia_criada' or row['provedor'] != 'mercadopago'
            or row['moeda'].strip() != 'BRL' or not row['preference_id']):
        raise ContractGuardError('contratacao nao elegivel')
    # Passar ambos os precos como o valor imutavel da mesma contratacao, NAO
    # os precos correntes do app (que podem ter mudado desde o checkout).
    try:
        validated = validate_financial_payment(
            payment, company_id=row['empresa_id'], payment_id=payment_id,
            expected_reference=row['external_reference'], plan=row['plano'],
            monthly_price=row['valor_esperado'], yearly_price=row['valor_esperado'])
    except (FinancialValidationError, ValueError, TypeError):
        raise ContractGuardError('pagamento incompatível com contratacao') from None
    return validated
