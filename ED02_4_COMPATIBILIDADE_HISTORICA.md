# ED02.4 — política de pagamentos históricos (proposta; não ativada)

- Pagamento cuja `external_reference` não exista em `contratacao_checkout`: **não ativar e não renovar automaticamente** no modo da guarda financeira.
- Não localizar empresa pelo e-mail do pagador quando a guarda estiver ativa.
- Encaminhar para conciliação administrativa manual com evidência autenticada do Mercado Pago, empresa, contrato, moeda, valor, situação e data.
- Não criar contratação retroativa automaticamente a partir do webhook. Qualquer migração exige script auditável, dupla conferência, plano de reversão e autorização própria.
- Renovação antecipada, estorno, chargeback e duração contratual ainda precisam de decisão de negócio explícita. A regra atual `data_pagamento = now()` não acumula saldo.
- Nenhuma flag ED02 deve ser habilitada em produção até migração e plano de implantação aprovados.
