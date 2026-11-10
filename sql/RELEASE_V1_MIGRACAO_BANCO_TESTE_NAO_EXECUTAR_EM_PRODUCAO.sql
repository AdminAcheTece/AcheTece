-- AcheTece release/assinaturas-v1, schema aditivo e protegido.
-- NAO executar na producao nem no staging P03.
-- Somente no banco dedicado: achetece_release_v1_test (ainda nao provisionado).
\set ON_ERROR_STOP on
BEGIN;
DO $guard$
BEGIN
    IF current_database() <> 'achetece_release_v1_test' THEN
        RAISE EXCEPTION 'BLOQUEADO: este script exige o banco dedicado achetece_release_v1_test';
    END IF;
END
$guard$;

ALTER TABLE public.empresa ADD COLUMN IF NOT EXISTS assinatura_ate TIMESTAMP WITHOUT TIME ZONE;

CREATE TABLE IF NOT EXISTS public.billing_intent (
    id SERIAL PRIMARY KEY,
    empresa_id INTEGER NOT NULL REFERENCES public.empresa(id),
    external_reference VARCHAR(100) NOT NULL UNIQUE,
    plano VARCHAR(10) NOT NULL CHECK(plano IN ('mensal','anual')),
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'BRL' CHECK(currency='BRL'),
    status VARCHAR(30) NOT NULL DEFAULT 'created' CHECK(status IN
        ('created','pending','failed','active','authorized','paid','canceled','paused')),
    provider_resource_id VARCHAR(160) UNIQUE,
    checkout_url VARCHAR(600),
    cancel_at_period_end BOOLEAN NOT NULL DEFAULT FALSE,
    created_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    updated_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC')
);

CREATE INDEX IF NOT EXISTS ix_billing_intent_empresa_id ON public.billing_intent(empresa_id);
-- Impede multiplas contratacoes simultaneas para a mesma empresa/plano.
CREATE UNIQUE INDEX IF NOT EXISTS uq_billing_intent_unfinished_per_company_plan
 ON public.billing_intent(empresa_id, plano)
 WHERE status IN ('created', 'pending') OR (plano='mensal' AND status='active');

CREATE TABLE IF NOT EXISTS public.billing_payment (
    id SERIAL PRIMARY KEY,
    intent_id INTEGER NOT NULL REFERENCES public.billing_intent(id),
    provider_payment_id VARCHAR(60) NOT NULL UNIQUE,
    amount_cents INTEGER NOT NULL CHECK(amount_cents > 0),
    currency VARCHAR(3) NOT NULL DEFAULT 'BRL' CHECK(currency='BRL'),
    status VARCHAR(24) NOT NULL,
    period_start TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    period_end TIMESTAMP WITHOUT TIME ZONE NOT NULL,
    processed_at TIMESTAMP WITHOUT TIME ZONE NOT NULL DEFAULT (NOW() AT TIME ZONE 'UTC'),
    CHECK(period_end > period_start)
);

CREATE INDEX IF NOT EXISTS ix_billing_payment_intent_id ON public.billing_payment(intent_id);
COMMIT;
