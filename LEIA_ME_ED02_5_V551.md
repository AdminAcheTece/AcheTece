# AcheTece — ED02.5 V5.5.1 — Concorrência com commits reais

## Escopo
Teste **estritamente de homologação**. Usa duas conexões independentes, o `_processar_pagamento` já presente no `main.py`, resposta fictícia da API Mercado Pago e um contrato fictício mensal. Exercita commits efetivos, verifica que uma chamada cria a vigência e a outra retorna duplicado, e audita os registros após os commits.

## Proteções
- Destino externo autorizado: `achetece_ed01_test` no host de homologação já conhecido; valida URL e banco efetivo.
- Apenas a empresa fictícia `id=1`, exigindo `status_pagamento='pendente'` e `data_pagamento IS NULL`.
- Valores fictícios de R$ 2,00, IDs únicos e preferência com prefixo `ED025-V551-`.
- Nenhuma API Mercado Pago, e-mail ou ativação empresarial.
- Trabalho autorizado **somente** pelo commit `ED02_RUN_COMMIT_ED025_V551` na branch ED02, usuário autorizado.
- Limpeza ao final limitada a IDs e referência/preferência exclusivos deste ensaio, seguida de auditoria em outra conexão.

## Atenção operacional
Este ensaio **não é puramente transacional com rollback**: confirma dados e depois os elimina de forma controlada. Se o runner for interrompido abruptamente entre um commit e a limpeza, dados fictícios podem permanecer no banco. **Não repita o gatilho** se houver qualquer falha; inspecione os registros e faça limpeza supervisionada, identificando precisamente os IDs fictícios. Não habilite flags no Render nem execute esse teste localmente.

## Publicação
Adicionar `test_ed02_5_v551_commit_concorrente.py`, `test_ed02_5_v551_safety.py` e este README na raiz do repositório. Substituir `.github/workflows/ed02-shadow-tests.yml` pelo arquivo completo do pacote, somente após conferir que a versão da branch não recebeu alterações posteriores.

Commit de preparação: `ED02.5 V5.5.1 - Prepara commits reais e limpeza auditada`.

Os testes habituais devem subir de 154 para 158; o trabalho protegido deverá ficar ignorado.

**Somente depois de verificar a suíte**: editar `ED02_HOMOLOG_EXECUTION.txt`, acrescentar linha de autorização e usar commit exato `ED02_RUN_COMMIT_ED025_V551`.

## Critérios de aprovação
- Duas execuções concorrentes: exatamente um período criado e um replay.
- Uma leitura independente, após ambos os commits, encontra um período e um evento financeiro.
- Empresa continua pendente, sem e-mails.
- Remoção direcionada exclusivamente aos IDs do ensaio.
- Segunda leitura independente confirma zero resíduos em contrato, evento e período.

**Não há autorização para `main`, produção, pagamentos reais ou automações de visibilidade.**
