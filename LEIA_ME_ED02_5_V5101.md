# AcheTece — ED02.5 V5.10.1 — Integração Protegida ao Flask

## Escopo e bloqueios

A V5.10 já homologou o processador financeiro de replay no PostgreSQL em uma transação com rollback. Esta V5.10.1 prepara a ligação da função `_processar_pagamento` e do observador HTTP às rotas Flask reais, **sem publicar a versão candidata no Render ou no `main.py` operacional**.

- Branch autorizada: `ed02-assinaturas-vigencia-ledger`.
- Produção: não modificar `main`, não ativar env/flags, não criar deploy intencional de código operacional.
- A V5.10.1 não executa pagamentos, webhooks nem SMTP de verdade.
- `ED02_V510_REPLAY_HOMOLOG=false` e `ED02_V510_HTTP_SHADOW_HOMOLOG=false` por padrão.
- O fluxo legado com as duas flags OFF continua como está, inclusive o risco conhecido de reaplicação; a correção só existe no ramo protegido candidato. Isto **não** representa correção de produção.
- A observação shadow nunca determina a elegibilidade pública, não altera HTML, JSON ou CSV.
- A conciliação de clientes históricos e o tratamento financeiro de estornos continuam bloqueadores de lançamento.

## Arquivos novos a publicar (todos completos)

Na raiz do repositório:

- `aplicar_ed02_v5101_main.py`
- `test_ed02_5_v5101_integracao.py`
- `test_ed02_5_v5101_shadow.py`
- `test_ed02_5_v5101_pg_guard.py`
- `test_ed02_5_v5101_pg_http.py`
- `LEIA_ME_ED02_5_V5101.md`

Em `.github/workflows/`:

- `ed02-v5101-flask-protegido.yml`

A V5.10 anterior já instalou `aplicar_ed02_v510_main.py`, `ed02_5_v510_legacy_once.py`, `ed02_5_v510_http_shadow.py` e seus módulos de dependência. **Não substitua esses arquivos nem `main.py`** nesta primeira publicação.

## 1. Primeiro commit (somente preparação)

Mensagem recomendada:

`ED02.5 V5.10.1 - Prepara integracao Flask com candidato integral`

O workflow deve:

1. Executar **34 testes unitários novos** sem banco externo, além de compilação.
2. Criar `main_V5101_CANDIDATO.py` com base no `main.py` do **HEAD** que acabou de ser publicado.
3. Criar `ED02_V5101_MAIN.diff` e `ED02_V5101_RELATORIO.json` com SHA256 de origem e de candidato.
4. Verificar `git diff --exit-code -- main.py` (original não alterado).
5. Expor um **Artifact** chamado `ED02_V5101_MAIN_COMPLETO_E_DIFF` para download no GitHub Actions.
6. Marcar o trabalho PostgreSQL como **Skipped**.

Se os âncoras mudaram, a ferramenta deve falhar, sem gerar um arquivo parcial.

O arquivo completo `main_V5101_CANDIDATO.py` é um **artefato para revisão**, não deve ser enviado ao repositório com esse nome, importado no Render nem renomeado para `main.py` antes da segunda homologação e de revisão do diff.

## 2. Ensaio protegido PostgreSQL (autorização separada)

Somente após revisar a primeira execução, acrescentar uma linha nova a `ED02_HOMOLOG_EXECUTION.txt` e usar **exatamente** a mensagem de commit:

`ED02_RUN_FLASK_HTTP_ED025_V5101_READONLY`

O job protegido exigirá o segredo `ED02_TEST_DATABASE_URL` previamente configurado. Não colar nem enviar seu valor.

O job constrói o candidato em memória/arquivo a partir do checkout atual, força o driver PostgreSQL com `default_transaction_read_only=on`, e testa as **quatro rotas originais** (8 GETs), primeiro com a observação desligada e depois com a observação ligada no processo de teste. A política ED02 pública e o processamento financeiro continuam desligados. Compara códigos HTTP/CSV/JSON; exige que as quatro superfícies tenham sido auditadas e que a única empresa pendente permaneça bloqueada e sem alterações.

Este teste NÃO executa webhook real nem demonstra o funcionamento de todo o ciclo comercial de checkout/renovação no Flask operacional. Antes de implantação, ainda serão exigidos testes financeiros integrados de webhook/checkout, verificação de rollback e uma revisão de compatibilidade do `main.py` atualizado.

Se alguma execução falhar, NÃO repetir o gatilho automaticamente. Registrar as mensagens antes de alterar código.

## 3. Depois da aprovação

Aprovação desta V5.10.1 significa **candidato validado para integração de homologação**, não autorização para produção. O próximo gate será revisão de diff e substituição controlada do `main.py` **somente na branch de homologação**, confirmação de flags OFF no Render, testes de regressão, operação de webhook simulada, conciliação histórica e plano de reversão.
