# ED02.5 V5 — Integração Flask protegida (SEM ativação)

Base: ZIP da branch enviado pelo usuário após a execução #72.

## Arquivos completos
- `main.py` — substitui o atual, com branch condicional de homologação.
- `ed02_5_visibility_gate.py` — consulta read-only de período vigente, ainda não conecta a buscas públicas.
- `test_ed02_5_v5_gate.py` — testes da guarda e de regressão estrutural.

## Garantias e limites
- Nova flag `ED02_VIGENCIA_WRITE_HOMOLOG`, default FALSE. NÃO configurar no Render por enquanto.
- Só entra no caminho V5 quando as quatro flags prévias são verdadeiras: `ED01_HOMOLOGACAO`, `ED02_FINANCIAL_GUARD_HOMOLOG`, `ED02_CHECKOUT_LEDGER_HOMOLOG`, `ED02_EXACT_ONCE_HOMOLOG`.
- A guarda financeira anterior verifica o contrato antes de chegar ao novo bloco; o adaptador verifica novamente e bloqueia a empresa.
- Sem tabela persistente, a tentativa V5 bloqueia e não ativa a empresa. O ensaio V4 fez rollback do DDL.
- O retorno V5 não ativa a empresa e não envia e-mail. O status de assinatura e as buscas continuam com a ED01; não ativar o caminho V5 para clientes.
- Não foi implementada conciliação histórica, coexistência em produção, migração definitiva, processamento de webhooks HTTP paralelos ou visibilidade contratual.
- O teste V5 deve ser acrescentado à suíte Github Actions em atualização posterior, não substitui os 135 testes existentes.

## Ordem segura
1. Substituir os três arquivos completos na branch de homologação (nenhuma migração ou flag).
2. Verificar testes da branch (135 anteriores). Validar os novos testes separadamente até entrarem no workflow.
3. Preparar e homologar uma migração controlada persistente exclusivamente em staging, com backup e rollback lógico, antes de habilitar quaisquer flags.
4. Testar webhook duplo, renovação, contratos antigos, mudança de visibilidade e reversão antes da produção.

**Não aplicar este código à branch main. Não ativar flags.**
