# AcheTece ED02.5 V5.6 — Inventário histórico e classificação conservadora

**Escopo:** auditoria somente leitura. Não altera `main.py`, não modifica a visibilidade, não migra contratos, não ativa pagamentos e não envia e-mails.

## Arquivos completos
- `ed02_5_v56_elegibilidade.py`: classificador **apenas de auditoria**, sem integração à busca.
- `test_ed02_5_v56_elegibilidade.py`: 7 testes unitários.
- `test_ed02_5_v56_auditoria_pg.py`: inventário agregado por status, plano, legado e presença/vigência ED02; sem nomes, e-mails ou dados pessoais.
- `.github/workflows/ed02-shadow-tests.yml`: workflow completo preservando todos os trabalhos existentes + job protegido de inventário.

## Regras de classificação (não equivalem à ativação pública)
- Status diferente de `ativo`: `BLOQUEADO_STATUS`.
- Status `ativo` e intervalo ED02 vigente: `ED02_VIGENTE_AUDITORIA`.
- ED02 presente porém não vigente: `ED02_SEM_VIGENCIA_ATUAL`.
- Legado com `data_pagamento` mas sem ED02: `HISTORICO_REQUER_CONCILIACAO`.
- Sem prova financeira: `SEM_COMPROVACAO_FINANCEIRA`.
- Intervalos inconsistentes: `REVISAR_PERIODO_INVALIDO`.

A regra real ED01 de visibilidade (incluindo durações legadas) permanece **intocada**. Nunca ocultar ou ativar assinantes antigos sem conciliação e revisão do direito de vigência.

## Publicação
Na branch `ed02-assinaturas-vigencia-ledger`, instalar os três Python na raiz, substituir o workflow completo, colocar este README na raiz. Commit de preparação: `ED02.5 V5.6 - Prepara auditoria historica somente leitura`.

Esperado: 158 testes existentes + 7 novos = 165 testes. Trabalho PostgreSQL deve ficar *skipped*.

Somente após validar a suíte, autorizar leitura com commit **exato** `ED02_RUN_HISTORICO_ED025_V56` após acrescentar uma linha ao arquivo `ED02_HOMOLOG_EXECUTION.txt`.

O job protegida usa `ED02_TEST_DATABASE_URL` e impõe `SET TRANSACTION READ ONLY`; valida URL, banco real e estrutura ED02 antes da consulta. O resumo só traz contagens agregadas. Não contém DML.

**Proibido:** rodar migrações, executar SQL manual, alterar Render, habilitar flags, fazer merge para `main`, publicar nova regra de elegibilidade.
