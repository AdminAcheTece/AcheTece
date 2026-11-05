# AcheTece — ED02.5 V5.10 | Correção financeira e Shadow HTTP protegidos

**Estado: pacote de implementação preparado; NÃO autorizado para produção.**

## Origem / limite de versão

O `main.py` usado no ensaio local veio do ZIP da branch disponível em 30/10/2026,
SHA256 `46cac8414f8b5f930a75b95d35383b9d9c60a2cd651460fce64015e90c28163d`.
**Não foi possível consultar o HEAD remoto privado em 04/11/2026.**
A integração final precisa ser verificada contra o `main.py` real do GitHub antes de
substituí-lo. O ZIP NÃO contém um `main.py` que possa ser publicado cegamente.

## O que está implementado

- `ed02_5_v510_legacy_once.py`: uma identidade imutável `(provedor, payment_id)` e
  contrato financeiro registrado, bloqueio `FOR UPDATE` por empresa, replay sem
  prorrogação, renovação por checkout novo e pagamento distinto, análise de
  histórico não conciliado em vez de renovar indiscriminadamente.
- `ed02_5_v510_http_shadow.py`: gancho Flask **depois da resposta**, apenas para
  as quatro rotas, com SELECTs independentes/READ ONLY e log agregado sem IDs,
  nomes, emails ou payloads. **Não altera o resultado HTTP nem publica empresas.**
  A coorte de observação tem os filtros de tear/UF mais relevantes; não deve ser
  confundida com a paginação ou com os resultados individuais retornados.
- `ed02_5_v510_historico.py`: parecer DOCUMENTAL de compatibilidade. Mesmo
  `CANDIDATA_A_CONCILIACAO` não migra nem concede visibilidade.
- `aplicar_ed02_v510_main.py`: gerador protegido do `main.py` completo; por
  padrão **não altera** o arquivo original. Não publique um candidato gerado
  de uma versão antiga. Confronte o código da branch e o `git diff` antes.
- `test_ed02_5_v510_pg_rollback.py`: prova PostgreSQL real contra homologação,
  com dados fictícios e `Session(join_transaction_mode="create_savepoint")`,
  rollback externo, segunda conexão e verificação da empresa original.
- Workflow V5.10 isolado: segurança em cada commit, PostgreSQL real apenas com
  gatilho **exato**, actor e branch autorizados.

## 1. Primeira publicação (não ativa nada)

Na branch `ed02-assinaturas-vigencia-ledger`, adicione os arquivos da raiz e
`.github/workflows/ed02-v510-financeiro-shadow.yml`. Não altere `main.py`,
nem `.github/workflows/ed02-shadow-tests.yml`, nem arquivos já homologados.

Mensagem de commit sugerida:

`ED02.5 V5.10 - Prepara replay atomico e shadow HTTP bloqueado`

Resultado esperado: 34 testes novos OK, job PostgreSQL `Skipped`. A suíte
histórica deve continuar verde. Nenhuma variável de ambiente do Render muda.

## 2. Validação do processador no PostgreSQL (segundo gatilho)

Somente depois de confirmar a preparação:

- edite `ED02_HOMOLOG_EXECUTION.txt` em branch ED02;
- use a mensagem exata `ED02_RUN_REPLAY_ED025_V510_ROLLBACK`;
- veja o trabalho **Replay PostgreSQL real COM rollback**.

Requisitos: `ED02_TEST_DATABASE_URL` já existente em Secrets, banco
`achetece_ed01_test`, host externo exato e empresa original 1 pendente.
**Sem chamadas reais de pagamento**. Se falhar, não reexecute sem investigar.

## 3. Integração futura ao main.py completo (somente após evidência da etapa 2)

O gerador só produz candidato (sem sobrescrever):

```sh
python aplicar_ed02_v510_main.py --source main.py --output main_V510_CANDIDATO.py
```

Compare com o código HEAD da branch. Não use um `main.py` de ZIP antigo sem
confirmar SHA e diferença. Revisar o arquivo gerado e os testes antes de
publicar. O patch instala duas flags **desligadas por padrão**:

- `ED02_V510_REPLAY_HOMOLOG` = false: processador legado ED01 preservado.
- `ED02_V510_HTTP_SHADOW_HOMOLOG` = false: nenhuma observação HTTP adicional.

Quando autorizadas futuramente somente em staging, exigem também
`ED01_HOMOLOGACAO=true`. Não ligue as flags no Render sem auditoria adicional.

## 4. Política de segurança e pendências de produção

1. Reprocessamento legado em produção **continua como risco** até implantação
   de deduplicação com schema, contrato comprovado, verificações e rollback.
2. Empresa histórica ativa sem pagamento aplicado no ledger exige **revisão**:
   pode representar pagamento anterior à migração e jamais será prorrogada
   automaticamente por um evento tardio.
3. Estorno e contestação exigem política de revogação/revisão; a V5.10 NÃO
   altera status de empresas automaticamente por estorno.
4. O comparador shadow é **observacional** e só demonstra divergências; não
   modifica as consultas públicas ED01.
5. Testes unitários locais e testes PostgreSQL com dados sintéticos NÃO
   substituem homologação do Mercado Pago, mensal recorrente, anual parcelável,
   cobrança, cancelamento, conciliação histórica real ou monitoramento.
6. Nenhuma migração DDL é incluída: depende do esquema de homologação ED02.2,
   checkout ED02.3G.7B e períodos ED02.5 já auditados.
7. O gate de produção continua **BLOQUEADO**. Não fazer merge para `main`.
