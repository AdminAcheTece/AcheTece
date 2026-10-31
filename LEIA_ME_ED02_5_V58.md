# AcheTece — ED02.5 V5.8 · Consolidação de elegibilidade e gate de preparação

## Finalidade

Pacote **não-operacional** e independente do `main.py` para comparar estado observado ED01 com evidência ED02 em modo shadow, classificar lacunas contratuais e registrar bloqueios para uma futura ativação controlada.

**Esta versão não integra as regras novas nas rotas. Não altera visibilidade, assinaturas, dados do PostgreSQL, Mercado Pago, Render ou produção.**

## Arquivos e destinos (todos novos)

- `ed02_5_v58_consolidacao.py` → raiz do repositório.
- `ed02_5_v58_gate_ativacao.py` → raiz.
- `test_ed02_5_v58_consolidacao.py` → raiz.
- `test_ed02_5_v58_gate.py` → raiz.
- `ed02_5_v58_pg_auditoria.py` → raiz; auditoria agregada PostgreSQL, guardada.
- `test_ed02_5_v58_pg_safety.py` → raiz.
- `ED02_5_V58_GATE_ATIVACAO.json` → raiz; todos os prerequisitos `false`.
- `ED02_5_V58_CONTRATO_HISTORICO.md` → raiz.
- `ED02_5_V58_PLANO_DE_LIBERACAO.md` → raiz.
- `LEIA_ME_ED02_5_V58.md` → raiz.
- `.github/workflows/ed02-v58-consolidacao.yml` → novo arquivo na pasta de workflows.

**Não substitua** `main.py`, os workflows históricos, o `.env`, templates ou scripts de teste anteriores. Confira se os arquivos ainda não existem na branch antes de adicioná-los.

## Execução A — publicação segura

1. Publicar os arquivos exclusivamente na branch `ed02-assinaturas-vigencia-ledger`.
2. Commit sugerido: `ED02.5 V5.8 - Consolida politica historica e gate bloqueado`.
3. Abrir GitHub Actions → `ED02 V5.8 - Consolidacao e gate documental protegido`.
4. Esperado: **31 testes específicos** verdes, compilação e mensagem `PASSOU: ED02 V5.8 nao autoriza ativacao.`
5. Se houver falha, não executar qualquer ativação: compartilhar logs.

## Execução local opcional (sem BD)

```sh
python -m unittest -v test_ed02_5_v58_consolidacao test_ed02_5_v58_gate test_ed02_5_v58_pg_safety
```

## Execução B — auditoria PostgreSQL opcional (somente após validar A)

O workflow instala um segundo trabalho, inicialmente **Skipped**, com gatilho restrito `ED02_RUN_CONSOLIDACAO_ED025_V58_READONLY`. Após a execução A passar, adicionar linha neutra a `ED02_HOMOLOG_EXECUTION.txt` e criar commit com essa mensagem exata na branch ED02. O job usa o segredo existente `ED02_TEST_DATABASE_URL`, host e banco de homologação fixos, SQL `SET TRANSACTION READ ONLY`, retorna somente contagens agregadas e não habilita políticas ED02. Não executar o gatilho antes de validar o job A.

## O que falta após essa aprovação

A V5.8 estabelece o **contrato e a preparação do gate**, mas não conclui a migração histórica e nem a integração ED02. O próximo passo deve ser uma auditoria de divergências ED01 vs ED02 no PostgreSQL de homologação, inicialmente só leitura e com agregados; depois, desenvolver o filtro SQL ED02 sob flag desligada e aprovar um ensaio integrado, sem deploy em produção.

## Segurança

O avaliador shadow retorna `autoriza_publicar=False` incondicionalmente. O gate documental retorna `pode_ativar_producao=False` incondicionalmente. Alterar manualmente um JSON não habilita nada na aplicação. Não inserir no repositório segredos, cartões, identificação pessoal ou dumps financeiros.
