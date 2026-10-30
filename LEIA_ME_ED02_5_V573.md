# ED02.5 V5.7.3 — GATE de segurança de visibilidade

## Constatação
A versão atualizada da branch contida no ZIP `AcheTece-ed02-assinaturas-vigencia-ledger (1).zip` possui o filtro `Empresa.assinatura_ativa` na home, exportação CSV, JSON e perfil; a versão solta anterior de `main.py` disponível no espaço de trabalho NÃO possui esses filtros em todas as rotas. Esta divergência demonstra a necessidade de checar o HEAD da branch antes de aplicar qualquer patch. O pacote inclui testes unitários do detector e inventário PostgreSQL somente leitura; não é prova de HTTP + PostgreSQL real.

## Arquivos completos
- `ed02_5_v573_auditoria.py`: análise AST e relatório de riscos; somente leitura. **Não será colocado como gate que quebra a suíte regressiva sem aviso.**
- `test_ed02_5_v573_auditoria.py`: seis testes do detector.
- `test_ed02_5_v573_pg_readonly.py`: inspeção segura de identidade e contagem de empresas em transação READ ONLY, sem HTTP real.
- `.github/workflows/ed02-shadow-tests.yml`: workflow completo com os seis testes e job protegido somente leitura.

## Publicação
Copiar arquivos Python e este README para a raiz, workflow para `.github/workflows/`; nunca alterar `main.py`, Render ou branch `main`. Commit normal sugerido: `ED02.5 V5.7.3 - Audita divergencias de visibilidade ED01`.

## Diagnóstico estático local
`python ed02_5_v573_auditoria.py` retorna saída não-zero se houver riscos. É uma falha esperada até a correção. Não trate os testes unitários verdes como aprovação da visibilidade.

## Consulta protegida
Gatilho exato `ED02_RUN_AUDIT_ED025_V573` somente após revisão dos resultados normais. Verifica conexão real PostgreSQL e conta empresas, mas não faz renderização Flask nem altera dados.

## Próximo passo técnico obrigatório
Usar o HEAD atual da branch, auditar os quatro filtros e executar testes das rotas reais com SQLAlchemy vinculado a uma transação PostgreSQL reversível antes de homologar V5.7.3. Este pacote é uma pré-validação e não declara a V5.7.3 concluída.
