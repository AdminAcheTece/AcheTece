'''ED02.5 V2: adaptador isolado, SEM ligacao ao Flask ou webhook.

Uma chamada corresponde a pagamento verificado via API autenticada do Mercado Pago.
Nao envia email, nao gera cobrancas nem altera status da empresa.
A transacao faz lock + valida contrato + aplica um periodo e ledger juntos.
'''
from datetime import datetime, timezone
import os
from sqlalchemy import text
from ed02_5_vigencia import calcular_vigencia
from ed02_payment_shadow import extract_verified_record
from ed02_payment_contract_guard import verify_payment_against_contract

class ED025Blocked(RuntimeError): pass

def registrar_periodo_homolog(db, payment, empresa_id: int, aprovado_em: datetime):
    session=db.session
    try:
        # Falhar fechado, verificar banco EFETIVO antes de qualquer escrita.
        if os.getenv('ED01_HOMOLOGACAO','').lower() != 'true':
            raise ED025Blocked('ambiente nao autorizado')
        if db.engine.dialect.name != 'postgresql':
            raise ED025Blocked('dialeto nao autorizado')
        if session.execute(text('SELECT current_database()')).scalar_one() != 'achetece_ed01_test':
            raise ED025Blocked('banco nao autorizado')
        if not isinstance(aprovado_em,datetime) or aprovado_em.tzinfo is None or aprovado_em.utcoffset() is None:
            raise ED025Blocked('data de aprovacao nao verificada')
        rec=extract_verified_record(payment, empresa_id)
        if rec['status_provedor'] != 'approved':
            raise ED025Blocked('pagamento nao aprovado')
        # Um pagamento nao pode ser aplicado a empresa distinta; serializa
        # TODOS os periodos de uma empresa, inclusive dois pagamentos distintos.
        company=session.execute(text('''SELECT id FROM public.empresa
            WHERE id=:empresa_id FOR UPDATE'''),{'empresa_id':int(empresa_id)}).first()
        if not company: raise ED025Blocked('empresa inexistente')
        validated=verify_payment_against_contract(db,payment,rec['payment_id'])
        if int(validated['empresa_id']) != int(empresa_id):
            raise ED025Blocked('empresa incompativel')
        contract=session.execute(text('''SELECT id, empresa_id, plano
             FROM public.contratacao_checkout
             WHERE external_reference=:external_reference FOR UPDATE'''),rec).mappings().one()
        if int(contract['empresa_id'])!=int(empresa_id):
            raise ED025Blocked('contratacao incompativel')
        # Duplicate check MUST happen before calculating a new period.
        prior=session.execute(text('''SELECT id,inicio,fim_exclusivo
            FROM public.ed02_assinatura_periodo
            WHERE provedor='mercadopago' AND payment_id=:payment_id
            FOR UPDATE'''),rec).mappings().one_or_none()
        if prior:
            session.commit()
            return {'duplicado':True,'periodo_id':prior['id'],
                    'inicio':prior['inicio'],'fim_exclusivo':prior['fim_exclusivo']}
        # contract_id is unique: a distinct payment cannot reuse the same checkout.
        existing=session.execute(text('''SELECT id FROM public.ed02_assinatura_periodo
          WHERE contrato_id=:contrato_id'''),{'contrato_id':contract['id']}).first()
        if existing: raise ED025Blocked('contratacao ja consumida')
        latest=session.execute(text('''SELECT max(fim_exclusivo) FROM public.ed02_assinatura_periodo
            WHERE empresa_id=:empresa_id'''),rec).scalar_one()
        period=calcular_vigencia(contract['plano'],aprovado_em,latest)
        res=session.execute(text('''INSERT INTO public.ed02_assinatura_periodo
          (empresa_id, contrato_id, provedor, payment_id, plano, inicio, fim_exclusivo)
          VALUES (:empresa_id,:contrato_id,'mercadopago',:payment_id,:plano,:inicio,:fim_exclusivo)
          RETURNING id'''),{**rec,'contrato_id':contract['id'],'plano':contract['plano'],
            'inicio':period.inicio,'fim_exclusivo':period.fim_exclusivo}).scalar_one()
        # Registro financeiro no mesmo commit: nao converte estado existente
        # 'aplicado_ed02' em nova vigencia, pois foi processado no modelo legado.
        old=session.execute(text('''SELECT id,empresa_id,external_reference,estado_processamento
            FROM public.pagamento_evento
            WHERE provedor='mercadopago' AND payment_id=:payment_id FOR UPDATE'''),rec).mappings().one_or_none()
        if old:
            if (int(old['empresa_id']) != int(empresa_id) or
                old['external_reference'] != rec['external_reference'] or
                old['estado_processamento'] == 'aplicado_ed02'):
                raise ED025Blocked('pagamento ja aplicado ou incompativel')
            session.execute(text('''UPDATE public.pagamento_evento
                 SET estado_processamento='periodo_ed025', ultima_vez_em=now(),
                     contador_recebimentos=contador_recebimentos+1
               WHERE id=:id'''),{'id':old['id']})
        else:
            session.execute(text('''INSERT INTO public.pagamento_evento
               (empresa_id,provedor,payment_id,external_reference,status_provedor,
                tipo_evento,valor,moeda,estado_processamento)
               VALUES (:empresa_id,'mercadopago',:payment_id,:external_reference,
                :status_provedor,'consulta_pagamento',:valor,:moeda,'periodo_ed025')'''),rec)
        # IMPORTANTE: nenhum status_pagamento/data_pagamento aqui.
        # A integracao de ativacao / visibilidade exige gate separado.
        session.commit()
        return {'duplicado':False,'periodo_id':res,'inicio':period.inicio,
                'fim_exclusivo':period.fim_exclusivo}
    except Exception:
        session.rollback()
        raise
