# AcheTece — ED02.5 V5.7.6 R2 · Correção do ensaio de elegibilidade

## Origem da correção
Na execução V5.7.6 #2, os 5 testes estáticos foram aprovados, mas a home retornou `total_encontrado=0` e a asserção de visibilidade da fixture ativa falhou.

Foram identificados **dois defeitos de desenho do próprio ensaio**, sem comprovação de erro na política de elegibilidade de produção:

1. O `scoped_session.configure(bind=conn)` não garante, por si só, que `Flask-SQLAlchemy.Session.get_bind` escolha a transação externa. A R2 substitui temporariamente `get_bind` no processo de teste, prova a consulta ORM à fixture antes do HTTP e restaura o método após o teste.
2. A V5.7.6 usava IDs negativos, que não passam pelo conversor Flask `<int:empresa_id>`. A R2 usa identificadores positivos de teste e explícitos tanto em `empresa` quanto em `tear`.

## Proteções adicionais
- O alvo continua exclusivamente `achetece_ed01_test` no host PostgreSQL autorizado.
- Os 4 registros de empresa e os 4 de tear são inseridos **sem commit**, dentro de `engine.connect()` + `conn.begin()`.
- Uma segunda conexão prova que as fixtures ainda não são visíveis.
- O teste verifica que a propriedade SQL `Empresa.assinatura_ativa` e o join `Tear`/`Empresa` retornam a fixture ativa **antes** de qualquer HTTP.
- O teste faz requisições às **quatro rotas originais**: `/`, `/exportar`, `/utils/empresas_json` e `/empresa/<id>`.
- As consultas ORM a `empresa`/`tear` fora da conexão do ensaio e DML/DDL durante as requisições são bloqueados.
- Rollback externo sempre executado. A auditoria independente por segunda conexão está em `finally`, incluindo em caso de falha de asserção.
- IDs de `tear` explícitos não avançam sequences do PostgreSQL.
- Fluxos de pagamento, emails, política ED02 e produção permanecem desligados/inalterados.

**Atenção:** sem acesso ao segredo de homologação, o ensaio funcional PostgreSQL não foi executado nesta preparação. Não repetir o gatilho anterior; a R2 possui autorização distinta.

## Arquivos completos
- `test_ed02_5_v576_elegibilidade_rollback.py` → raiz do repositório (substituir arquivo V576 anterior).
- `test_ed02_5_v576_safety.py` → raiz do repositório (substituir arquivo anterior).
- `ed02-v576-elegibilidade-rollback.yml` → `.github/workflows/` (substituir workflow V576 anterior).
- `LEIA_ME_ED02_5_V576_R2.md` → raiz do repositório.

## Etapa A · Preparação
Commit apenas na branch `ed02-assinaturas-vigencia-ledger` com mensagem:

`ED02.5 V5.7.6 R2 - Corrige transacao ORM e IDs das fixtures`

Esperado: os 10 testes de segurança passam, e o trabalho PostgreSQL fica **Skipped**.

## Etapa B · Homologação protegida (SOMENTE após aprovação A)
Editar apenas `ED02_HOMOLOG_EXECUTION.txt` e adicionar linha descritiva, sem tokens nem credenciais.
Fazer commit exclusivo na branch ED02 com mensagem exata:

`ED02_RUN_ELIGIBILITY_ED025_V576_R2`

Analisar os logs da execução protegida. Uma falha não autoriza alterar regras de visibilidade nem repetir o gatilho sem diagnóstico.

## Critério de conclusão
1. ORM encontra uma empresa ativa e um tear, dentro da transação.
2. Home/filtros/CSV/JSON/perfil mostram apenas a empresa ativa nos cenários inseridos.
3. Empresas pendente, vencida e sem data permanecem ocultas, inclusive com HTTP 404 em perfil direto.
4. A segunda conexão confirma nenhuma fixture persistida e baseline original intacto, mesmo se algum teste reprovar.

A V5.7.6 continua em homologação até esse resultado completo, sem merge em `main`.
