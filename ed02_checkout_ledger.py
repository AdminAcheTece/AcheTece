"""ED02.3G.7C: registro de checkout SOMENTE em banco isolado de homologacao."""
import os
import re
from decimal import Decimal, InvalidOperation
from sqlalchemy import text

REFERENCE = re.compile(r"^achetece:([1-9][0-9]*):[a-fA-F0-9]{32}$")

def _check(db, empresa_id, reference, plano, valor):
    if os.getenv('ED01_HOMOLOGACAO', '').lower() != 'true':
        raise RuntimeError('ED02 checkout bloqueado fora da homologacao')
    engine = db.engine
    if engine.dialect.name != 'postgresql':
        raise RuntimeError('ED02 requer PostgreSQL')
    banco = db.session.execute(text('select current_database()')).scalar()
    if banco != 'achetece_ed01_test':
        raise RuntimeError('ED02 banco conectado nao autorizado')
    m = REFERENCE.fullmatch(reference or '')
    if not m or int(m.group(1)) != int(empresa_id):
        raise ValueError('Referencia invalida para empresa')
    if plano not in ('mensal','anual'):
        raise ValueError('Plano invalido')
    try:
        v = Decimal(str(valor))
    except (InvalidOperation, TypeError):
        raise ValueError('Valor invalido')
    if not v.is_finite() or v <= 0 or v != v.quantize(Decimal('0.01')):
        raise ValueError('Valor invalido')
    return v

def registrar_contratacao(db, empresa_id, reference, plano, valor):
    v = _check(db, empresa_id, reference, plano, valor)
    try:
        db.session.execute(text("""INSERT INTO public.contratacao_checkout
            (empresa_id, external_reference, plano, valor_esperado, moeda, status)
            VALUES (:empresa, :ref, :plano, :valor, 'BRL', 'criada')"""),
            {'empresa': int(empresa_id), 'ref':reference, 'plano':plano, 'valor':v})
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise

def registrar_resultado_preferencia(db, reference, preference_id=None):
    # Estado posterior nao altera empresa/plano/valor/ref (dados contratados).
    if not REFERENCE.fullmatch(reference or ''):
        raise ValueError('Referencia invalida')
    if preference_id is not None and (not isinstance(preference_id,str) or not preference_id.strip()):
        raise ValueError('Identificador de preferencia invalido')
    try:
        # Revalidar em cada nova sessao, inclusive apos consulta externa.
        if os.getenv('ED01_HOMOLOGACAO','').lower() != 'true' or db.engine.dialect.name != 'postgresql' or db.session.execute(text('select current_database()')).scalar() != 'achetece_ed01_test':
            raise RuntimeError('Banco de homologacao nao autorizado')
        result = db.session.execute(text("""UPDATE public.contratacao_checkout
            SET status = :estado, preference_id = :pref, atualizado_em_utc=now()
            WHERE external_reference=:ref AND status='criada' RETURNING id"""),
            {'estado':'preferencia_criada' if preference_id else 'falha_preferencia',
             'pref':preference_id, 'ref':reference})
        if result.scalar_one_or_none() is None:
            raise RuntimeError('Contratacao nao encontrada ou ja finalizada')
        db.session.commit()
    except Exception:
        db.session.rollback()
        raise
