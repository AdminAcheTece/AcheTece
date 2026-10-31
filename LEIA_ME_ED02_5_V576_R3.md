# AcheTece · ED02.5 V5.7.6 R3 — Correção do título do perfil no ensaio PostgreSQL

## Diagnóstico de origem: GitHub Actions V5.7.6 R2 #5

O R2 confirmou com resultado `PASSOU`:
- fixtures isoladas de outras conexões PostgreSQL;
- a mesma transação ORM encontrou uma empresa e um tear elegíveis;
- home, filtros, paginação, CSV e JSON respeitaram a elegibilidade;
- rollback e auditoria independente sem resíduos e com baseline intacto.

A única afirmação reprovada foi:

`AssertionError: FALHOU: perfil ativo nao contem empresa`

A função auxiliar `get` havia confirmado resposta HTTP 200 antes da afirmação. O problema era a expectativa do teste sobre o nome que apareceria no HTML do perfil.

Na versão de `templates/empresa_perfil.html` revisada, o cabeçalho utiliza:

`{{ empresa.apelido or empresa.nome_fantasia or empresa.razao_social or 'Malharia' }}`

Entretanto, o modelo `Empresa` contém `nome` e `apelido` e a fixture R2 preenchia apenas `nome`. Com `apelido=NULL`, o Jinja pode mostrar simplesmente `Malharia`, sem a string `nome`. Assim, o teste podia falhar mesmo com HTTP 200 e assinatura vigente.

## Ajuste pontual R3

1. As 4 fixtures agora preenchem **`apelido=name`**, além de `nome=name`, respeitando a coluna real `Empresa.apelido` (`VARCHAR(50)`, única). O maior identificador R3 tem 42 caracteres.
2. O ensaio verifica que o perfil ativo respondeu HTTP 200, contém o identificador da empresa fictícia e usa esse nome no título `<h1 class="title">`.
3. Os perfis pendente, vencido e sem data continuam exigindo HTTP 404.
4. Novo gatilho exclusivo **`ED02_RUN_ELIGIBILITY_ED025_V576_R3`**; prefixo identificável R3.
5. Todos os demais controles transacionais, de leitura, rollback e auditoria da R2 são mantidos; **`main.py` e templates não são modificados**.

## Proteções inalteradas

- Exclusivamente PostgreSQL `achetece_ed01_test` no host autorizado.
- Branch `ed02-assinaturas-vigencia-ledger`, ator AdminAcheTece, evento push e mensagem de commit exata.
- Nenhum commit SQL. Quatro empresas e quatro teares temporários dentro de uma transação externa.
- IDs positivos explícitos, sem avançar sequences do banco.
- Consultas Flask-SQLAlchemy forçadas à conexão do ensaio; teste independente comprova que outras conexões não enxergam fixtures.
- Bloqueio de DML/DDL durante requisições HTTP.
- `finally` executa rollback externo e auditoria por segunda conexão, inclusive caso ocorram erros nas assertions.
- Sem pagamentos reais, sem e-mails, sem mudar política ED01/ED02, sem Render e sem merge em `main`.

## Arquivos completos

- `test_ed02_5_v576_elegibilidade_rollback.py`: substituir a versão R2 na raiz do repositório.
- `test_ed02_5_v576_safety.py`: substituir a versão R2 na raiz.
- `ed02-v576-elegibilidade-rollback.yml`: substituir o workflow na pasta `.github/workflows/`.
- `LEIA_ME_ED02_5_V576_R3.md`: adicionar à raiz.

## Etapa A — Preparação sem PostgreSQL

Commit na branch ED02 com a mensagem:

`ED02.5 V5.7.6 R3 - Corrige nome exibido no perfil de teste`

Resultado esperado: **12 testes de segurança passam**; o job real PostgreSQL fica **Skipped**.

## Etapa B — Ensaio transacional (somente após Etapa A)

Adicionar uma linha explicativa ao arquivo `ED02_HOMOLOG_EXECUTION.txt` na branch ED02 e criar um commit de mensagem EXATA:

`ED02_RUN_ELIGIBILITY_ED025_V576_R3`

O trabalho protegido deverá verificar: ativo visível, bloqueados ocultos, home/filtros/CSV/JSON/perfis reais, rollback e zero resíduos no PostgreSQL.

**Se houver falha, não repetir nem modificar `main.py`: analisar o log e conferir o baseline antes de qualquer nova execução.**

## Risco de interface identificado, fora deste patch

O template atual não inclui `empresa.nome` como fallback no título do perfil. Empresas reais sem `apelido` podem aparecer como `Malharia` no cabeçalho. Isso merece correção de UX em etapa própria e revisão de template/contrato de cadastro, sem relação com a autorização financeira ED02 e **não foi alterado na R3**.
