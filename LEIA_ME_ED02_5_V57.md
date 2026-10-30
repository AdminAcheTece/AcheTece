# AcheTece — ED02.5 V5.7: matriz histórica e política de elegibilidade (AUDITORIA)

## Escopo aprovado nesta entrega

Apenas código puro de classificação, 13 testes unitários e workflow completo baseado no workflow V5.6 (último pacote conhecido). **Não integra nem substitui a regra pública da ED01**. Não altera `main.py`, Render, Mercado Pago, banco ou registros financeiros. Nenhum caso classificado autoriza publicação automática. Nunca reutilize `avaliar` como filtro público sem uma etapa separada de decisão e migração aprovada.

## Arquivos e instalação

- `ed02_5_v57_politica.py` — colocar na raiz.
- `test_ed02_5_v57_politica.py` — colocar na raiz.
- `.github/workflows/ed02-shadow-tests.yml` — substituir integralmente **somente se** for idêntico à versão V5.6 vigente, exceto pela inclusão da chamada de testes V5.7. Se houver mudanças posteriores no repositório, preservar a versão mais recente e adicionar somente `python -m unittest -v test_ed02_5_v57_politica` à etapa de testes; não reverter jobs.

## Política de decisão (não autoriza publicação)

1. Status diferente de ativo: `BLOQUEADO_STATUS`.
2. Estorno confirmado ou disputa: `REVISAO_FINANCEIRA_OBRIGATORIA`.
3. Plano, datas ou períodos inválidos: `REVISAO_*`.
4. ED02 vigente: `ED02_VIGENTE_AUDITORIA` (diagnóstico apenas).
5. ED02 expirado: `ED02_SEM_VIGENCIA_ATUAL`.
6. Sem ED02, com pagamento histórico: `LEGADO_A_VALIDAR` ou `LEGADO_VALIDADO_AGUARDA_MIGRACAO` se houver comprovação documental separada.
7. Sem prova de pagamento: `SEM_PROVA_FINANCEIRA`.

A prova contratual do legado não pode ser inferida só de `data_pagamento`. Requer conciliação individual, contratos, regras efetivas de duração e eventuais exceções.

## Testes e continuidade

`python -m unittest -v test_ed02_5_v57_politica`

Esperado: 13 testes novos. Se os 165 anteriores forem preservados, a soma esperada é **178**. Isso não é prova de implantação ou elegibilidade pública.

Commit sugerido: `ED02.5 V5.7 - Matriz historica e politica de elegibilidade em auditoria`

### Gate para a próxima etapa

Antes de filtrar busca, perfil, CSV ou endpoints por vigência ED02: inventariar todas as consultas públicas do código atualizado; conciliar direitos legados com evidência; aprovar política comercial de mensal/anual, inadimplência, reembolso e vencimento; realizar testes completos com as flags desativadas e habilitadas, apenas na homologação; verificar as rotas contra a regra ED01. Não executar migração financeira automática nem habilitar flags no Render.
