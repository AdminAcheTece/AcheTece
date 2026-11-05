# V5.10 — Contrato de convivência ED01 × ED02

- **A ED01 é a única fonte de visibilidade pública durante a homologação.** A
  V5.10 não modifica `Empresa.assinatura_ativa`, SQLAlchemy nem os resultados
  originais de home, filtros, CSV, JSON e perfil.
- **ED02 é diagnóstico e período proposto.** Não converter pagamentos antigos
  em períodos vigentes automaticamente e não tratar um evento recebido como
  comprovante de pagamento.
- **Replay:** o mesmo `mercadopago/payment_id` não altera `data_pagamento` nem
  cria período ou envia nova notificação; conflitos de empresa/ref bloqueiam.
- **Renovação:** payment_id distinto + checkout distinto e válido podem renovar
  na homologação se o histórico anterior estiver comprovado no ledger.
- **Empresa ativa sem vínculo histórico:** revisão manual obrigatória antes de
  atualizar datas por evento antigo. Não apagar direito adquirido; manter ED01.
- **Estorno/contestação:** não renovar, não revogar automaticamente nesta etapa;
  encaminhar para política específica e revisão com evidências do provedor.
- **Shadow HTTP:** logs apenas agregados por endpoint, sem IDs, emails ou
  identificadores de pagamento; erros não mudam resposta pública.
- **Gate:** todos os requisitos comerciais e financeiros precisam ser
  homologados antes de qualquer substituição de regra, inclusive em staging.
