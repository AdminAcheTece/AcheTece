# training_catalog.py
# Catálogo do Treinamento Operacional (Opção 2)
# Você edita aqui: módulos, aulas, PDFs, quizzes etc.

# -*- coding: utf-8 -*-
from __future__ import annotations

from typing import Any, Dict, List

# Observação:
# - O campo "file" assume o padrão "<lesson_key com . -> _>.pdf" dentro de "training_files/".
#   Ex.: m0.1 -> training_files/m0_1.pdf
# - Ajuste "minutes" e os nomes dos arquivos conforme seus PDFs reais.
#
# Nota rápida:
# - No DOCX existem módulos de 0 a 20 (21 módulos no total). Mantive todos aqui.

TRAINING_CATALOG: List[Dict[str, Any]] = [
    {
        "key": "m0",
        "title": "Módulo 0 — Apresentação do Programa de Treinamento",
        "desc": "Apresentar e aprovar o Programa (escopo, investimento, governança).",
        "lessons": [
            {
                "key": "m0.1",
                "title": "Aula 1 - Contexto e por que agora",
                "minutes": 12,
                "file": "m0_1.pdf",
                "summary": "Explicar o contexto e a urgência do programa.",
            },
            {
                "key": "m0.2",
                "title": "Aula 2 - Objetivos estratégicos do Programa",
                "minutes": 12,
                "file": "m0_2.pdf",
                "summary": "Alinhar objetivos e ganhos esperados do programa.",
            },
            {
                "key": "m0.3",
                "title": "Aula 3 - Escopo (áreas cobertas)",
                "minutes": 12,
                "file": "m0_3.pdf",
                "summary": "Delimitar escopo e áreas cobertas na implantação.",
            },
            {
                "key": "m0.4",
                "title": "Aula 4 - Método (como o aprendizado vira resultado no turno)",
                "minutes": 12,
                "file": "m0_4.pdf",
                "summary": "Mostrar o método de aprendizagem e validação no turno.",
            },
            {
                "key": "m0.5",
                "title": "Aula 5 - Governança e papéis (RACI simplificado)",
                "minutes": 12,
                "file": "m0_5.pdf",
                "summary": "Definir governança, papéis e responsabilidades (RACI).",
            },
            {
                "key": "m0.6",
                "title": "Aula 6 - Cronograma de implantação (macro)",
                "minutes": 12,
                "file": "m0_6.pdf",
                "summary": "Apresentar fases e prazos do cronograma de implantação.",
            },
            {
                "key": "m0.7",
                "title": "Aula 7 - Métricas de sucesso (antes/depois)",
                "minutes": 12,
                "file": "m0_7.pdf",
                "summary": "Definir métricas antes/depois e rotina de acompanhamento.",
            },
        ],
    },
    {
        "key": "m1",
        "title": "Módulo 1 — Integração + Regras do Jogo do Setor (onboarding)",
        "desc": "Integrar novos colaboradores com mapa do setor, regras do jogo e comunicação padrão.",
        "lessons": [
            {
                "key": "m1.1",
                "title": "Aula 1 - Boas-vindas e mapa do setor",
                "minutes": 12,
                "file": "m1_1.pdf",
                "summary": "Dar visão clara do ambiente, fluxo e expectativas.",
            },
            {
                "key": "m1.2",
                "title": "Aula 2 - Regras do jogo (decisão padrão)",
                "minutes": 12,
                "file": "m1_2.pdf",
                "summary": "Padronizar como pensar e agir no turno.",
            },
            {
                "key": "m1.3",
                "title": "Aula 3 - Comunicação curta e escalonamento",
                "minutes": 12,
                "file": "m1_3.pdf",
                "summary": "Ensinar a pedir ajuda sem ruído.",
            },
            {
                "key": "m1.4",
                "title": "Aula 4 - Como será o treinamento (trilha e validação)",
                "minutes": 12,
                "file": "m1_4.pdf",
                "summary": "Criar clareza do método e critérios de liberação.",
            },
        ],
    },
    {
        "key": "m2",
        "title": "Módulo 2 — Segurança + Ergonomia + Riscos Reais (malharia circular)",
        "desc": "Aplicar segurança e ergonomia na malharia: EPIs, zonas de risco e conduta segura.",
        "lessons": [
            {
                "key": "m2.1",
                "title": "Aula 1 - EPIs e regras do setor",
                "minutes": 12,
                "file": "m2_1.pdf",
                "summary": "Garantir proteção mínima e disciplina.",
            },
            {
                "key": "m2.2",
                "title": "Aula 2 - Zonas de risco e ‘quando parar’",
                "minutes": 12,
                "file": "m2_2.pdf",
                "summary": "Evitar contato com partes móveis e acidentes típicos.",
            },
            {
                "key": "m2.3",
                "title": "Aula 3 - Conduta com máquina rodando (3 perguntas antes de agir)",
                "minutes": 12,
                "file": "m2_3.pdf",
                "summary": "Eliminar improviso antes de agir com máquina rodando.",
            },
            {
                "key": "m2.4",
                "title": "Aula 4 - Ar comprimido e ergonomia",
                "minutes": 12,
                "file": "m2_4.pdf",
                "summary": "Evitar riscos no uso de ar comprimido e reduzir esforço/lesões.",
            },
            {
                "key": "m2.5",
                "title": "Aula 5 - Emergências e resposta rápida",
                "minutes": 12,
                "file": "m2_5.pdf",
                "summary": "Reagir rápido e com segurança em emergências.",
            },
        ],
    },
    {
        "key": "m3",
        "title": "Módulo 3 — Cadeia Têxtil e Visão do Processo da Malharia (fio → rolo → expedição)",
        "desc": "Dar visão ponta a ponta do processo e linguagem comum da cadeia têxtil.",
        "lessons": [
            {
                "key": "m3.1",
                "title": "Aula 1 - Visão macro da cadeia têxtil",
                "minutes": 12,
                "file": "m3_1.pdf",
                "summary": "Dar linguagem comum sobre a cadeia têxtil.",
            },
            {
                "key": "m3.2",
                "title": "Aula 2 - Mapa do fluxo interno (recebimento → armazenagem → abastecimento)",
                "minutes": 12,
                "file": "m3_2.pdf",
                "summary": "Entender entradas, controles e pontos de falha do fluxo.",
            },
            {
                "key": "m3.3",
                "title": "Aula 3 - Do tear ao rolo",
                "minutes": 12,
                "file": "m3_3.pdf",
                "summary": "Conectar causa e efeito de defeitos do tear ao rolo.",
            },
            {
                "key": "m3.4",
                "title": "Aula 4 - Revisão e expedição (interface)",
                "minutes": 12,
                "file": "m3_4.pdf",
                "summary": "Alinhar interface com revisão/expedição e critérios de entrega.",
            },
        ],
    },
    {
        "key": "m4",
        "title": "Módulo 4 — Fios e Identificação (tecnologia, composição, lote, elastano, decisão)",
        "desc": "Identificar e controlar fios: lote, elastano, tecnologia e bloqueio de erros caros.",
        "lessons": [
            {
                "key": "m4.1",
                "title": "Aula 1 - Erros caros primeiro (o que custa lote)",
                "minutes": 12,
                "file": "m4_1.pdf",
                "summary": "Criar prioridade mental e disciplina de bloqueio de erros caros.",
            },
            {
                "key": "m4.2",
                "title": "Aula 2 - Etiqueta e conferência (embalagem + cone)",
                "minutes": 12,
                "file": "m4_2.pdf",
                "summary": "Evitar fio errado e lote trocado com conferência padrão.",
            },
            {
                "key": "m4.3",
                "title": "Aula 3 - Tecnologia do fio: o que muda no olho e no uso",
                "minutes": 12,
                "file": "m4_3.pdf",
                "summary": "Entender como a tecnologia do fio muda desempenho e aparência.",
            },
            {
                "key": "m4.4",
                "title": "Aula 4 - Composição e comportamento no processo",
                "minutes": 12,
                "file": "m4_4.pdf",
                "summary": "Reconhecer comportamento e risco por tipo de fibra/composição.",
            },
            {
                "key": "m4.5",
                "title": "Aula 5 - Lote e rastreabilidade (regras e exceções)",
                "minutes": 12,
                "file": "m4_5.pdf",
                "summary": "Controlar lote e rastreabilidade para evitar variação e barramento.",
            },
            {
                "key": "m4.6",
                "title": "Aula 6 - Elastano (regras de ouro)",
                "minutes": 12,
                "file": "m4_6.pdf",
                "summary": "Evitar defeitos por elastano errado/contaminado.",
            },
            {
                "key": "m4.7",
                "title": "Aula 7 - Armazenagem e manuseio (7 regras)",
                "minutes": 12,
                "file": "m4_7.pdf",
                "summary": "Preservar qualidade do fio com armazenamento e manuseio corretos.",
            },
            {
                "key": "m4.8",
                "title": "Aula 8 - Registro e comunicação (padrão)",
                "minutes": 12,
                "file": "m4_8.pdf",
                "summary": "Evitar que o problema volte com registro e comunicação padrão.",
            },
        ],
    },
    {
        "key": "m5",
        "title": "Módulo 5 — Função Auxiliar (0–30/60/90 dias) — rotina, abastecimento e disciplina",
        "desc": "Formar o Auxiliar na rotina: abastecimento, disciplina e prevenção de falhas.",
        "lessons": [
            {
                "key": "m5.1",
                "title": "Aula 1 - Início de turno (ritual de 5 minutos)",
                "minutes": 12,
                "file": "m5_1.pdf",
                "summary": "Criar previsibilidade e disciplina no início do turno.",
            },
            {
                "key": "m5.2",
                "title": "Aula 2 - Ronda (intervalo definido) e o que olhar sempre",
                "minutes": 12,
                "file": "m5_2.pdf",
                "summary": "Detectar desvio cedo com ronda padrão e pontos críticos.",
            },
            {
                "key": "m5.3",
                "title": "Aula 3 - Abastecimento com tear rodando (padrão seguro)",
                "minutes": 12,
                "file": "m5_3.pdf",
                "summary": "Abastecer com segurança e sem erro com tear rodando.",
            },
            {
                "key": "m5.4",
                "title": "Aula 4 - Erros críticos do auxiliar (e como bloquear)",
                "minutes": 12,
                "file": "m5_4.pdf",
                "summary": "Bloquear erros do auxiliar que viram defeito ou parada.",
            },
            {
                "key": "m5.5",
                "title": "Aula 5 - Limpeza e organização (diária x semanal)",
                "minutes": 12,
                "file": "m5_5.pdf",
                "summary": "Manter limpeza e organização para estabilidade do processo.",
            },
            {
                "key": "m5.6",
                "title": "Aula 6 - Registro e comunicação curta",
                "minutes": 12,
                "file": "m5_6.pdf",
                "summary": "Garantir rastreabilidade e alinhamento com comunicação curta.",
            },
            {
                "key": "m5.7",
                "title": "Aula 7 - Validação 30/60/90 dias (marcos)",
                "minutes": 12,
                "file": "m5_7.pdf",
                "summary": "Tornar crescimento claro e mensurável em 30/60/90 dias.",
            },
        ],
    },
    {
        "key": "m6",
        "title": "Módulo 6 — Função Tecelão (Base → Pleno → Sênior) — método de setor, defeitos e evolução",
        "desc": "Formar o Tecelão: set-up, estabilidade do tear, defeitos e evolução por nível.",
        "lessons": [
            {
                "key": "m6.1",
                "title": "Aula 1 - Níveis e critérios (Base/Pleno/Sênior)",
                "minutes": 12,
                "file": "m6_1.pdf",
                "summary": "Clarificar progressão técnica (Base/Pleno/Sênior) e critérios.",
            },
            {
                "key": "m6.2",
                "title": "Aula 2 - Rotina do tecelão (início → ronda → fim)",
                "minutes": 12,
                "file": "m6_2.pdf",
                "summary": "Criar padrão de execução do tecelão no turno.",
            },
            {
                "key": "m6.3",
                "title": "Aula 3 - Método de ronda (qualidade + estabilidade + organização)",
                "minutes": 12,
                "file": "m6_3.pdf",
                "summary": "Inspecionar qualidade, estabilidade e organização com método.",
            },
            {
                "key": "m6.4",
                "title": "Aula 4 - Top 10 defeitos (linguagem comum)",
                "minutes": 12,
                "file": "m6_4.pdf",
                "summary": "Diagnosticar rápido usando linguagem comum de defeitos.",
            },
            {
                "key": "m6.5",
                "title": "Aula 5 - Ação rápida segura (limites de alçada)",
                "minutes": 12,
                "file": "m6_5.pdf",
                "summary": "Corrigir com segurança respeitando limites de alçada.",
            },
            {
                "key": "m6.6",
                "title": "Aula 6 - Registro e marcação (para revisão entender em 10s)",
                "minutes": 12,
                "file": "m6_6.pdf",
                "summary": "Deixar o problema claro para revisão em segundos (marcação/registro).",
            },
            {
                "key": "m6.7",
                "title": "Aula 7 - Qualidade x produtividade (decisão do mundo real)",
                "minutes": 12,
                "file": "m6_7.pdf",
                "summary": "Decidir com critério entre qualidade e produtividade no mundo real.",
            },
            {
                "key": "m6.8",
                "title": "Aula 8 - Comunicação e escalonamento (líder/mecânico/qualidade)",
                "minutes": 12,
                "file": "m6_8.pdf",
                "summary": "Chamar a área certa com dados mínimos (escalonamento).",
            },
            {
                "key": "m6.9",
                "title": "Aula 9 - Sênior e multiplicação (como treinar)",
                "minutes": 12,
                "file": "m6_9.pdf",
                "summary": "Multiplicar conhecimento: como treinar no posto.",
            },
        ],
    },
    {
        "key": "m7",
        "title": "Módulo 7 — Qualidade no Olho + Ação Rápida (Viu → Confirmou → Ação → Registro → Escalonou)",
        "desc": "Treinar qualidade no olho e ação rápida com registro e escalonamento.",
        "lessons": [
            {
                "key": "m7.1",
                "title": "Aula 1 - O método M6 (fluxo padrão)",
                "minutes": 12,
                "file": "m7_1.pdf",
                "summary": "Aplicar o fluxo padrão de ação rápida na qualidade (método).",
            },
            {
                "key": "m7.2",
                "title": "Aula 2 - Confirmar: direito/avesso + repetição + localização",
                "minutes": 12,
                "file": "m7_2.pdf",
                "summary": "Confirmar defeito com teste simples (direito/avesso, repetição, localização).",
            },
            {
                "key": "m7.3",
                "title": "Aula 3 - Crítico vs controlável (decisão)",
                "minutes": 12,
                "file": "m7_3.pdf",
                "summary": "Decidir o que é crítico vs controlável e quando escalar.",
            },
            {
                "key": "m7.4",
                "title": "Aula 4 - Marcação e registro (dados mínimos)",
                "minutes": 12,
                "file": "m7_4.pdf",
                "summary": "Padronizar marcação e registro com dados mínimos.",
            },
            {
                "key": "m7.5",
                "title": "Aula 5 - Escalonamento (mensagem curta)",
                "minutes": 12,
                "file": "m7_5.pdf",
                "summary": "Escalonar com mensagem curta e completa.",
            },
            {
                "key": "m7.6",
                "title": "Aula 6 - Rotina diária (microdose)",
                "minutes": 12,
                "file": "m7_6.pdf",
                "summary": "Fixar o padrão com microdoses diárias.",
            },
        ],
    },
    {
        "key": "m8",
        "title": "Módulo 8 — Rotinas de Estabilidade (5 Regras + checklists + sinais fracos)",
        "desc": "Implantar rotinas de estabilidade com checklists e reação a sinais fracos.",
        "lessons": [
            {
                "key": "m8.1",
                "title": "Aula 1 - Conceito de estabilidade",
                "minutes": 12,
                "file": "m8_1.pdf",
                "summary": "Entender o conceito de estabilidade e por que ele sustenta o resultado.",
            },
            {
                "key": "m8.2",
                "title": "Aula 2 - 5 Regras de Ouro e por que elas pagam a conta",
                "minutes": 12,
                "file": "m8_2.pdf",
                "summary": "Aplicar as 5 regras de ouro e criar disciplina de turno.",
            },
            {
                "key": "m8.3",
                "title": "Aula 3 - Checklists curtos (início e fim de peça)",
                "minutes": 12,
                "file": "m8_3.pdf",
                "summary": "Executar checklists curtos no início e fim de peça.",
            },
            {
                "key": "m8.4",
                "title": "Aula 4 - Sinais fracos (como enxergar cedo)",
                "minutes": 12,
                "file": "m8_4.pdf",
                "summary": "Enxergar sinais fracos antes de virar defeito ou parada.",
            },
            {
                "key": "m8.5",
                "title": "Aula 5 - Organização, registro e auditoria do líder",
                "minutes": 12,
                "file": "m8_5.pdf",
                "summary": "Sustentar padrão com organização, registro e auditoria rápida do líder.",
            },
            {
                "key": "m8.6",
                "title": "Aula 6 - Simulações e revalidação",
                "minutes": 12,
                "file": "m8_6.pdf",
                "summary": "Treinar com simulações e revalidar para consolidar.",
            },
        ],
    },
    {
        "key": "m9",
        "title": "Módulo 9 — Diferenciais de Performance (nível avançado)",
        "desc": "Elevar performance avançada: mapear perdas, decidir melhor e treinar multiplicadores.",
        "lessons": [
            {
                "key": "m9.1",
                "title": "Aula 1 - Mapa de perdas e desperdícios silenciosos",
                "minutes": 12,
                "file": "m9_1.pdf",
                "summary": "Enxergar onde a produção vaza (perdas silenciosas).",
            },
            {
                "key": "m9.2",
                "title": "Aula 2 - Tomada de decisão avançada (qualidade x produtividade)",
                "minutes": 12,
                "file": "m9_2.pdf",
                "summary": "Evitar decisões caras por instinto com critérios claros.",
            },
            {
                "key": "m9.3",
                "title": "Aula 3 - Melhoria contínua no posto (kaizen simples)",
                "minutes": 12,
                "file": "m9_3.pdf",
                "summary": "Melhorar no posto com kaizen simples e prático.",
            },
            {
                "key": "m9.4",
                "title": "Aula 4 - Como treinar e validar (multiplicador)",
                "minutes": 12,
                "file": "m9_4.pdf",
                "summary": "Criar formadores internos: treinar e validar multiplicadores.",
            },
        ],
    },
    {
        "key": "m10",
        "title": "Módulo 10 — Recebimento e Armazenagem de Fios e Insumos (almoxarifado + ponto de uso)",
        "desc": "Padronizar recebimento e armazenagem de fios/insumos com preservação e rastreabilidade.",
        "lessons": [
            {
                "key": "m10.1",
                "title": "Aula 1 - Conferência de recebimento (o que não pode passar)",
                "minutes": 12,
                "file": "m10_1.pdf",
                "summary": "Evitar entrada de erro no sistema com conferência de recebimento.",
            },
            {
                "key": "m10.2",
                "title": "Aula 2 - Armazenagem (layout, proteção e organização)",
                "minutes": 12,
                "file": "m10_2.pdf",
                "summary": "Preservar fio e evitar contaminação com armazenagem correta.",
            },
            {
                "key": "m10.3",
                "title": "Aula 3 - Separação e abastecimento (interface com malharia)",
                "minutes": 12,
                "file": "m10_3.pdf",
                "summary": "Prevenir mistura e erro no ponto de uso (separação/abastecimento).",
            },
            {
                "key": "m10.4",
                "title": "Aula 4 - Rastreabilidade e inventário simples",
                "minutes": 12,
                "file": "m10_4.pdf",
                "summary": "Garantir controle simples de lotes com rastreabilidade e inventário.",
            },
        ],
    },
    {
        "key": "m11",
        "title": "Módulo 11 — Set-up e Carregamento de Tear (troca de artigo, abastecimento, passamentos)",
        "desc": "Padronizar troca de artigo: set-up, carregamento e passamentos com segurança.",
        "lessons": [
            {
                "key": "m11.1",
                "title": "Aula 1 - Sequência padrão de set-up",
                "minutes": 12,
                "file": "m11_1.pdf",
                "summary": "Reduzir tempo e erro com sequência padrão de set-up.",
            },
            {
                "key": "m11.2",
                "title": "Aula 2 - Passamento de fios e elastano (pontos críticos)",
                "minutes": 12,
                "file": "m11_2.pdf",
                "summary": "Evitar falha por montagem nos passamentos (fios e elastano).",
            },
            {
                "key": "m11.3",
                "title": "Aula 3 - Checklist de liberação do tear",
                "minutes": 12,
                "file": "m11_3.pdf",
                "summary": "Confirmar liberação do tear com checklist antes de rodar lote.",
            },
            {
                "key": "m11.4",
                "title": "Aula 4 - Transferência entre turnos (setup em aberto)",
                "minutes": 12,
                "file": "m11_4.pdf",
                "summary": "Evitar herança ruim com transferência de set-up entre turnos.",
            },
        ],
    },
    {
        "key": "m12",
        "title": "Módulo 12 — Limpeza de Tear e Ambiente (padrão por peça/turno)",
        "desc": "Padronizar limpeza de tear e ambiente para reduzir defeitos e paradas.",
        "lessons": [
            {
                "key": "m12.1",
                "title": "Aula 1 - Por que limpeza é qualidade + estabilidade",
                "minutes": 12,
                "file": "m12_1.pdf",
                "summary": "Entender limpeza como qualidade + estabilidade.",
            },
            {
                "key": "m12.2",
                "title": "Aula 2 - Padrão de limpeza (fim de peça)",
                "minutes": 12,
                "file": "m12_2.pdf",
                "summary": "Executar padrão de limpeza no fim de peça com método.",
            },
            {
                "key": "m12.3",
                "title": "Aula 3 - Rotina diária x semanal",
                "minutes": 12,
                "file": "m12_3.pdf",
                "summary": "Manter disciplina com rotina diária x semanal.",
            },
        ],
    },
    {
        "key": "m13",
        "title": "Módulo 13 — Paradas Automáticas e Troubleshooting Operacional (resposta padrão)",
        "desc": "Responder paradas automáticas com troubleshooting padrão e rápido.",
        "lessons": [
            {
                "key": "m13.1",
                "title": "Aula 1 - Tipos de parada e riscos",
                "minutes": 12,
                "file": "m13_1.pdf",
                "summary": "Ler tipos de parada e agir com segurança.",
            },
            {
                "key": "m13.2",
                "title": "Aula 2 - Rompimento de fio (resposta padrão)",
                "minutes": 12,
                "file": "m13_2.pdf",
                "summary": "Aplicar resposta padrão no rompimento de fio.",
            },
            {
                "key": "m13.3",
                "title": "Aula 3 - Troca de agulha/platina (quando permitido)",
                "minutes": 12,
                "file": "m13_3.pdf",
                "summary": "Trocar agulha/platina com critério e dentro do permitido.",
            },
            {
                "key": "m13.4",
                "title": "Aula 4 - Corte de peça e segregação",
                "minutes": 12,
                "file": "m13_4.pdf",
                "summary": "Proteger o lote com corte de peça e segregação correta.",
            },
            {
                "key": "m13.5",
                "title": "Aula 5 - Chamado para manutenção (dados mínimos)",
                "minutes": 12,
                "file": "m13_5.pdf",
                "summary": "Abrir chamado para manutenção com dados mínimos e rastreáveis.",
            },
        ],
    },
    {
        "key": "m14",
        "title": "Módulo 14 — Manutenção Autônoma (TPM) e Interface com Manutenção Planejada",
        "desc": "Executar manutenção autônoma (TPM) e integrar com manutenção planejada.",
        "lessons": [
            {
                "key": "m14.1",
                "title": "Aula 1 - TPM: o que é e o que não é",
                "minutes": 12,
                "file": "m14_1.pdf",
                "summary": "Separar o que é do operador vs manutenção (TPM na prática).",
            },
            {
                "key": "m14.2",
                "title": "Aula 2 - Inspeção por condição (sinais fracos mecânicos)",
                "minutes": 12,
                "file": "m14_2.pdf",
                "summary": "Detectar falha antes de quebrar com inspeção por condição.",
            },
            {
                "key": "m14.3",
                "title": "Aula 3 - Paradas programadas e padrão de intervenção",
                "minutes": 12,
                "file": "m14_3.pdf",
                "summary": "Padronizar intervenção em paradas programadas.",
            },
            {
                "key": "m14.4",
                "title": "Aula 4 - Indicadores de manutenção ligados à operação",
                "minutes": 12,
                "file": "m14_4.pdf",
                "summary": "Conectar manutenção com estabilidade via indicadores ligados à operação.",
            },
        ],
    },
    {
        "key": "m15",
        "title": "Módulo 15 — PCP e Eficiência na Malharia (cálculo de produção, apontamentos e perdas)",
        "desc": "Gerir PCP e eficiência: apontamentos, cálculo de produção e perdas.",
        "lessons": [
            {
                "key": "m15.1",
                "title": "Aula 1 - Capacidade e cálculo básico",
                "minutes": 12,
                "file": "m15_1.pdf",
                "summary": "Entender capacidade e cálculo básico antes de cobrar resultado.",
            },
            {
                "key": "m15.2",
                "title": "Aula 2 - Apontamento (paradas e motivos)",
                "minutes": 12,
                "file": "m15_2.pdf",
                "summary": "Evitar dado errado com apontamento correto de paradas/motivos.",
            },
            {
                "key": "m15.3",
                "title": "Aula 3 - OEE/eficiência e leitura prática",
                "minutes": 12,
                "file": "m15_3.pdf",
                "summary": "Transformar OEE/eficiência em leitura prática no dia a dia.",
            },
            {
                "key": "m15.4",
                "title": "Aula 4 - Planejamento de set-up e mix",
                "minutes": 12,
                "file": "m15_4.pdf",
                "summary": "Planejar set-up e mix para reduzir trocas e perdas.",
            },
            {
                "key": "m15.5",
                "title": "Aula 5 - Ritual de gestão (diário/semanal)",
                "minutes": 12,
                "file": "m15_5.pdf",
                "summary": "Liderar com clareza em ritual de gestão diário/semanal.",
            },
        ],
    },
    {
        "key": "m16",
        "title": "Módulo 16 — Controle de Qualidade de Fios e Malhas (sistema, critérios e laboratório)",
        "desc": "Controlar qualidade de fios e malhas: critérios, inspeção e laboratório.",
        "lessons": [
            {
                "key": "m16.1",
                "title": "Aula 1 - Sistema de qualidade no fluxo (onde controlar)",
                "minutes": 12,
                "file": "m16_1.pdf",
                "summary": "Definir onde controlar qualidade no fluxo e quem decide.",
            },
            {
                "key": "m16.2",
                "title": "Aula 2 - Qualidade de fio (conceitos e ensaios)",
                "minutes": 12,
                "file": "m16_2.pdf",
                "summary": "Usar conceitos e ensaios para avaliar qualidade de fio.",
            },
            {
                "key": "m16.3",
                "title": "Aula 3 - Qualidade de malha crua (critérios)",
                "minutes": 12,
                "file": "m16_3.pdf",
                "summary": "Aplicar critérios consistentes para qualidade de malha crua.",
            },
            {
                "key": "m16.4",
                "title": "Aula 4 - Laboratório e normas (o necessário)",
                "minutes": 12,
                "file": "m16_4.pdf",
                "summary": "Entender o essencial de laboratório e normas.",
            },
            {
                "key": "m16.5",
                "title": "Aula 5 - Tratamento de não conformidade (NC)",
                "minutes": 12,
                "file": "m16_5.pdf",
                "summary": "Tratar não conformidades para evitar reincidência.",
            },
        ],
    },
    {
        "key": "m17",
        "title": "Módulo 17 — Engenharia de Produto e Desenvolvimento de Novas Malhas (do briefing ao artigo liberado)",
        "desc": "Desenvolver novas malhas: do briefing ao artigo liberado e padronizado.",
        "lessons": [
            {
                "key": "m17.1",
                "title": "Aula 1 - Briefing e requisitos (cliente/coleção/processo)",
                "minutes": 12,
                "file": "m17_1.pdf",
                "summary": "Fechar briefing com requisitos claros (cliente/coleção/processo).",
            },
            {
                "key": "m17.2",
                "title": "Aula 2 - Ficha técnica e parâmetros do tear",
                "minutes": 12,
                "file": "m17_2.pdf",
                "summary": "Construir ficha técnica e parâmetros do tear com consistência.",
            },
            {
                "key": "m17.3",
                "title": "Aula 3 - Piloto/amostra (planejamento e execução)",
                "minutes": 12,
                "file": "m17_3.pdf",
                "summary": "Planejar e executar piloto/amostra reduzindo tentativas.",
            },
            {
                "key": "m17.4",
                "title": "Aula 4 - Validação (qualidade e beneficiamento)",
                "minutes": 12,
                "file": "m17_4.pdf",
                "summary": "Validar com qualidade e beneficiamento antes de escalar.",
            },
            {
                "key": "m17.5",
                "title": "Aula 5 - Liberação e padronização (transferência para produção)",
                "minutes": 12,
                "file": "m17_5.pdf",
                "summary": "Liberar e padronizar artigo para produção sem variação.",
            },
        ],
    },
    {
        "key": "m18",
        "title": "Módulo 18 — Beneficiamento e Interface (por que o cru precisa sair certo)",
        "desc": "Alinhar cru e beneficiamento para evitar retrabalho e reclamações.",
        "lessons": [
            {
                "key": "m18.1",
                "title": "Aula 1 - O que o beneficiamento ‘revela’",
                "minutes": 12,
                "file": "m18_1.pdf",
                "summary": "Entender o que o beneficiamento revela sobre o cru.",
            },
            {
                "key": "m18.2",
                "title": "Aula 2 - Requisitos de rolo para seguir no fluxo",
                "minutes": 12,
                "file": "m18_2.pdf",
                "summary": "Garantir requisitos mínimos do rolo para seguir no fluxo.",
            },
            {
                "key": "m18.3",
                "title": "Aula 3 - Padrão de feedback entre áreas",
                "minutes": 12,
                "file": "m18_3.pdf",
                "summary": "Padronizar feedback entre áreas para cortar retrabalho.",
            },
        ],
    },
    {
        "key": "m19",
        "title": "Módulo 19 — Logística de Rolos e Expedição (proteção, rastreabilidade e manuseio)",
        "desc": "Garantir logística de rolos: proteção, rastreabilidade e manuseio seguro.",
        "lessons": [
            {
                "key": "m19.1",
                "title": "Aula 1 - Manuseio seguro de rolos",
                "minutes": 12,
                "file": "m19_1.pdf",
                "summary": "Evitar dano e risco no manuseio de rolos.",
            },
            {
                "key": "m19.2",
                "title": "Aula 2 - Identificação e documentação",
                "minutes": 12,
                "file": "m19_2.pdf",
                "summary": "Garantir rastreabilidade com identificação e documentação.",
            },
            {
                "key": "m19.3",
                "title": "Aula 3 - Armazenagem (padrão)",
                "minutes": 12,
                "file": "m19_3.pdf",
                "summary": "Preservar rolos com armazenamento padrão (sem vincos/contaminação).",
            },
        ],
    },
    {
        "key": "m20",
        "title": "Módulo 20 — Liderança como Instrutor (formar e sustentar o padrão)",
        "desc": "Desenvolver líderes-instrutores para treinar, validar e sustentar o padrão.",
        "lessons": [
            {
                "key": "m20.1",
                "title": "Aula 1 - Como ensinar adulto no chão (microdose)",
                "minutes": 12,
                "file": "m20_1.pdf",
                "summary": "Treinar adulto no chão com microdoses (retém e aplica).",
            },
            {
                "key": "m20.2",
                "title": "Aula 2 - Checklist e prova prática",
                "minutes": 12,
                "file": "m20_2.pdf",
                "summary": "Padronizar validação com checklist e prova prática.",
            },
            {
                "key": "m20.3",
                "title": "Aula 3 - Feedback e correção sem conflito",
                "minutes": 12,
                "file": "m20_3.pdf",
                "summary": "Dar feedback e corrigir sem conflito para aumentar adesão.",
            },
            {
                "key": "m20.4",
                "title": "Aula 4 - Auditoria rápida (2–5 min) e rotina de gestão",
                "minutes": 12,
                "file": "m20_4.pdf",
                "summary": "Sustentar padrão com auditoria rápida e rotina de gestão.",
            },
            {
                "key": "m20.5",
                "title": "Aula 5 - Matriz de habilidades (desenho)",
                "minutes": 12,
                "file": "m20_5.pdf",
                "summary": "Tornar lacunas visíveis com matriz de habilidades.",
            },
            {
                "key": "m20.6",
                "title": "Aula 6 - Reciclagens e revalidações",
                "minutes": 12,
                "file": "m20_6.pdf",
                "summary": "Evitar ‘treino que some’ com reciclagens e revalidações.",
            },
            {
                "key": "m20.7",
                "title": "Aula 7 - Trilha de carreira (critérios e incentivos)",
                "minutes": 12,
                "file": "m20_7.pdf",
                "summary": "Clarificar carreira com critérios e incentivos para retenção.",
            },
            {
                "key": "m20.8",
                "title": "Aula 8 - Auditoria do programa (governança)",
                "minutes": 12,
                "file": "m20_8.pdf",
                "summary": "Garantir entrega do programa com auditoria de governança.",
            },
        ],
    },
    {
        "key": "m21",
        "title": "Módulo 21 — Gestão do Programa (Matriz de Habilidades, Reciclagens e Trilha de Carreira)",
        "desc": "Sustentar o Programa com matriz de habilidades, reciclagens por risco e trilha de carreira com critérios claros.",
        "lessons": [
            {
                "key": "m21.1",
                "title": "Aula 1 - Matriz de habilidades (desenho)",
                "minutes": 12,
                # arquivo protegido (fica em training_files/)
                "file": "m21_1.pdf",
                "summary": "Tornar lacunas visíveis e treináveis com evidências por função e setor.",
            },
            {
                "key": "m21.2",
                "title": "Aula 2 - Reciclagens e revalidações",
                "minutes": 12,
                # arquivo protegido (fica em training_files/)
                "file": "m21_2.pdf",
                "summary": "Garantir reciclagens por risco e revalidações para evitar “treino que some”.",
            },
            {
                "key": "m21.3",
                "title": "Aula 3 - Trilha de carreira (critérios e incentivos)",
                "minutes": 12,
                # arquivo protegido (fica em training_files/)
                "file": "m21_3.pdf",
                "summary": "Definir progressão técnica e incentivos para atrair, reter e formar multiplicadores.",
            },
            {
                "key": "m21.4",
                "title": "Aula 4 - Auditoria do programa (governança)",
                "minutes": 12,
                # arquivo protegido (fica em training_files/)
                "file": "m21_4.pdf",
                "summary": "Acompanhar KPIs e rodar auditorias mensais com ações corretivas.",
            },
        ],
    },
]

def get_module(module_key: str) -> Optional[Dict[str, Any]]:
    k = (module_key or "").strip().lower()
    for m in TRAINING_CATALOG:
        if m.get("key") == k:
            return m
    return None

def get_lesson(module_key: str, lesson_key: str) -> Optional[Dict[str, Any]]:
    m = get_module(module_key)
    if not m:
        return None
    lk = (lesson_key or "").strip().lower()
    for a in (m.get("lessons") or []):
        if a.get("key") == lk:
            return a
    return None
