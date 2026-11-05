# AcheTece — ED02.5 V5.10.3 — Integração operacional controlada (pré-merge)

**ESTADO:** pacote de integração CANDIDATA. Não executa deploy, não liga pagamentos, não aplica alterações ao Render e não autoriza merge. Não edite a branch `main`.

## Escopo

Os dois arquivos completos `main.py` e `ed02_5_v510_http_shadow.py` vêm dos **candidatos V5.10.2 aprovados no Gate 2** (11 testes do preflight e 160 testes de regressão em cópia isolada). São fornecidos com nomes operacionais **somente para instalar em uma nova branch de integração candidata**; não substituir na ED02 diretamente.

Antes de qualquer integração, o GitHub verificará os hashes contra o Gate 2, se o `main.py` da branch ED02 ainda tem um dos dois hashes auditados, se o módulo shadow ainda tem o hash anterior, se nenhuma rota ED01 foi modificada e se o diff contém somente arquivos da integração.

## Etapa A — Branch candidata, sem deploy

1. No GitHub, em **Branches / Ramos**, crie a branch `ed02-v5103-integracao-candidata`, **a partir de `ed02-assinaturas-vigencia-ledger`** (não a partir de `main` e não a partir da branch do Gate 2).
2. Confira que nenhum serviço Render usa a nova branch. O serviço `achetece-ed01-teste` deve permanecer associado a `ed02-assinaturas-vigencia-ledger`.
3. Abra a nova branch e faça upload **apenas** do conteúdo de `PUBLICAR_NA_BRANCH_CANDIDATA/`, respeitando os caminhos:
   - `main.py` → raiz (substitui apenas o da **nova branch**)
   - `ed02_5_v510_http_shadow.py` → raiz (substitui apenas o da **nova branch**)
   - `ed02_v5103_gate.py`, `test_ed02_v5103_gate.py`, `ED02_V5103_MANIFESTO.json`, `LEIA_ME_ED02_V5103.md` → raiz
   - `.github/workflows/ed02-v5103-revisao-pr.yml` → pasta `.github/workflows/`
4. Commit: `ED02.5 V5.10.3 - Prepara PR de integracao operacional sem deploy`.
5. Abra **Actions → ED02 V5.10.3 - Integracao candidata PR SEM deploy**. Confirme verde nos dois trabalhos: *Gate 3 - Preflight SHA e ED01* e *Gate 3 - Regressao dos arquivos operacionais em copia*.
6. Baixe os artefatos do workflow. Contêm auditoria de hashes, log de regressão e **backup dos dois arquivos pré-alteração** retirados da branch ED02. Guarde esses arquivos fora da branch.

**Se qualquer teste falhar: NÃO reaplique, NÃO faça merge e NÃO altere Render.** Leia o log, resolva a incompatibilidade na candidata e repita a verificação só após uma revisão técnica.

## Etapa B — Pull request, sem merge automático

Depois de duas validações verdes, crie um pull request **da branch candidata para a branch `ed02-assinaturas-vigencia-ledger`**. NÃO selecione `main` como base. Revise `Files changed` e confirme que apenas os dois módulos operacionais e os arquivos auxiliares de auditoria constam no PR. **Deixe o PR aberto, sem merge**, enquanto não for confirmada a segurança do Render.

## Etapa C — Proteção Render obrigatória antes do merge

No serviço de **homologação** Render `achetece-ed01-teste`, em Settings / Build & Deploy, registre em captura o nome da branch e o estado de Auto-Deploy. A branch deve ser `ed02-assinaturas-vigencia-ledger` e **Auto-Deploy precisa estar OFF** antes de aprovar o merge para ED02. Confirme que as flags ED02 financeiras e de shadow não estão ativadas **sem expor valores sensíveis nem senhas**. Não mexa no serviço de produção.

Crie ponto de reversão do SHA da ED02 imediatamente anterior ao merge. O backup do Actions não substitui esse SHA. Nenhum botão *Deploy*, *Redeploy*, *Merge* ou *Enable Auto Deploy* deve ser pressionado até revisão dos logs e condições de regressão.

## Etapa D — Integração e homologação subsequentes (não executadas por este pacote)

Depois de verificada a configuração do Render, o merge poderá ser autorizado **separadamente**, exclusivamente na ED02. Com Auto-Deploy desligado, confirme que o commit apareceu no GitHub mas o Render ainda permanece no SHA anterior. Execute regressões na branch ED02, homologação HTTP/PostgreSQL com o código integrado e somente depois considere um deploy manual de homologação. Registrar sinais de saúde, rotas, webhooks simulados, logs de inicialização e plano de rollback.

**Gate financeiro:** bloqueado. Sem Mercado Pago real, sem e-mails, sem mudança de planos (R$ 2,00 de teste mensal/anual), sem ativação comercial, sem merge para `main`.

## Limites da prova

A regressão GitHub é offline e usa um clone descartável. NÃO valida integração com Mercado Pago em produção, nem PostgreSQL real pós-merge, nem o deploy do Render. O workflow possui `contents:read`, não tem credenciais de produção e não faz push nem merge. A instalação da nova branch é feita pelo usuário no GitHub e pode desencadear workflows de push gerais; por isso é importante confirmar o branch targeting de qualquer serviço antes de publicar.

## Reversão posterior

Em caso de regressão após merge: manter Auto-Deploy desligado, **reverter o commit de merge de homologação** ou restaurar os dois arquivos conforme SHA original, em novo commit da ED02; nunca fazer force-push nem modificar `main`. Se houver deploy manual de homologação, usar o ponto de reversão anterior somente após verificar estado do serviço e dependências. Nenhuma migração de dados está incluída neste pacote.
