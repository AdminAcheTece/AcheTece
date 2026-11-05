# AcheTece — ED02.5 V5.10.5 — Homologação HTTP + PostgreSQL pós-merge

## Objetivo

Verificar o `main.py` **realmente integrado** na branch `ed02-assinaturas-vigencia-ledger`, após o PR #2 (merge de referência `303d29f`) e a correção da suíte legada V5.10.4. Nenhuma versão candidata é gerada. Não se substitui `main.py`, nem se modifica o módulo shadow.

Esta etapa observa as quatro superfícies ED01 (`index`, `exportar`, `utils_empresas_json`, `empresa_perfil`), primeiro com o shadow desligado, depois com o observador ligado **somente no processo de testes**. A lógica pública ED01 continua a decidir visibilidade.

## Escopo de segurança

- ÚNICA branch autorizada: `ed02-assinaturas-vigencia-ledger`.
- Apenas GitHub Actions acionado por **push** do ator `AdminAcheTece` e commit de autorização exato.
- Banco autorizado: `achetece_ed01_test`, servidor PostgreSQL externo canônico de homologação.
- URL no segredo GitHub `ED02_TEST_DATABASE_URL`, **não revelar, colar em chat ou adicionar ao commit**.
- Conexões SQLAlchemy forçadas com `default_transaction_read_only=on`. Teste verifica `SHOW transaction_read_only` e `SHOW default_transaction_read_only`.
- O bootstrap que cria tabelas e o analytics que registra eventos são desativados **somente na cópia importada em memória do processo de testes**, não no código do repositório.
- Todas as flags financeiras OFF; e-mail suprimido; sem chamadas de webhook e sem pagamentos reais.
- O shadow ON nesta etapa é local ao runner: **não habilitar a flag no Render**.
- Nenhuma alteração na branch `main`, nenhum merge adicional, nenhum deploy Render e nenhuma migração de banco.
- O serviço Render de homologação deve continuar com **Auto-Deploy Off**. Último deploy Live conhecido: `04fbdca`.

## 1. Publicação inicial — sem banco

No GitHub, abra a branch `ed02-assinaturas-vigencia-ledger` e publique **somente** estes arquivos:

Na **raiz do repositório**:

- `test_ed02_5_v5105_pg_posmerge.py`
- `test_ed02_5_v5105_seguranca.py`
- `ED02_V5105_MANIFESTO.json`
- `LEIA_ME_ED02_5_V5105.md`

Na pasta `.github/workflows/`:

- `ed02-v5105-posmerge-readonly.yml`

**Não incluir `main.py`, arquivos do banco, URLs, segredos ou ZIP de projeto antigo.**

Commit sugerido:

`ED02.5 V5.10.5 - Prepara homologacao pos-merge somente leitura`

Esperado: workflow **ED02 V5.10.5 - Homologacao pos-merge Flask PostgreSQL READ ONLY** com trabalho **Integridade e regressao pos-merge SEM banco** aprovado, e trabalho PostgreSQL **Skipped**. A suíte do workflow inclui 14 testes de segurança novos, 35 regressões V5.10.1 corrigidas e a seleção de 180 testes V5.10.3 (a contagem pode ser mostrada separadamente).

**Se qualquer teste falhar, parar.** Não executar o gatilho PostgreSQL.

## 2. Execução real — somente com autorização independente

Após a aprovação da publicação inicial, editar **somente** o arquivo existente `ED02_HOMOLOG_EXECUTION.txt`, adicionando uma linha descritiva da V5.10.5. Commit com a mensagem EXATA:

`ED02_RUN_HTTP_PG_ED025_V5105_READONLY`

O segundo job deve executar:

1. `test_ed02_5_v5105_pg_posmerge.py --mode off` — oito requisições HTTP Flask originais.
2. `test_ed02_5_v5105_pg_posmerge.py --mode shadow` — as mesmas oito requisições com observação shadow SQL real.
3. Comparação dos resultados semânticos de HTTP/CSV/JSON; confirmação de perfil 404, empresa pendente oculta, read-only ativo e inventário `empresa` intacto.

Ele exige a coorte atual de homologação com **uma empresa**, `id=1`, pagamento pendente e `data_pagamento=NULL`. Se o inventário do banco mudar, o teste **bloqueia** e precisa ser revisado antes de novo disparo; NÃO adaptar o teste para liberar empresas por conta própria.

## 3. Evidências esperadas

```
PASSOU: V5.10.5 main.py POS-MERGE integrado; hashes aprovados
PASSOU: PostgreSQL homologacao READ ONLY; empresa original preservada
PASSOU: 8 respostas HTTP, modo=off; shadow=0 rotas
PASSOU: 8 respostas HTTP, modo=shadow; shadow=4 rotas
PASSOU: zero DML permitido, sem webhook, e-mail, pagamento ou deploy
```

Essas mensagens são **critérios futuros**, não resultados de execução já obtidos. O job publicará relatórios JSON sanitizados como artefatos GitHub.

## 4. Limitações e próximos gates

Este ensaio exercita o cliente de testes Flask sobre o PostgreSQL verdadeiro, **não** a URL pública do serviço Render e **não** o ciclo financeiro completo. Não comprova pagamento real no Mercado Pago, webhook real, renovações reais, estornos ou clientes históricos. Também não testa o bootstrap de migração porque ele é suprimido deliberadamente para garantir somente leitura.

Depois da aprovação, revisar checklist de flags e SHA Live no Render antes de considerar um **deploy manual separado** no serviço `achetece-ed01-teste`. **Não ativar pagamentos ou publicar ED02 em produção.**
