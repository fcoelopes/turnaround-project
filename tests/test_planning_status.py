from __future__ import annotations

from pathlib import Path

from turnaround.planning_status import classify_planning_status


def test_status_is_green_only_when_deterministic_and_p80_are_inside():
    result = classify_planning_status(
        makespan_h=90,
        deadline_h=100,
        p80_h=98,
        probability_meet_deadline=0.84,
    )

    assert result.deterministic == "DENTRO"
    assert result.p80 == "DENTRO"
    assert result.risk == "CONTROLADO"
    assert result.overall == "DENTRO COM CONFIANÇA P80"
    assert result.tone == "ok"


def test_status_warns_when_deterministic_fits_but_p80_is_outside():
    result = classify_planning_status(
        makespan_h=90,
        deadline_h=100,
        p80_h=108,
        probability_meet_deadline=0.42,
    )

    assert result.deterministic == "DENTRO"
    assert result.p80 == "FORA"
    assert result.risk == "EXPOSTO"
    assert result.overall == "RISCO DE PRAZO"
    assert result.tone == "warn"


def test_status_warns_when_probability_is_below_confidence_threshold():
    result = classify_planning_status(
        makespan_h=90,
        deadline_h=100,
        p80_h=100,
        probability_meet_deadline=0.79,
    )

    assert result.risk == "EXPOSTO"
    assert result.overall == "RISCO DE PRAZO"


def test_status_is_danger_when_deterministic_plan_is_outside():
    result = classify_planning_status(
        makespan_h=110,
        deadline_h=100,
        p80_h=125,
        probability_meet_deadline=0.20,
    )

    assert result.deterministic == "FORA"
    assert result.overall == "FORA DA JANELA"
    assert result.tone == "danger"


def test_status_handles_missing_deadline_explicitly():
    result = classify_planning_status(
        makespan_h=90,
        deadline_h=None,
        p80_h=105,
        probability_meet_deadline=None,
    )

    assert result.deterministic == "SEM JANELA"
    assert result.risk == "NÃO AVALIADO"
    assert result.overall == "JANELA NÃO DEFINIDA"
    assert result.tone == "warn"


def test_status_does_not_turn_green_when_risk_is_missing():
    result = classify_planning_status(
        makespan_h=90,
        deadline_h=100,
        p80_h=None,
        probability_meet_deadline=None,
    )

    assert result.deterministic == "DENTRO"
    assert result.overall == "RISCO NÃO AVALIADO"
    assert result.tone == "warn"

def test_planning_ui_has_single_managerial_status_source():
    source = (
        Path(__file__).resolve().parents[1] / "Planejamento.py"
    ).read_text(encoding="utf-8")

    assert "classify_planning_status(" in source
    assert 'tone = "ok" if probability >= 0.8 else "warn"' not in source
    assert "A classificação gerencial de prazo combina P80 e " in source
    assert "P(cumprir janela); nenhum desses sinais" in source
    assert 'title=f"Status geral: {executive_status.overall}."' in source
