# AcheTece — ED02.5 V5.10.2 — Gate 2: regressão operacional isolada

**STATUS:** PACOTE DE VERIFICAÇÃO. NÃO APLICA O `main.py`, NÃO FAZ DEPLOY, NÃO ATIVA FINANCEIRO.

## Objetivo e pré-condições

O Gate 1 já aprovou o pacote candidato V5.10.2. O Gate 2 **simula a substituição** em uma cópia temporária integral do repositório e executa uma bateria de regressões financeiras, shadow, HTTP, ED01, vigência e compatibilidade. A branch conectada ao Render **não deve** receber uma substituição de `main.py` nesta etapa.

O executor verifica os SHA256 registrados em `ED02_V5102_RELATORIO.json`, compara quatro funções de rota originais por AST e valida a versão atual do `main.py`. Se houver qualquer alteração desde o Gate 1, o processo para (fail-closed). Não cria ou altera banco de dados remoto.

**Atenção:** a regressão offline cobre testes com simulações/SQLite e não equivale a homologação financeira real com Mercado Pago, e-mails, renovação/estornos ou PostgreSQL com commit. A aprovação do Gate 2 não autoriza ativação comercial.

## Passo 1 — Branch temporária de auditoria

No GitHub, partindo **exatamente** da branch `ed02-assinaturas-vigencia-ledger` aprovada no Gate 1, crie uma nova branch chamada:

`ed02-v5102-gate2-auditoria`

A branch temporária serve para impedir que o Render `achetece-ed01-teste` realize um auto-deploy ao receber arquivos de teste ou mudanças no `main.py`. Confirme que **nenhum serviço Render** utiliza essa nova branch. Não crie a partir de `main`.

## Passo 2 — Publicação segura

Publique apenas estes três arquivos, mantendo os diretórios relativos:

- `ed02_v5102_gate2_runner.py` na raiz
- `test_ed02_v5102_gate2_runner.py` na raiz
- `LEIA_ME_ED02_5_V5102_GATE2.md` na raiz
- `.github/workflows/ed02-v5102-gate2-regressao-isolada.yml` na pasta `.github/workflows`

Não substitua `main.py` nem `ed02_5_v510_http_shadow.py`. Os arquivos integrais `main_V5102_CANDIDATO.py`, `ed02_5_v510_http_shadow_V5102_CANDIDATO.py`, `main_V5101_BASE.py` e `ED02_V5102_RELATORIO.json` já devem estar na branch, herdados da preparação anterior.

Mensagem sugerida de commit:

`ED02.5 V5.10.2 Gate 2 - Regressao isolada sem deploy`

## Passo 3 — Resultado esperado no GitHub Actions

Abra **ED02 V5.10.2 - Gate 2 Regressao Isolada SEM Deploy** e examine:

1. `Preflight V5102 sem alterar arquivos`: executará 11 testes de segurança e validará hashes, quatro rotas e as flags.
2. `Regressao em copia temporaria SEM Deploy`: gerará a cópia temporária, substituirá nela `main.py` e o módulo shadow, executará os 18 módulos de regressão (17 sobre o candidato e o contrato histórico do gerador V5.10.1 sobre uma baseline reconstruída e isolada) e descartará ambas as cópias.

Artefatos gerados: `ED02_V5102_GATE2_PREFLIGHT` e `ED02_V5102_GATE2_RESULTADOS` com JSON e log. Nenhum artefato deve conter URLs de banco, tokens ou credenciais.

Se algum trabalho falhar, **não faça merge, não force bypass e não repita automaticamente**. Envie os trechos finais do log para correção direcionada. O runner nunca modifica o `main.py` do checkout.

## Gate 3 — Aplicação controlada futura à branch ED02 (não autorizada por este pacote)

Após Gate 2 aprovado, deve-se: verificar novamente o HEAD e os hashes, confirmar proteção/pausa do Auto-Deploy do Render de homologação, criar ponto de reversão e somente então substituir **arquivos completos** `main.py` e `ed02_5_v510_http_shadow.py` na branch `ed02-assinaturas-vigencia-ledger`. Isso exige uma autorização separada de aplicação e testes pós-deploy. O histórico `main` (produção) permanece intacto.

O gate financeiro ED02 continua bloqueado. Nunca habilitar `ED02_V510_REPLAY_HOMOLOG`, `ED02_V510_HTTP_SHADOW_HOMOLOG`, `ED02_VIGENCIA_WRITE_HOMOLOG` ou flags associadas em produção.

## Limitações técnicas e critérios

- Testes executados em cópia descartável com variáveis de ambiente sem credenciais.
- Os módulos de regressão são fixos e exigidos; um arquivo ausente reprova a execução.
- Uma falha do ambiente de dependências ou da suíte também reprova o Gate 2; não ignorar testes.
- O teste histórico que registra o defeito legado com flags desligadas **não** comprova correção daquele caminho. O modo corrigido só opera em homologação até a liberação formal.
- A divergência shadow `CONVERGENTE_OCULTO_ED01` foi reclassificada como convergência; divergências reais continuam contabilizadas sem afetar a busca.
- Nenhum teste de conexão PostgreSQL real, cobrança real, e-mail ou webhook de produção é disparado neste Gate 2.

## Resultados já observados no desenvolvimento do pacote

- 11 testes unitários do executor: **PASSARAM**.
- Pré-voo de integridade em cópia local: **PASSOU**.
- 18 testes da baseline de geração V5.10.1: **PASSARAM**.
- Regressão parcial do candidato em ambiente local: **138 testes passaram e três módulos HTTP não puderam ser importados porque este ambiente não dispõe do Flask**. Esses três módulos continuam obrigatórios no workflow GitHub, que instala `requirements.txt`. Isto NÃO equivale à aprovação da regressão completa no GitHub.
- Nenhum PostgreSQL remoto nem Mercado Pago foi acessado.
