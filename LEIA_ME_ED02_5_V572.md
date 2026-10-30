# AcheTece — ED02.5 V5.7.2 — Homologação HTTP isolada da visibilidade ED01

## Objetivo e escopo

Executar as funções **reais** `index`, `exportar`, `utils_empresas_json` e `empresa_perfil`, extraídas por AST do `main.py` da própria branch, através do `Flask.test_client`. Todas as consultas são substituídas por um fixture em memória com interface de consulta simulada. Os testes NÃO acessam PostgreSQL, Render, Mercado Pago ou serviços externos e NÃO alteram o `main.py`.

O fixture usa explicitamente a regra ED01: somente status exatamente `ativo`, data preenchida e prazo de 35 dias (mensal) ou 370 dias (anual). Demonstra visibilidade em requisições HTTP, não a avaliação do SQL real do banco. Os templates também são substituídos por respostas JSON de teste; não prova renderização de HTML nem todas as demais rotas da aplicação. Não confundir com homologação HTTP + PostgreSQL ponta a ponta.

## Conteúdo

- `test_ed02_5_v572_http_visibilidade.py`: oito testes HTTP em memória sobre rotas extraídas do `main.py`, sem imports do módulo aplicativo.
- `.github/workflows/ed02-shadow-tests.yml`: workflow **completo**, preserva trabalhos protegidos anteriores e acrescenta execução do novo grupo de testes ao trabalho comum.
- Este arquivo de documentação.

## Publicação

Na branch `ed02-assinaturas-vigencia-ledger`, colocar o Python e o README na raiz e o YAML completo em `.github/workflows/ed02-shadow-tests.yml`. Comparar previamente o YAML atual com o fornecido para preservar mudanças posteriores; não utilizar o arquivo em `main` ou produção. Commit: `ED02.5 V5.7.2 - Testa rotas HTTP de visibilidade ED01`.

No GitHub Actions, confirmar 8 testes do grupo `test_ed02_5_v572_http_visibilidade` como `ok`, além dos 184 anteriores. **Meta esperada: 192 testes**. Não existe job protegido de banco V5.7.2 nem gatilho a executar. Não habilitar flags financeiras, não migrar dados ou mudar critérios de publicação.

## Cenários e limites

1. Busca de 2 vigentes com pendente, vencida, sem data e status equivalente não autorizado bloqueados.
2. Filtros de tipo, diâmetro, galga, UF e cidade.
3. Paginação sem resgatar bloqueadas.
4. CSV sem empresas bloqueadas com e sem filtros.
5. JSON `pagantes`, `demo`, filtro UF.
6. Perfil direto 404 para bloqueadas e ID inexistente.
7. Perfil ativo testado com template simulado.
8. Combinações de filtro sem incluir pendente.

Próximo marco, após a aprovação: criar testes HTTP+SQL reais **somente** em banco de homologação isolado, com registros de teste exclusivos e rollback, para verificar a tradução SQL do `Empresa.assinatura_ativa` e os filtros, inclusive canais alternativos e APIs. Revisar também os prazos históricos (35/370 dias), migração e direito contratual antes de alteração de produção.
