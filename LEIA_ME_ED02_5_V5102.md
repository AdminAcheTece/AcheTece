# AcheTece — ED02.5 V5.10.2 — Aplicação Controlada ao Flask (gate 1)

**Situação: CANDIDATO PARA REVISÃO. NÃO APLICADO. NÃO AUTORIZADO PARA PRODUÇÃO.**

Esta entrega foi montada a partir do artefato `ED02_V5101_MAIN_COMPLETO_E_DIFF.zip` auditado anteriormente. A versão original de `main.py` no GitHub deve continuar íntegra na branch `ed02-assinaturas-vigencia-ledger`. O script de pré-voo compara seus hashes antes de autorizar a revisão. Nenhuma credencial é necessária ou incluída.

## Mudanças propostas

1. Modo de replay V510: falha fechada antes de consultar Mercado Pago quando `ED01_HOMOLOGACAO` não está ligado ou quando há flags financeiras conflitantes. A política antiga `ED02_VIGENCIA_WRITE_HOMOLOG` depende de `ED02_EXACT_ONCE_HOMOLOG`; a execução concorrente com V510 é bloqueada.
2. Corrige somente o e-mail do **ramo V510**, passando os quatro argumentos que `_send_email` exige (`destinatario`, `assunto`, `texto`, `html`). Não envia nenhum e-mail durante a revisão.
3. O comparador shadow deixa de contar `CONVERGENTE_OCULTO_ED01` e `CONVERGENTE_VISIVEL` como divergências. Novas categorias desconhecidas contam como alertas. Nenhuma decisão de visibilidade foi alterada.

### Limitações e riscos ainda abertos

- Chamadas de e-mail do fluxo legado e do ramo EXACT_ONCE precisam de auditoria específica; **não foram modificadas neste pacote**.
- Testes históricos criados antes da nova validação de flags podem exigir atualização do ambiente simulado **após** a aplicação do candidato ao `main.py`. Não pular regressões; qualquer falha impede a aplicação.
- A integração ainda depende de validar fluxos financeiros reais em sandbox, conciliação histórica, estornos e regras de renovação; o gate de produção permanece bloqueado.
- O script não executa pagamentos, webhooks, conexão com PostgreSQL ou envios de e-mail.

## Arquivos do pacote

| Arquivo | Finalidade |
|---|---|
| `main_V5101_BASE.py` | Referência integral anterior, nunca deve ser enviada como `main.py` |
| `main_V5102_CANDIDATO.py` | **Arquivo completo** candidato a substituir `main.py` no futuro |
| `ed02_5_v510_http_shadow_V5102_CANDIDATO.py` | **Módulo completo** candidato ao shadow |
| `ED02_V5102_DIFF_MAIN.patch` | Mudanças propostas no `main.py` V5101 |
| `ED02_V5102_DIFF_SHADOW.patch` | Mudanças propostas no módulo shadow |
| `ED02_V5102_RELATORIO.json` | Hashes de integridade e pendências |
| `test_ed02_5_v5102_integracao.py` | Testes isolados de segurança e compatibilidade |
| `verificar_ed02_v5102_head.py` | Auditoria de hash somente leitura |
| `.github/workflows/ed02-v5102-candidato-protegido.yml` | Workflow independente, **sem gatilho de pagamentos ou PostgreSQL** |

## Gate 1 — Publicar os candidatos, SEM substituir código operacional

1. Na branch `ed02-assinaturas-vigencia-ledger`, envie os arquivos acima para a raiz, preservando `.github/workflows/` para o workflow.
2. **Não renomeie** `main_V5102_CANDIDATO.py` para `main.py` e **não substitua** `ed02_5_v510_http_shadow.py` nesta publicação.
3. Faça commit: `ED02.5 V5.10.2 - Prepara aplicacao controlada do candidato Flask`.
4. Abra GitHub Actions → `ED02 V5.10.2 - Revisao de candidato Flask protegido`.
5. Confira os testes e o resultado de `Verificar HEAD e hashes`. Se houver divergência de hash, PARE: a branch precisa ser reavaliada; não sobrescreva arquivos mais recentes.

## Gate 2 — Futuro, somente após revisão e regressoes

A aplicação no `main.py` real **não faz parte do gate 1**. Antes de aplicá-la, deve-se garantir que o Render de homologação não fará deploy automático inesperado. Em execução posterior, comparar o HEAD novamente e substituir o código exclusivamente na branch de homologação, com novas regressões de rotas ED01, financial webhook, vigência e PostgreSQL. Qualquer teste regressivo reprovado mantém o gate bloqueado.

**Nunca habilitar flags financeiras, testar com pagamentos reais, alterar a branch `main` ou fazer merge para produção por meio deste pacote.**
