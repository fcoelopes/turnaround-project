from __future__ import annotations

from turnaround import (
    DailyShift,
    PlanningScopeRisk,
    WorkingCalendar,
    build_planning_baseline,
    validate_planning_baseline_for_execution,
)
from turnaround.models import Link, ScheduledTask, Task, TurnaroundResult


def _result(*items: ScheduledTask, makespan: float | None = None) -> TurnaroundResult:
    finish = makespan if makespan is not None else max(item.finish_h for item in items)
    return TurnaroundResult(
        schedule=list(items),
        makespan_h=finish,
        resource_peak={},
        resource_utilization={},
        tardiness_h=0,
        priority_rule="minimum_float",
    )


def _formal_baseline(
    *,
    tasks: list[Task],
    result: TurnaroundResult,
    capacities: dict[str, float] | None = None,
    scope_risks: list[PlanningScopeRisk] | None = None,
):
    return build_planning_baseline(
        project_name="Parada teste",
        source_name="parada.xml",
        hours_per_day=8,
        deadline_h=24,
        capacities=capacities or {},
        capacity_origins={
            resource: "PROJECT"
            for resource in (capacities or {})
        },
        capacities_validated=True,
        tasks=tasks,
        result=result,
        scope_risks=scope_risks or [],
        scenario_name="Baseline 0",
        approved_by="Planejamento",
        approval_reason="Liberado para execução.",
    )


def test_ready_baseline_passes_execution_gate():
    tasks = [
        Task(
            id="1",
            name="Preparar",
            duration_h=2,
            resources={"Equipe": 1},
        ),
        Task(
            id="2",
            name="Executar",
            duration_h=3,
            predecessors=[Link("1")],
            resources={"Equipe": 1},
        ),
    ]
    baseline = _formal_baseline(
        tasks=tasks,
        capacities={"Equipe": 1},
        result=_result(
            ScheduledTask(
                id="1",
                name="Preparar",
                start_h=0,
                finish_h=2,
                duration_h=2,
                resources={"Equipe": 1},
            ),
            ScheduledTask(
                id="2",
                name="Executar",
                start_h=2,
                finish_h=5,
                duration_h=3,
                resources={"Equipe": 1},
            ),
        ),
    )

    report = validate_planning_baseline_for_execution(
        baseline,
        require_formal_approval=True,
    )

    assert report.ready is True
    assert report.errors == ()
    assert "Rede de precedências acíclica" in report.checks
    assert "Cronograma aprovado cobre exatamente o escopo-base" in report.checks


def test_gate_catches_dangling_link_hidden_in_potential_scope():
    tasks = [
        Task(id="1", name="Inspecionar", duration_h=2),
        Task(
            id="7",
            name="Reparo potencial",
            duration_h=4,
            predecessors=[Link("5")],
        ),
    ]
    baseline = _formal_baseline(
        tasks=tasks,
        result=_result(
            ScheduledTask(
                id="1",
                name="Inspecionar",
                start_h=0,
                finish_h=2,
                duration_h=2,
                resources={},
            )
        ),
        scope_risks=[
            PlanningScopeRisk(
                task_id="7",
                trigger_task_id="1",
                event_name="damage",
                probability=0.2,
            )
        ],
    )

    report = validate_planning_baseline_for_execution(baseline)

    assert report.ready is False
    assert any("7 → 5" in error for error in report.errors)


def test_gate_checks_resources_of_dormant_potential_scope():
    tasks = [
        Task(id="1", name="Inspecionar", duration_h=2),
        Task(
            id="7",
            name="Trocar feixe",
            duration_h=4,
            resources={"Guindaste": 2},
        ),
    ]
    baseline = _formal_baseline(
        tasks=tasks,
        capacities={"Guindaste": 1},
        result=_result(
            ScheduledTask(
                id="1",
                name="Inspecionar",
                start_h=0,
                finish_h=2,
                duration_h=2,
                resources={},
            )
        ),
        scope_risks=[
            PlanningScopeRisk(
                task_id="7",
                trigger_task_id="1",
                event_name="bundle_damage",
                probability=0.2,
            )
        ],
    )

    report = validate_planning_baseline_for_execution(baseline)

    assert report.ready is False
    assert any(
        "Guindaste exige 2 > 1" in error
        for error in report.errors
    )


def test_gate_catches_cycle_that_exists_only_in_potential_scope():
    tasks = [
        Task(id="1", name="Inspecionar", duration_h=2),
        Task(id="7", name="Opção A", duration_h=2, predecessors=[Link("8")]),
        Task(id="8", name="Opção B", duration_h=2, predecessors=[Link("7")]),
    ]
    baseline = _formal_baseline(
        tasks=tasks,
        result=_result(
            ScheduledTask(
                id="1",
                name="Inspecionar",
                start_h=0,
                finish_h=2,
                duration_h=2,
                resources={},
            )
        ),
        scope_risks=[
            PlanningScopeRisk(
                task_id="7",
                trigger_task_id="1",
                event_name="a",
                probability=0.1,
            ),
            PlanningScopeRisk(
                task_id="8",
                trigger_task_id="1",
                event_name="b",
                probability=0.1,
            ),
        ],
    )

    report = validate_planning_baseline_for_execution(baseline)

    assert report.ready is False
    assert any("ciclo de precedência" in error for error in report.errors)


def test_gate_catches_persisted_schedule_capacity_overload():
    tasks = [
        Task(id="1", name="A", duration_h=4, resources={"Equipe": 1}),
        Task(id="2", name="B", duration_h=4, resources={"Equipe": 1}),
    ]
    baseline = _formal_baseline(
        tasks=tasks,
        capacities={"Equipe": 1},
        result=_result(
            ScheduledTask(
                id="1",
                name="A",
                start_h=0,
                finish_h=4,
                duration_h=4,
                resources={"Equipe": 1},
            ),
            ScheduledTask(
                id="2",
                name="B",
                start_h=0,
                finish_h=4,
                duration_h=4,
                resources={"Equipe": 1},
            ),
        ),
    )

    report = validate_planning_baseline_for_execution(baseline)

    assert report.ready is False
    assert any("uso 2 > 1" in error for error in report.errors)


def test_execution_side_can_repair_only_legacy_dangling_link():
    tasks = [
        Task(
            id="7",
            name="Atividade legado",
            duration_h=4,
            predecessors=[Link("5")],
        )
    ]
    baseline = _formal_baseline(
        tasks=tasks,
        result=_result(
            ScheduledTask(
                id="7",
                name="Atividade legado",
                start_h=0,
                finish_h=4,
                duration_h=4,
                resources={},
            )
        ),
    )

    blocked = validate_planning_baseline_for_execution(baseline)
    repaired = validate_planning_baseline_for_execution(
        baseline,
        allow_dangling_repair=True,
        require_formal_approval=True,
    )

    assert blocked.ready is False
    assert repaired.ready is True
    assert any("7 → 5" in warning for warning in repaired.warnings)


def test_execution_side_requires_formal_governance():
    tasks = [Task(id="1", name="A", duration_h=2)]
    baseline = build_planning_baseline(
        project_name="Parada",
        source_name="parada.xml",
        hours_per_day=8,
        deadline_h=8,
        capacities={},
        capacities_validated=True,
        tasks=tasks,
        result=_result(
            ScheduledTask(
                id="1",
                name="A",
                start_h=0,
                finish_h=2,
                duration_h=2,
                resources={},
            )
        ),
    )

    report = validate_planning_baseline_for_execution(
        baseline,
        require_formal_approval=True,
    )

    assert report.ready is False
    assert any("governança formal" in error for error in report.errors)


def test_gate_rejects_schedule_outside_approved_resource_calendar():
    tasks = [
        Task(
            id="1",
            name="Serviço",
            duration_h=4,
            resources={"Equipe": 1},
        )
    ]
    baseline = build_planning_baseline(
        project_name="Parada calendário",
        source_name="parada.xml",
        hours_per_day=24,
        deadline_h=48,
        capacities={"Equipe": 1},
        capacity_origins={"Equipe": "PROJECT"},
        capacities_validated=True,
        tasks=tasks,
        result=_result(
            ScheduledTask(
                id="1",
                name="Serviço",
                start_h=0,
                finish_h=4,
                duration_h=4,
                resources={"Equipe": 1},
            )
        ),
        resource_calendars={
            "Equipe": WorkingCalendar(
                name="Equipe",
                shifts=(DailyShift(7, 19),),
                origin_hour=6,
            )
        },
        scenario_name="Baseline 0",
        approved_by="Planejamento",
        approval_reason="Liberado.",
    )

    report = validate_planning_baseline_for_execution(
        baseline,
        require_formal_approval=True,
    )

    assert report.ready is False
    assert any(
        "fora da janela de trabalho" in error
        for error in report.errors
    )
