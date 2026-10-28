"""ED02.3G.3: autenticidade de Webhooks Mercado Pago. Sem IO e sem dependencia Flask.

Contrato para notificacoes modernas do tipo payment, sem fallback para IPN.
Falha fechada quando cabeçalhos ou data.id nao existem.
A funcao valida a origem; a confirmacao do pagamento deve ser feita pela API MP.
"""
from __future__ import annotations
import hashlib
import hmac
import re
from collections.abc import Mapping

_HEX_SHA256 = re.compile(r"[0-9a-fA-F]{64}\Z")
_TS = re.compile(r"[0-9]{1,20}\Z")
_ID = re.compile(r"[A-Za-z0-9_-]{1,160}\Z")


def validate_mp_signature(*, secret: str, x_signature: str, x_request_id: str,
                          data_id: str, payload_data_id: str | None = None) -> bool:
    """True apenas se a assinatura HMAC bate e os identificadores coincidem.

    data_id deve vir da query string 'data.id' (nao do JSON).
    payload_data_id, quando disponivel, e conferido com a query string.
    Nao chama rede, nao grava em banco, nao registra segredos.
    """
    if not all(isinstance(v, str) and v for v in (secret, x_signature, x_request_id, data_id)):
        return False
    if not (_ID.fullmatch(data_id) and len(x_request_id) <= 200 and
            all(32 <= ord(ch) <= 126 for ch in x_request_id)):
        return False
    if payload_data_id is not None and str(payload_data_id) != data_id:
        return False
    fields = {}
    for chunk in x_signature.split(','):
        if '=' not in chunk:
            return False
        key, value = chunk.strip().split('=', 1)
        if key in fields:
            return False
        fields[key] = value.strip()
    if set(fields) != {'ts', 'v1'}:
        return False
    ts, signature = fields['ts'], fields['v1']
    if not (_TS.fullmatch(ts) and _HEX_SHA256.fullmatch(signature)):
        return False
    manifest = f"id:{data_id};request-id:{x_request_id};ts:{ts};"
    expected = hmac.new(secret.encode('utf-8'), manifest.encode('utf-8'), hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, signature.lower())


def validate_mp_flask_request(req, secret: str) -> bool:
    """Adapter sem efeitos colaterais para request Flask."""
    if req.method != 'POST' or req.args.get('type') != 'payment':
        return False
    data_id = req.args.get('data.id')
    payload = req.get_json(silent=True)
    if not isinstance(payload, dict) or not isinstance(payload.get('data'), dict):
        return False
    if payload.get('type') != 'payment':
        return False
    body_id = payload['data'].get('id')
    if body_id is None:
        return False
    return validate_mp_signature(
        secret=secret,
        x_signature=req.headers.get('x-signature', ''),
        x_request_id=req.headers.get('x-request-id', ''),
        data_id=data_id,
        payload_data_id=str(body_id),
    )
