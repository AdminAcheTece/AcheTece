# AcheTece — ED02.5 V5.1 — Gate de integracao Flask e preflight PostgreSQL

## Escopo verificado
Base: ZIP completo recebido apos a execucao #72, com `main.py` V5 atualizado do pacote anterior.

- Arquivos COMPLETOS: `main.py`, `ed02_5_visibility_gate.py`, `test_ed02_5_v5_gate.py`.
- Workflow COMPLETO: `.github/workflows/ed02-shadow-tests.yml`, com os 6 testes V5 adicionais na suite (135 + 6 = 141).
- Novo script: `test_ed02_5_v51_preflight.py`, somente leitura.
- A integracao Flask continua atras da flag `ED02_VIGENCIA_WRITE_HOMOLOG`, desligada por padrao. As buscas ED01 nao foram modificadas.

## Publicacao segura
1. Conferir se o branch ainda corresponde ao commit V5 da execucao #73; em caso de commits posteriores, comparar antes de substituir arquivos.
2. Copiar os arquivos conforme os caminhos acima para `ed02-assinaturas-vigencia-ledger`.
3. Commit comum sugerido: `ED02.5 V5.1 - Inclui testes Flask e preflight seguro`.
4. A suite automatizada deve somar 141 testes. A execucao comum nao acessa o banco real.
5. Somente APOS 141 testes aprovados, para acionar o preflight, atualizar `ED02_HOMOLOG_EXECUTION.txt` com um novo commit cuja mensagem EXATA seja `ED02_RUN_PREFLIGHT_ED025_V51`.
6. Inspecionar os logs do trabalho `ED02.5 V5.1 - Inspecao PostgreSQL somente leitura`.

## Limites
- O preflight **nao cria, altera ou apaga tabelas**. Mostra somente `presente`/`ausente`, sem credenciais.
- A migration permanente e testes Flask+PostgreSQL integrados NAO estao executados na V5.1. Para esses passos, exige gate proprio posterior, com backup, auditoria de esquema e plano de rollback.
- Nunca executar o SQL manualmente na producao. Nao alterar Render, flags financeiras, webhook ou main.
- Antes de liberar ativacao/visibilidade e obrigatorio homologar dois webhooks Flask concorrentes, conciliar historico e verificar vigencia real.
