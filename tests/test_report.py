import pandas as pd

from turnaround.report import (
    _GanttFlowable,
    build_base_management_pdf,
    build_conditional_management_pdf,
)


def test_base_management_pdf_is_valid_binary():
    schedule_df = pd.DataFrame(
        [
            {
                "ID": "10",
                "Atividade": "Abrir equipamento",
                "Inicio_h": 0,
                "Fim_h": 4,
                "Duracao_h": 4,
                "WBS": "1.1",
            },
            {
                "ID": "20",
                "Atividade": "Inspecionar",
                "Inicio_h": 4,
                "Fim_h": 6,
                "Duracao_h": 2,
                "WBS": "1.2",
            },
        ]
    )
    criticality_df = pd.DataFrame(
        [
            {
                "ID": "10",
                "Atividade": "Abrir equipamento",
                "ES": 0,
                "EF": 4,
                "Float_h": 0,
                "Critical": True,
            }
        ]
    )
    resource_df = pd.DataFrame(
        [
            {
                "Recurso": "Mecânica",
                "Capacidade": 4,
                "Pico": 4,
                "Utilizacao_%": 82.5,
            }
        ]
    )
    risk = {
        "p50_h": 20.0,
        "p80_h": 22.0,
        "p90_h": 24.0,
        "probability_meet_deadline": 0.84,
    }

    pdf = build_base_management_pdf(
        project_name="Parada P-101",
        hours_per_day=8,
        makespan_h=20,
        deadline_h=24,
        priority_rule="minimum_float",
        comparison={
            "unconstrained_makespan_h": 18,
            "resource_penalty_h": 2,
        },
        schedule_df=schedule_df,
        criticality_df=criticality_df,
        resource_df=resource_df,
        risk=risk,
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2500
    assert b"%%EOF" in pdf[-1024:]


def test_conditional_management_pdf_is_valid_binary():
    activation_df = pd.DataFrame(
        [
            {
                "ID": "I",
                "Atividade": "Inspecionar bomba",
                "Tipo": "mandatory",
                "Estado": "active",
                "Motivo": "atividade já completed",
            },
            {
                "ID": "R",
                "Atividade": "Trocar rolamento",
                "Tipo": "conditional",
                "Estado": "active",
                "Motivo": "condição de ativação satisfeita",
            },
        ]
    )
    schedule_df = pd.DataFrame(
        [
            {
                "ID": "I",
                "Atividade": "Inspecionar bomba",
                "Modo": "base",
                "Início (h)": 0,
                "Fim (h)": 2,
                "Duração (h)": 2,
                "Congelada": True,
            },
            {
                "ID": "R",
                "Atividade": "Trocar rolamento",
                "Modo": "reforco",
                "Início (h)": 2,
                "Fim (h)": 5,
                "Duração (h)": 3,
                "Congelada": False,
            },
        ]
    )

    pdf = build_conditional_management_pdf(
        project_name="Turnaround Kinder Ovo",
        baseline_makespan=10,
        current_makespan=13,
        deadline=14,
        current_time=2,
        total_cost=1200,
        new_scope_count=1,
        strategy="enumeration+ssgs",
        activation_df=activation_df,
        schedule_df=schedule_df,
    )

    assert pdf.startswith(b"%PDF")
    assert len(pdf) > 2500
    assert b"%%EOF" in pdf[-1024:]


def test_conditional_gantt_can_preserve_project_order():
    schedule_df = pd.DataFrame(
        [
            {
                "_Ordem": 1,
                "ID": "2",
                "Atividade": "Atividade 2",
                "Início (h)": 8,
                "Fim (h)": 10,
            },
            {
                "_Ordem": 0,
                "ID": "1",
                "Atividade": "Atividade 1",
                "Início (h)": 12,
                "Fim (h)": 14,
            },
        ]
    )

    gantt = _GanttFlowable(
        schedule_df,
        order_column="_Ordem",
    )

    assert gantt.schedule_df["ID"].tolist() == ["1", "2"]
