# training_catalog.py
# Catálogo do Treinamento Operacional (Opção 2)
# Você edita aqui: módulos, aulas, PDFs, quizzes etc.

from __future__ import annotations

from typing import Any, Dict, List, Optional

TRAINING_CATALOG: List[Dict[str, Any]] = [
    {
        "key": "m0",
        "title": "Módulo 0 — Boas-vindas e regras de chão de fábrica",
        "desc": "Visão geral do processo, postura operacional, padrões mínimos e segurança.",
        "lessons": [
            {
                "key": "a1",
                "title": "Fluxo ponta a ponta (fio → tear → rolo → revisão → expedição)",
                "minutes": 12,
                # arquivo protegido (fica em training_files/)
                "file": "m0_fluxo.pdf",
                "summary": "Entender o processo completo e onde nascem os principais desperdícios.",
            },
            {
                "key": "a2",
                "title": "Padrão mínimo para pedir ajuda (4 itens) + registro de anomalias",
                "minutes": 10,
                "file": "m0_pedir_ajuda.pdf",
                "summary": "Como pedir ajuda de forma objetiva e rastreável.",
                "quiz": [
                    {
                        "q": "Qual é o objetivo do padrão mínimo ao pedir ajuda?",
                        "options": [
                            "Acelerar a resposta e evitar retrabalho",
                            "Substituir o líder de turno",
                            "Evitar que a manutenção atue",
                            "Aumentar o tempo de máquina parada",
                        ],
                        "answer": 0,
                    }
                ],
            },
        ],
    },
    {
        "key": "m1",
        "title": "Módulo 1 — Operação básica no tear (rotinas e atenção ao detalhe)",
        "desc": "Rotina do operador, pontos críticos e como evitar defeitos comuns.",
        "lessons": [
            {
                "key": "a1",
                "title": "Checklist diário do tear: limpeza, lubrificação e inspeção visual",
                "minutes": 15,
                "file": "m1_checklist_diario.pdf",
                "summary": "Rotina simples que previne defeitos repetitivos e paradas.",
            },
            {
                "key": "a2",
                "title": "Mistura de lotes: quando pode, quando não pode e consequências",
                "minutes": 14,
                "file": "m1_mistura_lote.pdf",
                "summary": "Regra de negócio para evitar barramento/variação e retrabalho.",
            },
        ],
    },
    {
        "key": "m2",
        "title": "Módulo 2 — Qualidade na prática (defeitos, causas e prevenção)",
        "desc": "Como identificar defeitos, causas prováveis e ações imediatas.",
        "lessons": [
            {
                "key": "a1",
                "title": "Defeitos comuns: barramento, furos, laçadas, sujidade",
                "minutes": 18,
                "file": "m2_defeitos_comuns.pdf",
                "summary": "Reconhecer rápido, conter rápido, registrar certo.",
            }
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
