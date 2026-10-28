"""ED02.3B: registro observacional, exclusivamente em homologação.

Não concede assinatura, não altera Empresa e não envia notificações.
O chamador deve obter o pagamento previamente pela API autenticada do Mercado Pago.
"""
from decimal import Decimal, InvalidOperation
from sqlalchemy import text


def extract_verified_record(payment, empresa_id):
    """Valida a associação explícita e extrai dados mínimos sem dados pessoais."""
    if not isinstance(payment, dict):
        raise ValueError('pagamento inválido')
    ident = str(payment.get('id') or '').strip()
    if not ident.isdecimal() or len(ident) > 160:
        raise ValueError('identificador do pagamento inválido')
    ref = str(payment.get('external_reference') or '').strip()
    parts = ref.split(':')
    if len(parts) != 3 or parts[0] != 'achetece' or not parts[1].isdecimal() or int(parts[1]) != int(empresa_id) or not parts[2]:
        raise ValueError('referência externa ausente ou incompatível')
    status = str(payment.get('status') or '').lower().strip()
    if status not in ('approved', 'pending', 'in_process', 'rejected', 'cancelled', 'refunded', 'charged_back', 'authorized', 'in_mediation'):
        raise ValueError('status do provedor desconhecido')
    moeda = str(payment.get('currency_id') or '').upper().strip()
    if moeda != 'BRL':
        raise ValueError('moeda inesperada')
    try:
        valor = Decimal(str(payment['transaction_amount']))
    except (KeyError, ValueError, InvalidOperation, TypeError):
        raise ValueError('valor inválido') from None
    if not valor.is_finite() or valor < 0 or valor.as_tuple().exponent < -2:
        raise ValueError('valor fora do formato aceito')
    return dict(empresa_id=int(empresa_id), payment_id=ident, external_reference=ref,
                status_provedor=status, valor=valor, moeda=moeda)


def shadow_record_payment(db, payment, empresa_id):
    """Upsert idempotente no ledger, sem alterar a assinatura legada.

    A segurança é reforçada pela verificação de banco PostgreSQL no próprio SQL.
    """
    rec = extract_verified_record(payment, empresa_id)
    conn = db.session.connection()
    database = conn.execute(text('SELECT current_database()')).scalar_one()
    if database != 'achetece_ed01_test':
        raise RuntimeError('ED02 bloqueado: banco não é homologação')
    # Evita atribuir um ID já registrado a outra empresa, inclusive sob concorrência.
    result = conn.execute(text('''
        INSERT INTO public.pagamento_evento
          (empresa_id, provedor, payment_id, external_reference, status_provedor,
           tipo_evento, valor, moeda, payload_minimo_redigido, estado_processamento)
        VALUES (:empresa_id, 'mercadopago', :payment_id, :external_reference,
           :status_provedor, 'consulta_pagamento', :valor, :moeda,
           '{}'::jsonb, 'observado_ed02')
        ON CONFLICT (provedor, payment_id) DO UPDATE SET
          ultima_vez_em = now(),
          contador_recebimentos = pagamento_evento.contador_recebimentos + 1,
          status_provedor = EXCLUDED.status_provedor
        WHERE pagamento_evento.empresa_id = EXCLUDED.empresa_id
          AND pagamento_evento.external_reference = EXCLUDED.external_reference
        RETURNING id, contador_recebimentos
    '''), rec).first()
    if result is None:
        raise ValueError('pagamento previamente vinculado a outra referência/empresa')
    db.session.commit()
    return {'payment_id': rec['payment_id'], 'recebimentos': result[1], 'somente_observacao': True}
