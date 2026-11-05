# ED02.5 V5.10.1 — Critérios de aceite e riscos remanescentes

## Aprovável na primeira publicação
- Testes de integração da função de pagamento via AST/mocks: aprovar.
- Testes de não intervenção nas quatro rotas ED01: aprovar.
- Confirmação do patch estrito, somente duas inserções no `main.py`: aprovar.
- Artefato `main_V5101_CANDIDATO.py` completo e `ED02_V5101_MAIN.diff`: gerar a partir do HEAD.
- Nenhuma alteração do `main.py` operacional: obrigatório.
- Job PostgreSQL marcado como Skipped: obrigatório.

## Aprovável no ensaio protegido posterior
- Acesso exclusivamente ao PostgreSQL `achetece_ed01_test`.
- Todas as conexões com `default_transaction_read_only=on`.
- Home, filtros, CSV, JSON e perfis originais preservados.
- Shadow OFF: nenhuma observação executada.
- Shadow ON (somente processo de teste): quatro superfícies observadas e respostas equivalentes às do modo OFF.
- Uma empresa pendente permanece oculta e seus dados intactos.
- Zero escrita, zero pagamento, zero envio de e-mails.

## Restrições expressas
1. Esta fase **não implanta** o `main.py` candidato no Render.
2. A confirmação do processador financeiro com SQL real foi feita na V5.10 isolada; esta fase só comprova integração Flask sem pagamentos reais.
3. O fluxo legado com flags desligadas conserva o comportamento atual e seu risco de replay. Não informar que foi corrigido em produção.
4. Estornos, chargebacks, conciliação histórica e renovações do ciclo de assinatura completo são gates pendentes.
5. Não habilitar flags no Render, não modificar credenciais, não promover branch para `main` sem autorização separada.
6. A revisão do `main.py` completo gerado pelo HEAD é obrigatória antes de qualquer implantação controlada em homologação.

## Gate
**ED02 ATIVAÇÃO: BLOQUEADA.**
