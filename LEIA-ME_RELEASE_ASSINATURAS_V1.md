# AcheTece — Release de assinaturas v1 (pacote técnico, **NÃO PUBLICAR**)

**Revisão de instalação:** v1.1 — compatibilidade com token de teste do Mercado Pago (prefixo APP_USR).

**Base:** exportação `AcheTece-main (1).zip` fornecida em 10/11/2026; SHA-256 de `main.py` de origem:
`68d9ec2255d190de18acb8b61918d8dcc45fcc820e37948461195690eb12b570`.
**Branch prevista:** `release/assinaturas-v1`, criada a partir da `main`.
**Produção e P03:** não sofreram qualquer alteração. **Este pacote não está homologado para dinheiro real.**

## O que foi implementado no código

1. **Checkout por plano:** mensal com API de assinaturas Mercado Pago `/preapproval` e link de autorização; anual com Checkout Pro de pagamento único. Sem criar cobrança no GET: tela de confirmação seguida de POST com CSRF.
2. **Contratação registrada:** modelos `billing_intent` e `billing_payment` com identificadores de contratação, preço em centavos, plano, moeda, ID do provedor e período adquirido.
3. **Conciliação:** webhook exige HMAC `x-signature`, busca autenticada de pagamento, vínculo exato à contratação local, recebedor, moeda, valor, modalidade teste/produção e, quando presente, ID da assinatura; não usa e-mail do pagador para localizar empresa.
4. **Idempotência:** ID de pagamento exclusivo no banco, verificação adicional com bloqueio transacional, reconhecimento de notificações repetidas, renovação por novo pagamento e restrições de intenção pendente. Estornos e chargebacks identificados revogam o período conforme pagamentos válidos remanescentes.
5. **Vigência explícita:** campo `empresa.assinatura_ate` com periodicidade de um mês/um ano de calendário. Mantém compatibilidade de leitura com períodos legados para empresas anteriores à release.
6. **Cancelamento:** POST autenticado para interromper cobrança mensal futura no Mercado Pago, mantendo o acesso já pago até a data de vencimento.
7. **Proteções:** SECRET_KEY obrigatória com 32+ caracteres; CSRF em formulários; limites por IP no login; rotas de seed/debug e impersonação desabilitadas; modificação administrativa manual de status bloqueada; bloqueio contra exclusão direta de empresas com histórico financeiro.
8. **Busca:** corrigido seletor de cidades usando apenas malharias/teares elegíveis já calculados no servidor. Ajustada largura/rolagem da tabela desktop, **sem trocar as cores ou o design da `main`**.
9. **Preço canônico:** `ACHETECE_PRICE_MONTHLY_BRL` e `ACHETECE_PRICE_YEARLY_BRL` alimentam painel, vitrine e cobrança. Os preços públicos de referência (R$ 47,00 / R$ 479,40) são **provisórios** até confirmação comercial e configuração explícita.

## Pontos que ainda precisam de homologação para GO-LIVE

- Fluxo completo autenticado comprador/malharia/admin com contas fictícias separadas e autorização por objeto.
- API real de teste Mercado Pago: verificar elegibilidade para assinaturas no vendedor, criação e link `init_point`, id de assinatura, `external_reference` e dados efetivamente retornados por pagamentos recorrentes; simular notificações assinadas com segredo do sandbox.
- Renovação, suspensão, estorno, chargeback, cancelamento e falhas de rede/concorrência com banco PostgreSQL real. Os testes automatizados atuais usam mocks de API e análise estática; **não são uma confirmação de pagamento**.
- Incluir conciliação de webhook para eventos `subscription_authorized_payment` se a documentação da conta de vendedor / payload real exigir. Por enquanto esse evento é reconhecido sem ativar a conta; ativação exige `payment` aprovado.
- Conciliação de clientes antigos ativos e contratos legados na migração, com base em decisão comercial.
- Backup testado, migração com rollback, cookies/OAuth no host de teste, SMTP/Resend, textos legais de cancelamento, taxas/parcelamento do anual, regras de estorno e descontos.
- A limitação de requisições com `memory://` opera por processo; **em produção com múltiplos workers, configurar backend compartilhado (por exemplo Redis)** antes de considerar defesa contra força bruta completa.
- `SECRET_KEY` nova invalida sessões anteriores. Antes do deploy real, verificar se a chave atual atende o requisito e planejar impacto de troca.

## Configurações exigidas no **ambiente de teste isolado**, depois de criada infraestrutura própria

| Variável | Conteúdo | Observação |
|---|---|---|
| `SECRET_KEY` | valor aleatório forte, mínimo 32 caracteres | **não** salvar no Git |
| `INTERNAL_DATABASE_URL` | URL exclusiva do novo banco de teste | jamais apontar para P03 ou produção |
| `ALLOW_SQLITE_FALLBACK` | `0` | não usar SQLite em homologação |
| `DEMO_MODE` | `0` | evitar publicação indevida |
| `ACHETECE_RELEASE_SCHEMA_READY` | `1` após migração concluída | não usar antes da migração |
| `ACHETECE_BILLING_V1_ENABLED` | `1` somente ao testar pagamento | com `0`, checkout e webhook ficam indisponíveis |
| `ACHETECE_BILLING_ENV` | `sandbox` | tokens `TEST-...` apenas |
| `ACHETECE_BILLING_CALLBACK_BASE_URL` | `https://<host-de-teste>` | deve ser HTTPS e apontar para a release em teste |
| `ACHETECE_PRICE_MONTHLY_BRL` | ex.: `47.00` | confirmar antes da venda; formato decimal com ponto |
| `ACHETECE_PRICE_YEARLY_BRL` | ex.: `479.40` | confirmar antes da venda; anual inferior a 12 mensais |
| `MP_ACCESS_TOKEN` | token da aba **Teste** do painel Mercado Pago | pode começar por `APP_USR-` ou `TEST-`; nunca enviar no chat/Git |
| `ACHETECE_MP_TEST_TOKEN_CONFIRMED` | `1` para token de teste `APP_USR-` | definir somente após confirmar visualmente a origem no seletor **Teste** do painel MP |
| `MP_WEBHOOK_SECRET` | segredo de teste do webhook | não informar no chat/Git |
| `MP_COLLECTOR_ID` | ID do vendedor de teste | comparar com `collector_id` do pagamento |
| `MAIL_SUPPRESS_SEND` | `1` | manter no sandbox |
| `SESSION_COOKIE_DOMAIN` | deixar vazio | cookie pertence ao host de teste |
| `RATELIMIT_STORAGE_URI` | opcional, ex. Redis | `memory://` padrão para staging com um worker |

**Não basta definir variáveis para habilitar pagamento real.** As flags devem continuar desligadas até aprovação dos testes e validação das configurações. No ambiente de produção o token pode ter o mesmo prefixo `APP_USR-...` usado por tokens de teste; **o prefixo não comprova o ambiente**. Confirmar a aba de origem no Painel do Mercado Pago, conferir o vendedor de teste (`MP_COLLECTOR_ID`) e jamais copiar tokens de produção para o sandbox. **Não habilitar produção agora.**

## Banco e proteção

O script `sql/RELEASE_V1_MIGRACAO_BANCO_TESTE_NAO_EXECUTAR_EM_PRODUCAO.sql` **exige o nome exato `achetece_release_v1_test`**. Tenta interromper com erro em qualquer outro banco; em especial, não roda no P03 nem no banco `achetece-db` de produção. Nenhum SQL foi executado agora.

## Testes locais concluídos

- `python -m py_compile main.py billing_release.py` — aprovado;
- `python -m unittest discover -s tests -v` — **30 testes aprovados**;
- parse de todos os 41 templates Jinja — aprovado;
- validação estática de formulários POST e proteção CSRF — aprovada.

**Não foi possível executar os testes Flask + PostgreSQL + Mercado Pago em ambiente real a partir deste container**, que não possui os pacotes Flask/MP instalados nem as credenciais de teste. O código precisa de testes de integração reais antes de qualquer deploy comercial.

## Como aplicar (após validação de infraestrutura)

Fornecemos `ACHETECE_RELEASE_ASSINATURAS_V1.patch` e um ZIP de arquivos alterados. A estratégia preferida é fazer `git apply --check` na branch exata, conferir o diff e criar commit **somente na `release/assinaturas-v1`**. O ZIP não inclui dados privados, variáveis ou tokens.

A criação de commit e push deve ocorrer exclusivamente nessa branch, sem merge para `main` até a aprovação de pagamento em sandbox.

## Referências técnicas (Mercado Pago)

- https://www.mercadopago.com.br/developers/pt/reference/online-payments/subscriptions/create-preapproval/post
- https://www.mercadopago.com.br/developers/pt/docs/subscriptions/integration-configuration/subscription-no-associated-plan/pending-payments
- https://www.mercadopago.com.br/developers/pt/docs/subscriptions/additional-content/your-integrations/notifications/webhooks
- https://www.mercadopago.com.br/developers/pt/docs/checkout-pro-preferences/payment-notifications

**Status de engenharia:** código preparado + testes locais aprovados / sandbox e homologação integrada pendentes. **NÃO PUBLICAR.**
