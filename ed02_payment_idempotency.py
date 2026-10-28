"""ED02.3E: somente homologacao. Pagamento verificado via consulta autenticada do MP.

NAO instalar em producao; requer public.pagamento_evento criado em ED02.2.
O controle de duplicidade e a atualizacao da empresa sao uma UNICA transacao.
Nao cria ciclos, nao altera precos, nao chama Mercado Pago e nao envia email.
"""
from sqlalchemy import text
from ed02_payment_shadow import extract_verified_record


def process_verified_payment_exact_once(db, payment, empresa_id):
    rec = extract_verified_record(payment, empresa_id)
    session = db.session
    try:
        # Falha fechada: mesmo que a flag tenha sido ligada incorretamente em
        # producao, NENHUM UPDATE/INSERT ocorre antes desta verificacao.
        if session.execute(text('SELECT current_database()')).scalar_one() != 'achetece_ed01_test':
            raise RuntimeError('ED02.3E BLOQUEADO: uso permitido somente no banco achetece_ed01_test')
        # Uma empresa por vez, inclusive em workers diferentes do Gunicorn.
        row = session.execute(text("""
            SELECT id, lower(coalesce(status_pagamento, '')) AS status
              FROM public.empresa
             WHERE id=:empresa_id
             FOR UPDATE
        """), {'empresa_id': rec['empresa_id']}).mappings().first()
        if row is None:
            raise ValueError('empresa inexistente')
        # Identidade imutavel, isolada por provedor + payment_id.
        prior = session.execute(text("""
            SELECT id, empresa_id, external_reference, status_provedor, estado_processamento
              FROM public.pagamento_evento
             WHERE provedor='mercadopago' AND payment_id=:payment_id
             FOR UPDATE
        """), rec).mappings().first()
        if prior and (prior['empresa_id'] != rec['empresa_id'] or
                      prior['external_reference'] != rec['external_reference']):
            raise ValueError('payment_id ja pertence a outra empresa/referencia')
        already_applied = bool(prior and prior['estado_processamento'] == 'aplicado_ed02')
        if prior:
            session.execute(text("""
                UPDATE public.pagamento_evento
                   SET contador_recebimentos = contador_recebimentos + 1,
                       ultima_vez_em=now(), status_provedor=:status_provedor
                 WHERE id=:id
            """), {**rec, 'id':prior['id']})
        else:
            session.execute(text("""
                INSERT INTO public.pagamento_evento
                  (empresa_id, provedor, payment_id, external_reference,
                   status_provedor, tipo_evento, valor, moeda, estado_processamento)
                VALUES (:empresa_id, 'mercadopago', :payment_id, :external_reference,
                   :status_provedor, 'consulta_pagamento', :valor, :moeda, 'recebido')
            """), rec)
        applied = rec['status_provedor'] == 'approved' and not already_applied
        if applied:
            session.execute(text("""
                UPDATE public.empresa
                   SET status_pagamento='ativo', data_pagamento=(now() AT TIME ZONE 'UTC')
                 WHERE id=:empresa_id
            """), rec)
            session.execute(text("""
                UPDATE public.pagamento_evento SET estado_processamento='aplicado_ed02'
                 WHERE provedor='mercadopago' AND payment_id=:payment_id
            """), rec)
        session.commit()
        return {'aplicado': applied, 'duplicado': already_applied,
                'primeira_ativacao': applied and row['status'] != 'ativo',
                'status':rec['status_provedor']}
    except Exception:
        session.rollback()
        raise
