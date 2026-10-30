# ED02.5 V5.7.4 — Diagnóstico protegido inicial HTTP + PostgreSQL

## Escopo demonstrável
- A rotina usa um Flask `test_client` em uma rota de diagnóstico **isolada**, mais SQLAlchemy efetivo no PostgreSQL `achetece_ed01_test`.
- Somente transações READ ONLY: sem DML e sem criação de fixtures; não exige limpeza.
- Confirma que empresa id=1 pendente/sem data e id inexistente não são expostas no diagnóstico.
- Não executa rotas públicas originais e **não encerra a homologação integrada V5.7.4**.
- Para a prova integral futura, executar home, CSV, JSON e perfil originais com conexão à base real e fixtures transacionais, após revisar modelo, templates e inicialização da aplicação completa.

## Publicação
Copie os dois arquivos `.py` à raiz e o workflow completo para `.github/workflows/ed02-shadow-tests.yml`, conferindo o diff atual antes da substituição. Commit ordinário: `ED02.5 V5.7.4 - Prepara diagnostico HTTP PostgreSQL protegido`. O trabalho PostgreSQL ficará skipped.

## Gatilho posterior (apenas após validar os 4 testes)
Acrescente linha a ED02_HOMOLOG_EXECUTION.txt na branch ED02 e faça um commit cuja mensagem seja exatamente `ED02_RUN_HTTP_PG_ED025_V574`.

## Segurança
Não executar na produção. Não compartilhar URL/senha. Não ativar flags financeiras no Render. Não realizar merge em main. Sem Mercado Pago, e-mail ou atualização de empresas.
