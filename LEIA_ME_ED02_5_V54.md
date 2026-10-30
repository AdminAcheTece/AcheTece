# ED02.5 V5.4 — Ensaio Flask + PostgreSQL protegido

## Escopo

Este pacote adiciona **um teste de integração real**, sem alterar `main.py` nem os módulos financeiros. O teste extrai a função `_processar_pagamento` do `main.py` da branch, substitui exclusivamente a chamada HTTP ao Mercado Pago por dados simulados e usa os módulos reais de validação financeira, registro de ledger e cálculo de vigência ligados ao PostgreSQL de homologação.

O teste executa as chamadas Flask sequencialmente, **não** duas requisições HTTP/webhooks simultâneos completos. Prova bloqueio da mesma empresa em duas conexões PostgreSQL, idempotência sequencial, mensal/anual e renovação. A prova específica de concorrência entre webhooks segue pendente.

## Publicação

1. Copiar `test_ed02_5_v54_flask_pg_integrado.py` para a raiz da branch `ed02-assinaturas-vigencia-ledger`.
2. Substituir integralmente `.github/workflows/ed02-shadow-tests.yml` pela versão incluída, somente se não houver alterações posteriores no workflow remoto.
3. Adicionar este documento à raiz.
4. Commit de preparação: `ED02.5 V5.4 - Prepara ensaio Flask PostgreSQL com rollback`. Apenas os 150 testes anteriores devem rodar; trabalho PostgreSQL fica skipped.
5. Após confirmação de CI aprovado, editar `ED02_HOMOLOG_EXECUTION.txt` na branch e fazer **novo commit** com mensagem exata: `ED02_RUN_FLASK_PG_ED025_V54`.
6. Conferir os logs do trabalho `ED02.5 V5.4 - Flask PostgreSQL transacional com rollback`.

## Controles

- Banco *exato*: `achetece_ed01_test`, host externo de homologação já autorizado, usuário e senha no secret `ED02_TEST_DATABASE_URL`.
- Não alterar secret nem revelar o endereço completo contendo senha.
- `ED02_V54_HOMOLOG_AUTORIZADA=true` somente no trabalho protegido.
- Não ligar as flags financeiras no Render; nem executar SQL manualmente; nem fazer merge em `main`.
- SQL da tabela permanente **não é executado**; pressupõe migração V5.2 homologada.
- A gravação é fictícia e ocorre em transação externa cujo COMMIT é proibido pelo adaptador de teste; depois ocorre ROLLBACK e verificação independente.
- Se o teste falhar, **não reexecutar** até avaliar o log. Verificar integridade via consulta somente leitura se necessário.

## Evidências esperadas

- `PASSOU: segunda conexao bloqueada (55P03).`
- `PASSOU: Flask validou contrato e registrou mensal/anual no banco real.`
- `PASSOU: replay sem duplicidade; renovacao preservada.`
- `PASSOU: sem ativacao de empresa e sem emails.`
- `PASSOU: rollback externo obrigatorio realizado.`
- `PASSOU: segunda conexao confirma zero residuos e estrutura intacta.`

## Limitações expressas

Não prova que a API do Mercado Pago foi consultada de verdade, nem duas chamadas HTTP simultâneas, nem ativação/expiração/visibilidade ED01, nem conciliação de pagamentos históricos. Nenhuma aprovação desse pacote autoriza produção.
