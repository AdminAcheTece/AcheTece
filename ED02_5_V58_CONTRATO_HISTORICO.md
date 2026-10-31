# AcheTece · ED02.5 V5.8 — Contrato de conciliação ED01 ↔ ED02

**Status:** contrato proposto para auditoria, **não ativado**.

## Fontes e precedência

1. A autorização **pública atual** continua definida exclusivamente no `main.py` pelo critério legado ED01 `Empresa.assinatura_ativa` (status exatamente `ativo`, data de pagamento presente, janela 35/370 dias, conforme código de referência analisado). Nenhuma informação deste pacote altera esse comportamento.
2. O histórico `status_pagamento` + `data_pagamento` não se transforma automaticamente em contrato ED02. O vencimento legado deve ser reconciliado individualmente, preservando acordos comprovados sem liberar pagamentos não comprovados.
3. A tabela `ed02_assinatura_periodo` registra períodos contratuais com intervalo `[inicio, fim_exclusivo)`; a existência de período não substitui a conferência de contrato, pagamento no provedor, valores, moeda e vínculo com empresa, nem prova ausência de estorno posterior.
4. Um estado `ativo` legado **não prova** que existe uma vigência ED02; e uma vigência ED02 **não autoriza** publicar isoladamente uma empresa com status divergente.
5. Estornos, chargebacks, disputa e conflito de datas exigem fluxo específico, trilha de auditoria e decisão de negócio. Não realizar renovação retroativa nem gerar período a partir de e-mail do pagador.
6. A **visibilidade ED01 observada** deve ser medida pelas consultas originais em homologação. O classificador shadow apenas compara esse resultado com a evidência ED02; seus resultados nunca entram no WHERE da home, CSV, JSON ou perfil.

## Matriz mínima de cenários

| Cenário | Leitura diagnóstica V5.8 | Ação antes de ativar ED02 |
| --- | --- | --- |
| Pendente com período ED02 | Bloqueio por status | Revisar vínculo e motivo da pendência |
| ED01 visível, ED02 sem período | Legado a conciliar | Revisão contratual, sem ocultação automática nesta etapa |
| Legado conferido, ainda não migrado | Conciliação pendente de transição | Aprovar metodologia auditável de migração |
| Período ED02 vigente e financeiro conferido | Candidato ED02 (não publicado) | Confirmar equivalência ED01/ED02 e regras de publicação |
| Período ED02 vigente sem comprovação financeira | Conferência obrigatória | Não considerar pagamento confirmado |
| Vencimento exatamente em `fim_exclusivo` | Não vigente | Validar fronteira de vencimento |
| Períodos sobrepostos | Revisão obrigatória | Investigar duplicidade/renovação |
| ED01 visível com estorno/disputa | Alerta financeiro urgente | Verificar estorno e política de suspensão |
| ED02 candidato, ED01 oculto | Divergência | Revisar histórico antes de conceder acesso |

## Informações necessárias à conciliação real

Identificadores internos de empresa e contratação; plano; status e data legados; IDs de cobrança/pagamento, valor, moeda, provedor, status e data autenticados; início/fim dos períodos ED02; eventos de estorno/contestação; eventuais concessões ou acordos contratuais; responsável, data e evidência da aprovação. Guardar dados e evidências em sistemas com acesso restrito: **não colocar PII, credenciais ou payload financeiro em logs do Actions**.

## Critério de saída deste marco

Aprovação de testes unitários e de um manifesto **não constitui autorização de implantação**. Próxima etapa deve comprovar, com dados reais de homologação e sem produção: contagem de divergências, amostras anonimizadas, replay/renovação, casos de estorno, política aprovada e comparação shadow sem mudança nas rotas. Só depois preparar integração de consulta ED02 atrás de flag explicitamente desativada por padrão, com rollback e aprovação própria.
