# ED02.5 V5.2 — Migração persistente restrita à homologação

## Artefatos completos

- `ed02_5_v52_migration.py` — migração com guarda de banco/host, pré-requisitos, lock, DDL transacional, verificação de constraints e segunda conexão
- `test_ed02_5_v52_migration.py` — cinco testes locais de segurança
- `.github/workflows/ed02-shadow-tests.yml` — workflow completo preservado e job protegido novo

## Publicação em duas etapas

1. Na branch `ed02-assinaturas-vigencia-ledger`, adicionar os dois scripts Python na raiz e substituir o workflow completo, somente após conferir que não houve mudança concorrente no workflow. Commit NORMAL: `ED02.5 V5.2 - Prepara migracao persistente protegida`. Este commit NÃO autoriza migração.
2. Conferir GitHub Actions: os testes anteriores + cinco novos devem passar. O novo job de migração deve ficar **Skipped**.
3. Apenas depois, fazer um novo commit acrescentando uma linha no `ED02_HOMOLOG_EXECUTION.txt` com mensagem EXATAMENTE `ED02_RUN_MIGRATION_ED025_V52`. Isso autoriza criar a tabela PERSISTENTEMENTE no banco de homologação. Não usar esse gatilho em outro ambiente.
4. Examinar resultado do job `ED02.5 V5.2 - Migracao persistente somente homologacao`. São esperadas duas mensagens PASSOU e STATUS: CRIADA ou JA_EXISTENTE_VALIDADA.

## Guarda de segurança

A rotina exige o host externo Render conhecido, banco `achetece_ed01_test`, credencial do segredo `ED02_TEST_DATABASE_URL`, três tabelas financeiras já existentes, e schema conforme o contrato. A transação usa `lock_timeout`, `statement_timeout`, lock advisory e validação pós-commit. Não chama Mercado Pago, não envia e-mails e não modifica registros das empresas.

## Plano de reversão

A migração é persistente; **não se deve executar `DROP TABLE` automaticamente** como rollback, pois dados financeiros poderiam existir após a criação. Se a validação falhar antes do commit, a transação é revertida pelo PostgreSQL. Se houver falha de rede após o commit, conferir o estado por preflight somente leitura: talvez a tabela já tenha sido criada. Não reexecutar às cegas; a rotina suporta repetição apenas quando o schema presente passa na auditoria. Para reverter uma estrutura já persistida, primeiro desabilitar quaisquer flags V5, confirmar ausência de dados e dependências, registrar backup e solicitar autorização específica para alteração destrutiva. O plano preferido de reversão operacional é manter a tabela inerte e o fluxo antigo preservado.

## Limites

Esta etapa **não** habilita as flags de vigência, não integra a visibilidade pública, não comprova webhooks simultâneos e não modifica banco de produção. Não executar o SQL manualmente no DBeaver. Nenhuma mudança no Render ou merge para main.
