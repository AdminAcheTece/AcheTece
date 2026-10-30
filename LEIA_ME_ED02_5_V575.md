# AcheTece ED02.5 V5.7.5 — Prova das rotas originais, etapa 1

## Escopo e limites

O script chama **as funções originais** de rota Flask (`/`, `/exportar`, `/utils/empresas_json`, `/empresa/<id>`) importando `main.py` da branch, contra **PostgreSQL real**, sem mocks de consulta e sem alterar `main.py`.

**Não é a homologação integral da V5.7.5.** Verifica somente o registro pendente existente (id=1), uma rota inexistente, CSV/JSON vazios e ausência na home. Não cria cenários de empresa ativa e vencida, nem verifica rollback de fixtures. Esses cenários exigem uma segunda etapa em banco descartável ou isolamento transacional explicitamente garantido, com esquema real conferido. Em caso de divergência do inventário, aborta sem modificar dados.

Segurança: branch, ator, mensagem exata, host e nome do banco obrigatórios; sessão PostgreSQL configura `default_transaction_read_only=on`; desabilita bootstrap/analytics em requisições e desativa envs de escrita ED02. O commit gatilho não aciona pagamentos, não envia e-mails e não faz migração. O import do main pode executar somente leituras de inicialização; qualquer tentativa de DML/DDL deve falhar por read-only do próprio banco.

## Instalação

1. Colocar `test_ed02_5_v575_rotas_originais_pg.py` e `test_ed02_5_v575_safety.py` na raiz.
2. Colocar `ed02-v575-original-http-pg.yml` em `.github/workflows/`. **Não substituir** `ed02-shadow-tests.yml`, evitando perda de jobs antigos.
3. Commit comum primeiro: `ED02.5 V5.7.5 - Prepara rotas originais PostgreSQL readonly`. Esse commit deixa o trabalho protegido como *Skipped*.
4. Conferir no GitHub Actions o workflow anterior e, somente após aprovar, atualizar arquivo de autorização textual e commitar com a mensagem **exata** `ED02_RUN_ORIGINAL_HTTP_PG_ED025_V575` na branch ED02. Esse gatilho executará o PostgreSQL real em modo somente leitura.
5. Se falhar, enviar logs e **não repetir gatilho** sem diagnóstico.

Os quatro testes de segurança podem ser executados sem banco: `python -m unittest -v test_ed02_5_v575_safety`.

**Não habilitar flags financeiras, não alterar Render nem fazer merge em `main`.**
