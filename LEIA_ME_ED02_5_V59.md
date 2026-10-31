# AcheTece ED02.5 V5.9 — Instalação protegida

1. Escolha somente a branch `ed02-assinaturas-vigencia-ledger`.
2. Envie os sete arquivos Python (`ed02_5_v59_*` e `test_ed02_5_v59_*`)
   **completos**, sem sobrescrever `main.py` nem arquivos da V5.8.
3. Adicione `ED02_5_V59_PLANO_DE_FECHAMENTO.md` e este documento à raiz.
4. Adicione `.github/workflows/ed02-v59-consolidacao-financeira.yml` no caminho exato.
5. Confirme que os arquivos V5.8 (`ed02_5_v58_consolidacao.py` e
   `ed02_5_v58_gate_ativacao.py`) e a função transacional
   `ed02_payment_idempotency.py` já existem na mesma branch.
6. Commit de preparação: `ED02.5 V5.9 - Audita replay e comparacao shadow com gate bloqueado`.
7. O trabalho de testes deve passar; o PostgreSQL V5.9 deve constar como
   **Skipped**. Não acione a auditoria antes de revisar os resultados.

## Avisos de segurança

- Não habilitar `ED02_EXACT_ONCE_HOMOLOG`, `ED02_FINANCIAL_GUARD_HOMOLOG`
  nem flags de visibilidade na produção.
- O teste de regressão da V5.9 **não comprova** que o replay do caminho legado
  esteja corrigido; isso é uma pendência registrada no gate.
- Não criar conta, empresa, pagamento ou assinatura real durante esta fase.
- Nenhum merge para `main` sem autorização específica e release gate final.
