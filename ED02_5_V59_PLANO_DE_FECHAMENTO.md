# AcheTece — ED02.5 V5.9 · Conciliação e gate financeiro

**Escopo:** somente homologação. Nenhum deploy de produção, alteração de preço,
ativação ED02, migração ou mudança em `main.py` nesta entrega.

## Evidências que este pacote gera

1. Testa a função transacional **real** `process_verified_payment_exact_once`
   com driver PostgreSQL simulado, cobrindo primeiro pagamento, replay do mesmo
   `payment_id`, renovação por outro `payment_id`, conflito de referência, estados
   pendentes e estornados, e bloqueio por banco incorreto.
2. Detecta em `main.py` a escrita histórica de `data_pagamento` no ramo
   `approved`. **Não declara o problema resolvido** e não incorpora um falso
   resultado de liberação.
3. Reutiliza o comparador V5.8 para observar a divergência ED01×ED02, sem
   alterar a resposta de nenhuma rota e sem permitir a publicação de empresas.
4. Inclui rotina opcional e protegida que lê os registros reais de empresa e
   períodos no PostgreSQL autorizado e apresenta **somente contagens agregadas**.
5. Mantém o gate bloqueado, mesmo que o manifesto informe todos os requisitos.

## Pendências reais de liberação

- Reprocessamento no ramo **legado** ainda altera `data_pagamento`. A solução
  correta deve ser persistente por provedor+payment_id, preservar a renovação
  por pagamento *diferente* e passar cenários concorrentes com commit real.
- Estorno ou contestação após um pagamento aplicado precisa de política de
  revogação/revisão demonstrada (o caminho exato-once atual não revoga).
- O V5.9 executa um comparador SQL **fora do request HTTP**; a instrumentação
  shadow diretamente nas quatro rotas continua pendente de um patch separado,
  revisado contra o `main.py` HEAD.
- A auditoria sem dados históricos (uma empresa pendente, zero períodos) não
  comprova conciliação de clientes antigos reais.
- Confirmação do contrato mensal recorrente e do anual parcelável no Mercado
  Pago é gate independente, assim como notificações de renovação e vencimento.

## Decisão técnica

**Não aplicar correção simplista** `if empresa.status_pagamento == 'ativo':
return` no fluxo financeiro: isso impediria renovações legítimas distintas.
Não habilitar flags de pagamento/vigência/visibilidade enquanto a origem de
identidade persistente e a política de estornos não forem validadas.

## Próxima execução

Após testes de preparação aprovados, poderá ser autorizado o trabalho de
PostgreSQL somente leitura pelo commit `ED02_RUN_SHADOW_ED025_V59_READONLY`.
Este trabalho não processa webhooks, não chama Mercado Pago, não executa HTTP e
não muda a visibilidade pública. A fase seguinte deve reunir o patch
**atômico** de deduplicação legado/ED02 e a instrumentação HTTP shadow,
preferencialmente em um único pacote para homologação transacional.
