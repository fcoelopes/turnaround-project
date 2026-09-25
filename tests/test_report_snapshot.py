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

    result = report.build_conditional_management_pdf(
        project_name="Turnaround teste",
        baseline_makespan=17.0,
        current_makespan=27.0,
        deadline=24.0,
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
    assert metrics["Baseline"] == "17.0 h"
    assert metrics["Reprogramado"] == "27.0 h"
    assert metrics["Impacto"] == "+10.0 h"
    assert metrics["Excesso da janela"] == "3.0 h"
    assert len(metrics) == 4

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
