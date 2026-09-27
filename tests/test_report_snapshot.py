from __future__ import annotations

import pandas as pd
from reportlab.platypus import Paragraph, Table

import turnaround.report as report


def _plain(value):
    if isinstance(value, Paragraph):
        return value.getPlainText()
    return str(value)


def test_conditional_report_uses_current_snapshot_and_makespan(monkeypatch):
    captured = {}

    def capture_story(story):
        captured["story"] = story
        return b"snapshot-pdf"

    monkeypatch.setattr(report, "_build_pdf", capture_story)

    schedule_df = pd.DataFrame(
        [
            {
                "_Ordem": 0,
                "ID": "1",
                "Atividade": "Inspeção",
                "Modo": "base",
                "Início (h)": 0.0,
                "Fim (h)": 7.0,
                "Duração (h)": 7.0,
                "Congelada": True,
                "Crítica atual": False,
                "Controla por": "",
            },
            {
                "_Ordem": 1,
                "ID": "DS-001",
                "Atividade": "Reparo descoberto",
                "Modo": "campo",
                "Início (h)": 7.0,
                "Fim (h)": 27.0,
                "Duração (h)": 20.0,
                "Congelada": False,
                "Crítica atual": True,
                "Controla por": "gate",
            },
        ]
    )
    activation_df = pd.DataFrame(
        [
            {
                "ID": "DS-001",
                "Atividade": "Reparo descoberto",
                "Tipo": "mandatory",
                "Estado": "active",
                "Motivo": "dynamic discovery",
            }
        ]
    )
    resource_df = pd.DataFrame(
        [
            {
                "Recurso": "Soldador",
                "Base": "não informada",
                "Cenário": 1.0,
                "Δ vs base": "—",
            }
        ]
    )
    execution_df = pd.DataFrame(
        [
            {"Parâmetro": "Snapshot", "Valor": "abc123def456"},
            {"Parâmetro": "Makespan reprogramado", "Valor": "27.0 h"},
            {"Parâmetro": "Achados observados", "Valor": "4: crack_detected"},
        ]
    )
    dynamic_df = pd.DataFrame(
        [
            {
                "ID": "DS-001",
                "Atividade": "Reparo descoberto",
                "Descoberta em (h)": 7.0,
                "Recursos": "Soldador=1",
                "Bloqueia": "9",
            }
        ]
    )
    baseline_history_df = pd.DataFrame(
        [
            {
                "Linha de base": "Original",
                "Anterior": "—",
                "Makespan (h)": 17.0,
                "Janela (h)": 24.0,
                "Δ vs anterior (h)": 0.0,
                "Δ vs original (h)": 0.0,
                "Aprovada em": "26/09/2026 18:00",
                "Aprovado por": "Planejamento",
                "Motivo": "Plano original",
                "Snapshot": "baseline0001",
                "Observação": "—",
            },
            {
                "Linha de base": "Rev.1",
                "Anterior": "Original",
                "Makespan (h)": 23.0,
                "Janela (h)": 26.0,
                "Δ vs anterior (h)": 6.0,
                "Δ vs original (h)": 6.0,
                "Aprovada em": "26/09/2026 19:00",
                "Aprovado por": "Gerência da parada",
                "Motivo": "Novo escopo aprovado",
                "Snapshot": "snap-rev1",
                "Observação": "Janela revisada",
            },
        ]
    )

    result = report.build_conditional_management_pdf(
        project_name="Turnaround teste",
        baseline_makespan=17.0,
        current_makespan=27.0,
        deadline=24.0,
        governing_baseline_makespan=23.0,
        governing_baseline_label="Rev.1",
        current_time=7.0,
        total_cost=1000.0,
        new_scope_count=1,
        dynamic_scope_count=1,
        strategy="test",
        activation_df=activation_df,
        schedule_df=schedule_df,
        total_start_deviation=4.0,
        max_start_deviation=4.0,
        stability_compared_tasks=1,
        stability_weight=1.0,
        critical_ids={"DS-001"},
        critical_path_label="DS-001",
        snapshot_id="abc123def456",
        session_id="session-xyz",
        resource_scenario_df=resource_df,
        execution_state_df=execution_df,
        dynamic_scope_df=dynamic_df,
        baseline_revisions_df=baseline_history_df,
    )

    assert result == b"snapshot-pdf"
    story = captured["story"]

    paragraphs = [
        item.getPlainText()
        for item in story
        if isinstance(item, Paragraph)
    ]
    assert any(
        "Snapshot: abc123def456 · hora corrente 7.0 h" in text
        for text in paragraphs
    )
    assert any("O estado atual ativou 1 atividade(s)" in text for text in paragraphs)

    metric_cards = [
        item
        for item in story
        if hasattr(item, "metrics")
    ]
    assert metric_cards
    metrics = dict(metric_cards[0].metrics)
    assert metrics["Baseline original"] == "17.0 h"
    assert metrics["Baseline vigente"] == "23.0 h · Rev.1"
    assert metrics["Forecast atual"] == "27.0 h"
    assert metrics["Δ vs original"] == "+10.0 h"
    assert metrics["Δ vs vigente"] == "+4.0 h"
    assert metrics["Excesso da janela"] == "3.0 h"
    assert len(metrics) == 6

    table_text = []
    for item in story:
        if not isinstance(item, Table):
            continue
        for row in item._cellvalues:
            table_text.extend(_plain(cell) for cell in row)

    assert "Makespan reprogramado" not in table_text
    assert "Sessão" not in table_text
    assert "Soldador" in table_text
    assert "1.0" in table_text
    assert "Reparo descoberto" in table_text
    assert "Rev.1" in table_text
    assert "Original" in table_text
    assert "Gerência da parada" in table_text
    assert "snap-rev1" in table_text
    assert "Novo escopo aprovado" in table_text
    assert "Janela revisada" in table_text
