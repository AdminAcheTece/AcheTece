# ED02.5 V5.7.1 — Auditoria estática de visibilidade e compatibilidade ED01

## Escopo

Audita, por AST Python e sem executar `main.py`, a existência de filtros obrigatórios `Empresa.assinatura_ativa` dentro de chamadas SQLAlchemy `.filter()` nas rotas `/`, `/exportar`, `/utils/empresas_json`, `/empresa/<int:empresa_id>`. Confere referências no contrato histórico a `status_pagamento`, `data_pagamento` e duração por plano. Sinaliza separadamente o endpoint administrativo `/admin/seed_teares_all`, que usa um filtro de status simples.

**Não é um teste HTTP/SQL integrado**. Não audita templates, rotas adicionais, comportamento em runtime, permissões de admin, semântica efetiva da vigência ED02, renderização ou dados reais. O código foi analisado contra uma cópia conhecida do `main.py`, que pode diferir da branch mais recente; a validação final será feita no GitHub sobre o arquivo versionado atual.

## Arquivos e destinos

- `ed02_5_v571_auditoria.py`: raiz do repositório
- `test_ed02_5_v571_auditoria.py`: raiz do repositório
- `.github/workflows/ed02-shadow-tests.yml`: substituir **somente depois de comparar com a versão atual**; a cópia foi derivada do workflow V5.7 e inclui os seis novos testes na suíte comum.

Não atualizar `main.py`, Render, checkout, flags, banco ou produção.

## Execução

Commit normal na branch `ed02-assinaturas-vigencia-ledger` com a mensagem `ED02.5 V5.7.1 - Audita visibilidade ED01 sem alterar rotas`. A suíte esperada é 178 testes anteriores + 6 novos = 184. O teste estático pontual roda com `python ed02_5_v571_auditoria.py` na raiz, usando o `main.py` dessa mesma branch.

## Critérios e próximos gates

Aprovação do workflow não equivale a comprovar acesso negado via endpoints HTTP. Em próxima fase, executar testes HTTP e banco de homologação para status pendente, ativo não vencido, vencido, sem data, perfil direto, CSV e JSON; verificar filtros técnicos, paginação e consultas acessórias; revisar rotas administrativas; apenas depois considerar migração de visibilidade ED02. Nenhuma empresa ganha ou perde visibilidade nesta etapa.
