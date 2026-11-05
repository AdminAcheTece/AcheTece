"""ED02.5 V5.10 — aplicação única do pagamento LEGADO, SOMENTE homologação.

NUNCA executar com dados de webhook não confirmados: 'payment' precisa vir de
consulta autenticada ao Mercado Pago. Nunca habilitar em produção. Não efetua
HTTP, email, checkout nem publicação. Falha fechada e bloqueia históricos sem
proveniência financeira verificável; novos contratos/pagamentos são distintos.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import os
from sqlalchemy import text
from ed02_payment_shadow import extract_verified_record
from ed02_payment_contract_guard import verify_payment_against_contract

BANCO_AUTORIZADO = "achetece_ed01_test"
ESTADOS_APLICADOS = frozenset(("aplicado_legado_v510", "aplicado_ed02", "periodo_ed025"))
ESTADOS_OBSERVADOS = frozenset(("observado_ed02", "recebido"))
ESTADOS_REVISAO = frozenset(("refunded", "charged_back", "in_mediation"))


class BloqueioV510(RuntimeError):
    """Bloqueio genérico — não deve expor dados financeiros ou identidades."""


def _data_aprovacao(payment: dict) -> datetime:
    raw = payment.get("date_approved")
    if not isinstance(raw, str) or len(raw) > 64:
        raise BloqueioV510("data de aprovacao verificavel obrigatoria")
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        raise BloqueioV510("data de aprovacao invalida") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise BloqueioV510("data sem fuso horario")
    aware = parsed.astimezone(timezone.utc)
    if aware > datetime.now(timezone.utc) + timedelta(minutes=5):
        raise BloqueioV510("data de aprovacao futura")
    return aware


def _guard(db):
    if os.getenv("ED01_HOMOLOGACAO", "").strip().lower() != "true" or \
       os.getenv("ED02_V510_REPLAY_HOMOLOG", "").strip().lower() != "true":
        raise BloqueioV510("homologacao nao autorizada")
    if db.engine.dialect.name != "postgresql":
        raise BloqueioV510("dialeto bloqueado")
    if db.session.execute(text("SELECT current_database()")).scalar_one() != BANCO_AUTORIZADO:
        raise BloqueioV510("banco bloqueado")


def _resultado(aplicado=False, duplicado=False, revisao=False, primeira=False, status="approved"):
    return {"aplicado": bool(aplicado), "duplicado": bool(duplicado),
            "revisao_historica": bool(revisao), "primeira_ativacao": bool(primeira),
            "status": status, "modo": "v510_homologacao"}


def processar_legado_uma_vez_homolog(db, payment: dict, empresa_id: int) -> dict:
    """Um único commit para empresa+ledger; replay nunca prorroga a vigência.

    - lock na empresa serializa pagamentos diferentes para a mesma malharia;
    - payment_id globalmente único (provedor, ID); checkout/referência 1 uso;
    - empresa ativa sem histórico aplicado: conciliação manual, sem update;
    - outra confirmação real só renova com novo ID e checkout registrado;
    - datas são as de aprovação do provedor, não a chegada do webhook;
    - estornos/contestações não geram renovação nem revogação automática.
    """
    sessao = db.session
    try:
        # Guardas anteriores a qualquer DML ou processamento do evento.
        _guard(db)
        rec = extract_verified_record(payment, empresa_id)
        if rec["status_provedor"] != "approved":
            sessao.rollback()
            return _resultado(revisao=True, status=rec["status_provedor"])
        if payment.get("transaction_amount_refunded") not in (None, 0, "0", "0.00"):
            sessao.rollback()
            return _resultado(revisao=True)
        instante = _data_aprovacao(payment)

        # Esta consulta FOR UPDATE precisa vir antes do checkout para manter a
        # mesma ordem de lock em chamadas concorrentes da V5.10.
        empresa = sessao.execute(text("""
            SELECT id, lower(coalesce(status_pagamento,'')) AS status,
                   data_pagamento FROM public.empresa
             WHERE id=:empresa_id FOR UPDATE
        """), {"empresa_id": rec["empresa_id"]}).mappings().one_or_none()
        if empresa is None:
            raise BloqueioV510("empresa inexistente")
        if empresa["status"] not in ("ativo", "pendente"):
            raise BloqueioV510("estado da empresa exige revisao")

        validado = verify_payment_against_contract(db, payment, rec["payment_id"])
        if int(validado["empresa_id"]) != rec["empresa_id"]:
            raise BloqueioV510("contrato pertence a outra empresa")

        prior = sessao.execute(text("""
            SELECT id, empresa_id, external_reference, estado_processamento,
                   status_provedor FROM public.pagamento_evento
             WHERE provedor='mercadopago' AND payment_id=:payment_id FOR UPDATE
        """), rec).mappings().one_or_none()
        if prior:
            if int(prior["empresa_id"]) != rec["empresa_id"] or prior["external_reference"] != rec["external_reference"]:
                raise BloqueioV510("identidade de pagamento conflitante")
            if prior["estado_processamento"] in ESTADOS_APLICADOS:
                sessao.rollback()  # libera locks, sem atualizar data ou contador
                return _resultado(duplicado=True)
            if (prior["estado_processamento"] not in ESTADOS_OBSERVADOS or
                (prior["status_provedor"] or "").lower() in ESTADOS_REVISAO):
                raise BloqueioV510("pagamento historico exige revisao")

        # Mesmo checkout não poderá ser consumido por outro payment_id.
        usado = sessao.execute(text("""
            SELECT payment_id FROM public.pagamento_evento
             WHERE provedor='mercadopago' AND external_reference=:external_reference
               AND estado_processamento IN ('aplicado_legado_v510','aplicado_ed02','periodo_ed025')
             FOR UPDATE
        """), rec).mappings().all()
        if any(str(p["payment_id"]) != rec["payment_id"] for p in usado):
            raise BloqueioV510("checkout ja consumido")
        # Proteger também contrato que tenha período ED02 materializado.
        periodo = sessao.execute(text("""
            SELECT id FROM public.ed02_assinatura_periodo
             WHERE provedor='mercadopago'
               AND payment_id=:payment_id LIMIT 1
        """), rec).first()
        if periodo is not None:
            raise BloqueioV510("pagamento ja utilizado em periodo")

        # Uma assinatura histórica ativa não prova qual foi o último pagamento.
        # Impedir o replay de evento antigo ausente do ledger; exigir conciliação.
        is_ativo = empresa["status"] == "ativo"
        ultima_data = empresa["data_pagamento"]
        if is_ativo:
            historico_comprovado = sessao.execute(text("""
                SELECT id FROM public.pagamento_evento
                 WHERE empresa_id=:empresa_id AND provedor='mercadopago'
                   AND estado_processamento IN ('aplicado_legado_v510','aplicado_ed02','periodo_ed025')
                 LIMIT 1
            """), rec).first()
            if not historico_comprovado or ultima_data is None:
                sessao.rollback()
                return _resultado(revisao=True)
            # O pagamento não pode ter sido aprovado antes da última ativação.
            # É conservador: renovações atrasadas seguem para revisão.
            if instante.replace(tzinfo=None) <= ultima_data:
                sessao.rollback()
                return _resultado(revisao=True)
        elif ultima_data is not None:
            sessao.rollback()
            return _resultado(revisao=True)

        if prior:
            sessao.execute(text("""
                UPDATE public.pagamento_evento
                   SET estado_processamento='aplicado_legado_v510',
                       status_provedor='approved', ultima_vez_em=now(),
                       instante_aprovacao_provedor=:instante
                 WHERE id=:id
            """), {"id": prior["id"], "instante": instante})
        else:
            sessao.execute(text("""
                INSERT INTO public.pagamento_evento
                  (empresa_id, provedor, payment_id, external_reference,
                   status_provedor, tipo_evento, valor, moeda,
                   estado_processamento, instante_aprovacao_provedor)
                VALUES (:empresa_id,'mercadopago',:payment_id,:external_reference,
                   'approved','consulta_pagamento',:valor,:moeda,
                   'aplicado_legado_v510',:instante)
            """), {**rec, "instante": instante})
        sessao.execute(text("""
            UPDATE public.empresa
               SET status_pagamento='ativo', data_pagamento=:aprovacao_utc_naive
             WHERE id=:empresa_id
        """), {"empresa_id": rec["empresa_id"],
                "aprovacao_utc_naive": instante.replace(tzinfo=None)})
        sessao.commit()
        return _resultado(aplicado=True, primeira=not is_ativo)
    except Exception:
        sessao.rollback()
        raise
