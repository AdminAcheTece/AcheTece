# ED02.5 V5.3 — Flask + PostgreSQL persistente: pacote de verificacao protegida

## Alcance real desta entrega

- Quatro testes com mocks exercitam a funcao `_processar_pagamento` do `main.py` da branch: periodo novo, replay, configuracao incompleta e erro de persistencia.
- O trabalho PostgreSQL protegido **somente leitura** valida a tabela persistente, as restricoes e a consulta sem periodo da empresa inexistente.
- Nenhuma chamada ao Mercado Pago, nenhuma cobrança, nenhuma ativacao e nenhum e-mail.
- **NAO** comprova duas requisicoes Flask simultaneas ou uma operacao Flask end-to-end de gravacao persistente. Essas provas continuam pendentes para a etapa seguinte.

## Publicacao

Enviar `test_ed02_5_v53_flask_mock.py` e `test_ed02_5_v53_pg_readonly.py` para a raiz da branch `ed02-assinaturas-vigencia-ledger`; substituir `.github/workflows/ed02-shadow-tests.yml` pelo arquivo completo, desde que nao haja commits mais recentes no workflow. `main.py` e os modulos existentes **nao** devem ser substituidos neste pacote.

Commit normal: `ED02.5 V5.3 - Testes Flask e PostgreSQL de leitura protegida`.
A suite passa de 146 para **150 testes**, se todos os arquivos anteriores estiverem presentes.

Depois da suite verde, gatilho protegido independente:
`ED02_RUN_FLASK_ED025_V53` (via commit exatamente com esta mensagem em `ED02_HOMOLOG_EXECUTION.txt`).

**Nao** ligar `ED02_VIGENCIA_WRITE_HOMOLOG`, outras flags do Render, nem fazer merge para `main`.

## Proximos gates necessarios

1. Teste da transacao completa Flask + PostgreSQL persistente com fixture isolada e limpeza garantida;
2. Dois webhooks HTTP simultaneos e ausencia de duplicacao de periodo/ativacao/email;
3. Conciliacao do legado e regra de vigencia/visibilidade antes de ativacao.
