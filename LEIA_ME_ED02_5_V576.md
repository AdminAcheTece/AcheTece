# AcheTece — ED02.5 V5.7.6

## Escopo
Ensaio **ED01** de elegibilidade positiva e negativa nas quatro rotas HTTP originais, usando SQLAlchemy e PostgreSQL de homologação. A política ED02 de vigência NÃO será ativada.

## Arquivos
- `test_ed02_5_v576_elegibilidade_rollback.py` → raiz do repositório.
- `test_ed02_5_v576_safety.py` → raiz do repositório.
- `ed02-v576-elegibilidade-rollback.yml` → `.github/workflows/`.

Não substitua o `main.py`, o workflow histórico ou os templates.

## Etapa A — Preparação (sem acesso ao banco)
Commit na branch `ed02-assinaturas-vigencia-ledger`:

`ED02.5 V5.7.6 - Prepara elegibilidade com rollback`

Esperado: job `ED02.5 V5.7.6 - Testes de seguranca` passa **5/5**. Job protegido `HTTP original PostgreSQL rollback` fica **Skipped**.

## Etapa B — Execução protegida (apenas após confirmar Etapa A)
Editar `ED02_HOMOLOG_EXECUTION.txt` para registrar novo ensaio. Commit exclusivo na mesma branch com mensagem exata:

`ED02_RUN_ELIGIBILITY_ED025_V576`

O teste requer segredo `ED02_TEST_DATABASE_URL`, branch e ator autorizados, conexão PostgreSQL de homologação e baseline com uma empresa pendente (`id=1`). Nunca compartilhar a URL com credenciais.

## Como o teste funciona
1. Verifica inventário original e banco autorizado.
2. Abre transação externa **sem commit**.
3. Cria quatro empresas e quatro teares de teste em memória transacional, usando identificadores negativos exclusivos.
4. Comprova por segunda conexão que as fixtures não são públicas.
5. Usa a conexão da transação na sessão SQLAlchemy do Flask; executa `/`, `/exportar`, `/utils/empresas_json`, `/empresa/<id>` sem trocar as funções de rota.
6. Bloqueia DML/DDL disparado pelas requisições HTTP.
7. Faz rollback da transação em `finally`, mesmo se o teste falhar.
8. Comprova por segunda conexão: nenhuma fixture remanescente e empresa original inalterada.

**Atenção:** o teste faz `INSERT`s na transação temporária antes dos requests. Não execute em produção, não autorize outro banco, não habilite flags financeiras e não rode outro teste financeiro concorrente usando a malharia `id=1`.

## Expectativas / limitações
- **Esperado:** apenas a empresa ativa vigente aparece em home, CSV e JSON, e seu perfil responde HTTP 200; pendente, vencida e sem data retornam 404 no perfil e não aparecem nas demais superfícies.
- **Atenção:** se o teste falhar na renderização ou nos filtros, preservar falha e analisar log; NÃO afrouxar as afirmações para forçar verde.
- **Não cobre:** aplicação da política ED02 sobre a visibilidade, cobranças reais, autenticação de Mercado Pago ou concorrência HTTP de pagamentos.
- **Risco:** sequências PostgreSQL e outros efeitos externos não transacionais não são cobertos por rollback. O ensaio usa IDs negativos explícitos e bloqueia e-mails/analytics, mas não garante ausência de efeitos de código desconhecido fora do banco. Revisar a versão atual da branch antes de executar.
