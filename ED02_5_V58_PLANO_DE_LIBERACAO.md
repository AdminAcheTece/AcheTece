# AcheTece · ED02.5 V5.8 — Plano de gates até a ativação

**Estado inicial:** BLOQUEADO, sem permissão operacional. O arquivo `ED02_5_V58_GATE_ATIVACAO.json` é **somente documental**.

### Gate A — Segurança e conciliação (próxima entrega)
- Auditar resultados ED01 versus candidatos ED02 sem mudar a consulta pública.
- Comparar coortes: legado visível sem ED02; ED02 vigente mas ED01 oculto; status bloqueado; estorno/contestação; pagamentos e contratos sem correspondência; período expirado e sobreposto.
- Aprovar decisões de negócio: vigência mensal/anual, períodos antigos, antecipação, carência, reembolsos, inadimplência e reversão.
- Medir e reduzir divergências; alterações de direito devem ter justificativa e evidência.

### Gate B — Integração de visibilidade com flag desativada
- Implementar filtro ED02 real em SQL (não apenas em Python), com plano de execução e testes de paginação/CSV/JSON/perfil.
- Garantir fail-closed, inclusive timeout e falha do banco, e preservação de clientes legados conciliados.
- Testar migração de regra por coortes em homologação, com toggles, métricas, autorização e reversão.

### Gate C — Financeiro e experiência ponta a ponta
- Confirmar no Mercado Pago modalidade mensal recorrente e anual em pagamento único parcelável, com valor e taxas corretas.
- Comprovar checkout, contrato, webhook assinado, idempotência, ativação, renovação, cancelamento, pagamento tardio, estorno, chargeback, conciliação e alertas de renovação; testes em sandbox, nunca cobrar clientes para homologar.
- **Bloqueio conhecido a retestar:** o teste histórico `test_replayed_approved_payment_resets_legacy_payment_date__known_issue` da referência analisada aprova uma observação de que um replay altera `data_pagamento`. Verificar no HEAD atual se isso ainda ocorre e exigir correção comprovada para evitar prorrogar vigência sem novo pagamento. Um resultado `ok` nesse teste antigo **não equivale** à correção do defeito.
- Reexecutar cadastro/login → painel → plano → checkout → confirmação → busca pública, inclusive desktop e celular.

### Gate D — Release
- Backup recuperável, matriz de monitoramento/alertas, rollback ensaiado, observabilidade sem dados sensíveis, revisão de segurança e privacidade.
- Revisão do diff entre branch ED02 e `main`; congelamento de código e aprovação humana explícita de publicação.
- Ativação gradual apenas após autorização própria. **Este pacote não faz deploy nem merge.**

### Observações de código
- O snapshot de referência analisado inclui visibilidade ED01 `Empresa.assinatura_ativa` com janelas de **35/370 dias**. Confirmar se isso permanece na branch atual antes de qualquer alteração.
- Há uma rota administrativa de geração de teares com filtro simplificado `status_pagamento == 'ativo'`; revisar separadamente, sem confundir com busca pública.
- O template de perfil analisado não usa `Empresa.nome` como fallback do título; isso é uma pendência de UX independente da política financeira.
- O ensaio R3 concluiu home, filtro, CSV, JSON, perfil e rollback no PostgreSQL de homologação; **não** demonstra aplicação da política ED02 às buscas.

**Gates inicialmente abertos:** nenhum. O preenchimento do arquivo JSON não altera runtime e tampouco cria uma autorização tácita.
