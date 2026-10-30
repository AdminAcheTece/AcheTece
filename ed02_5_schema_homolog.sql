-- ED02.5 V2: SOMENTE achetece_ed01_test. Aplicar manualmente em DBeaver
-- conectado ao banco homologado. Nunca aplicar na producao.
BEGIN;
DO $$ BEGIN
 IF current_database() <> 'achetece_ed01_test' THEN
  RAISE EXCEPTION 'ED02.5: banco nao autorizado';
 END IF;
END $$;
CREATE TABLE IF NOT EXISTS public.ed02_assinatura_periodo (
 id BIGSERIAL PRIMARY KEY,
 empresa_id BIGINT NOT NULL REFERENCES public.empresa(id),
 contrato_id BIGINT NOT NULL REFERENCES public.contratacao_checkout(id),
 provedor VARCHAR(40) NOT NULL DEFAULT 'mercadopago',
 payment_id VARCHAR(160) NOT NULL,
 plano VARCHAR(16) NOT NULL CHECK (plano IN ('mensal', 'anual')),
 inicio TIMESTAMPTZ NOT NULL,
 fim_exclusivo TIMESTAMPTZ NOT NULL,
 criado_em TIMESTAMPTZ NOT NULL DEFAULT now(),
 CONSTRAINT ed02_vigencia_intervalo_chk CHECK (fim_exclusivo > inicio),
 CONSTRAINT ed02_vigencia_pagamento_uniq UNIQUE (provedor, payment_id),
 CONSTRAINT ed02_vigencia_contrato_uniq UNIQUE (contrato_id)
);
CREATE INDEX IF NOT EXISTS ed02_vigencia_empresa_fim_idx
 ON public.ed02_assinatura_periodo (empresa_id, fim_exclusivo DESC);
COMMIT;
