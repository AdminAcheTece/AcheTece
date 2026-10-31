# AcheTece · ED02.5 V5.8 — Estado consolidado e pendências de liberação

**Base:** capturas e logs fornecidos durante a homologação, mais snapshots de código da branch de desenvolvimento disponibilizados ao projeto. **Não** substitui a leitura do HEAD atual no GitHub.

| Evidência anterior | Conclusão limitada ao cenário |
| --- | --- |
| ED02.5 V5.7.6 R3, teste integrado (31/10/2026) | Fixtures transacionais isoladas; home/filtros/paginação; CSV e JSON; perfil ativo HTTP 200 e bloqueados HTTP 404; rollback com zero resíduos |
| ED02.5 V5.7 | Política histórica diagnóstica: não autoriza publicação |
| ED02.5 V5.6 | Auditoria de uma empresa pendente, sem data de pagamento e sem período ED02 no PostgreSQL de homologação |
| ED02.5 V5.5.1 | Ensaios de concorrência de commits reais, idempotência e limpeza auditada |
| ED02.4 | Endurecimento e testes de logs de webhook |

## Faltas que impedem liberação automática

- **ED02 não controla as buscas:** resultados anteriores validaram ED01; o novo gate ainda não está integrado no WHERE das consultas públicas.
- **Conciliação de históricos reais:** ainda não há amostra auditada de clientes antigos suficientes para decidir migração, descontos, carências e contratos divergentes.
- **Risco legado de replay:** no código de referência, `_processar_pagamento` no fluxo legado altera `data_pagamento` em cada `approved`. O teste `test_replayed_approved_payment_resets_legacy_payment_date__known_issue` faz `assertNotEqual` após um replay, evidenciando o comportamento indesejado, embora o teste mostre `ok`. Verificar HEAD antes de corrigir, e corrigir/testar fora deste pacote.
- **Reversões financeiras:** tabela de períodos ED02 não explicita por si só suspensão por chargeback/estorno; política e dados autenticados precisam ser definidos.
- **Checkout e negócios:** mensal recorrente versus anual pagamento único parcelável ainda exigem prova ponta a ponta e confirmação de configuração comercial antes do lançamento.
- **UX:** revisão do fallback de nome no perfil público para clientes sem `apelido` e validação integral dos fluxos login/painel/plano/checkout/retorno.
- **Operação:** backup, alertas, rollback de release e autorização humana formal.

## Resultado esperado V5.8

Entrega de 1) política shadow para comparar decisões ED01 e evidências ED02 sem mudar acesso, 2) gate documental totalmente bloqueado e 3) consulta PostgreSQL protegida para contagens agregadas. **Não** habilita assinatura ED02 para busca e **não** significa aprovação de release.
