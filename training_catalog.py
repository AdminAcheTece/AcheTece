# training_catalog.py
# Catálogo do Treinamento Operacional (Opção 2)
# Você edita aqui: módulos, aulas, PDFs, quizzes etc.

from __future__ import annotations

from typing import Any, Dict, List, Optional

TRAINING_CATALOG: List[Dict[str, Any]] = [
    {
        "key": "m0",
        "title": "Módulo 0 — Apresentação do Programa de Treinamento",
        "desc": "Apresentar e aprovar o Programa (escopo, investimento, governança).",
        "lessons": [
            {
                "key": "a1",
                "title": "Aula 1 - Contexto e por que agora",
                "minutes": 12,
                # arquivo protegido (fica em training_files/)
                "file": "m0_onboarding.pdf",
                "summary": "Diante da alta rotatividade e dificuldade de contratação, é essencial padronizar o onboarding com rotina e método para proteger conhecimento crítico, elevar qualidade e produtividade e reduzir riscos de segurança.",
            },
            {
                "key": "a2",
                "title": "Aula 2 - Objetivos estratégicos do programa",
                "minutes": 10,
                "file": "m0_aula_2.pdf",
                "summary": "Reduzir o tempo até a autonomia, estabilizar o processo, cortar retrabalho por defeitos e padronizar decisões, com trilha clara de crescimento por função.",
            },
            {
                "key": "a3",
                "title": "Aula 3 - Escopo (áreas cobertas)",
                "minutes": 10,
                "file": "m0_pedir_ajuda.pdf",
                "summary": "Escopo completo da malharia circular: onboarding e regras, segurança, fundamentos têxteis, fluxo ponta a ponta, funções, qualidade e rotinas, TPM/manutenção, PCP/OEE e desenvolvimento/liberação de artigos.",
            },
            {
                "key": "a4",
                "title": "Aula 4 - Método (como o aprendizado vira resultado)",
                "minutes": 10,
                "file": "m0_pedir_ajuda.pdf",
                "summary": "Método prático e curto no chão de fábrica, com microdoses diárias e liberação por competência, apoiado por padrões simples e materiais de setor (cartões, checklists, registros e mini-quizzes).",
            },
                            
        ],
    },
    {
        "key": "m1",
        "title": "Módulo 1 — Integração + Regras do Jogo do Setor (onboarding)",
        "desc_title": "Resultado (competência observável):",
        "desc_items": [
          "Explica o que é sucesso no setor: segurança primeiro, depois qualidade, depois produtividade.",
          "Conhece regras do setor (conduta, comunicação, disciplina de corredor/posto).",
          "Sabe pedir ajuda do jeito certo: mensagem curta + dados mínimos.",
          "Entende a trilha: o que aprende agora, o que vem depois e como será avaliado."
        ],
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
