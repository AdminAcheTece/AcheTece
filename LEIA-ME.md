# AcheTece ED02.3B V1 — integração observacional, desligada

## Contrato de segurança
- Usar apenas na branch `ed02-assinaturas-vigencia-ledger`.
- Não atualizar branch `main`; não executar merge nem pagamentos reais.
- O registro observacional exige `ED02_SHADOW_LEDGER=true`, MAS mesmo assim só escreve quando `current_database() = 'achetece_ed01_test'`.
- Nenhuma tabela `empresa` ou `tear` é modificada pelo módulo novo; nenhum novo ciclo de assinatura é criado neste passo.
- A lógica já existente `_processar_pagamento()` permanece como estava. A ED02 ainda não substitui as regras da ED01.
- O script usa a resposta autenticada obtida pela função `_mp_get_payment()` já presente no aplicativo; não confia no status fornecido diretamente pelo webhook.
- Não armazena e-mail, nome ou payload integral na nova tabela.

## Arquivos
- `main.py`: código principal com chamada observacional protegida por flag. Inclui a correção ED01 de diâmetro já publicada na main.
- `ed02_payment_shadow.py`: valida associação da empresa pelo `external_reference`, moeda, valor, ID, e salva via UPSERT idempotente no ledger.
- `test_ed02_payment_shadow.py`: 6 testes locais de validação de dados (não testa o PostgreSQL ou a API real).

## Implantação controlada
1. Na branch `ed02-assinaturas-vigencia-ledger` fazer upload dos 3 arquivos. Não apagar outros arquivos.
2. Aguardar confirmação do commit. Não implantar no serviço de produção.
3. Para o serviço `achetece-ed01-teste`, mudar branch de deploy para ED02 apenas depois de verificar que o banco ainda é o isolado `achetece_ed01_test` e que o serviço está sem credenciais reais do Mercado Pago.
4. Deixar `ED02_SHADOW_LEDGER=false` nesta etapa inicial; testes de eventos ocorrerão depois com serviço fake de provedor ou mocks de HTTP, nunca com pagamento real.

## Limitações e gates
- Estes seis testes locais não demonstram idempotência transacional sob concorrência real, nem validação da assinatura do webhook.
- A identidade do Mercado Pago é verificada via consulta à API existente; para ativar o registro em modo controlado, precisamos primeiro do teste mock e da revisão dos detalhes de autenticação.
- Para produção, implementar confirmação de valor/plano com uma referência de checkout imutável, relógio de vigência e medidas de resiliência. Nenhum desses recursos fica habilitado aqui.
