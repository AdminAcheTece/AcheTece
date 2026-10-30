"""ED02.5 V5: leitura somente homologacao, sem alterar visibilidade publica."""
import os
from datetime import datetime, timezone
from sqlalchemy import text

class VigenciaReadBlocked(RuntimeError):
    pass

def consultar_vigencia_homolog(db, empresa_id, agora=None):
    """Consulta periodo vigente; bloqueia fora do banco autorizado.

    Nenhum fallback por email, status antigo ou falta de tabela. Um erro
    gera bloqueio, nao uma ativacao. Nao modifica registros.
    """
    if os.getenv('ED01_HOMOLOGACAO', '').strip().lower() != 'true':
        raise VigenciaReadBlocked('ambiente nao autorizado')
    if db.engine.dialect.name != 'postgresql':
        raise VigenciaReadBlocked('dialeto nao autorizado')
    if db.session.execute(text('SELECT current_database()')).scalar_one() != 'achetece_ed01_test':
        raise VigenciaReadBlocked('banco nao autorizado')
    if not isinstance(empresa_id, int) or isinstance(empresa_id, bool) or empresa_id <= 0:
        raise VigenciaReadBlocked('empresa invalida')
    agora = agora or datetime.now(timezone.utc)
    if not isinstance(agora, datetime) or agora.tzinfo is None or agora.utcoffset() is None:
        raise VigenciaReadBlocked('data sem fuso')
    # Intervalo [inicio, fim): o instante de expiração NÃO está vigente.
    row = db.session.execute(text("""
      SELECT id, inicio, fim_exclusivo FROM public.ed02_assinatura_periodo
      WHERE empresa_id = :empresa_id AND inicio <= :agora AND fim_exclusivo > :agora
      ORDER BY fim_exclusivo DESC LIMIT 1
    """), {'empresa_id':empresa_id, 'agora':agora}).mappings().one_or_none()
    return {'vigente': bool(row), 'fim_exclusivo': row['fim_exclusivo'] if row else None}
