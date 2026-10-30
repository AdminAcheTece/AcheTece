# ED02.5 V5.5 — Concorrência HTTP e idempotência simulada

## Escopo comprovável

- Duas requisições HTTP concorrentes ao **webhook extraído do `main.py`** por AST, com dois `test_client` independentes, cabeçalhos HMAC calculados usando o validador real da aplicação.
- Processador de pagamento **simulado** com estado atômico e contador: uma aplicação e uma repetição.
- Assinatura inválida retorna 401 antes de chamar o processador.
- Falha de processamento ou ausência de confirmação retorna 503, sem expor detalhes sensíveis.
- Nenhuma chamada externa, envio de e-mail, alteração em banco ou no Render.

## Limite de evidência

**Este teste NÃO prova** duas transações PostgreSQL com commit concorrente, nem garante que o processador real implemente exclusão mútua em produção. A V5.4 prova bloqueio real e rollback, mas em ensaio diferente. Para liberação financeira ainda é necessário testar dois webhooks HTTP com **sessões independentes e commits persistentes**, usando fixtures exclusivas isoladas no banco de homologação, plano de limpeza e inspeção de resíduos. Não habilitar a produção com base neste pacote.

## Publicar na branch ED02

1. Enviar `test_ed02_5_v55_http_concorrente.py` para a raiz.
2. Substituir `.github/workflows/ed02-shadow-tests.yml` pelo arquivo completo deste pacote, somente se o workflow não recebeu alterações posteriores.
3. Opcional: enviar este documento à raiz.
4. Commit: `ED02.5 V5.5 - Adiciona concorrencia HTTP simulada e regressao`

Meta da suíte comum: **154 testes** (150 anteriores + 4 novos). Não há gatilho de banco na V5.5; o teste de banco com commit real será uma etapa distinta e requer revisão da limpeza e das fixtures.
