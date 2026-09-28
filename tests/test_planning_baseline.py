from __future__ import annotations

import pytest

from turnaround import (
    ApprovedPlanningBaseline,
    DailyShift,
    ExecutionStore,
    approved_schedule_matches_project,
    approved_schedule_to_advanced,
    build_planning_baseline,
    project_from_tasks,
    upgrade_database,
    WorkingCalendar,
)
from turnaround.models import Link, ScheduledTask, Task, TurnaroundResult


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'planning-baseline.db'}"


def _sample_plan():
    tasks = [
        Task(
            id="1",
            name="Preparar",
            duration_h=4,
            resources={"Mecânica": 2},
            baseline_start="2026-10-06 06:00",
            baseline_finish="2026-10-06 10:00",
            wbs="1.1",
        ),
        Task(
            id="2",
            name="Executar",
            duration_h=6,
            predecessors=[Link("1", "FS", 0)],
            resources={"Mecânica": 2},
            baseline_start="2026-10-06 10:00",
            baseline_finish="2026-10-06 16:00",
            wbs="1.2",
        ),
    ]
    result = TurnaroundResult(
        schedule=[
            ScheduledTask(
                id="1",
                name="Preparar",
                start_h=0,
                finish_h=4,
                duration_h=4,
                resources={"Mecânica": 2},
                wbs="1.1",
            ),
            ScheduledTask(
                id="2",
                name="Executar",
                start_h=4,
                finish_h=10,
                duration_h=6,
                resources={"Mecânica": 2},
                wbs="1.2",
            ),
        ],
        makespan_h=10,
        resource_peak={"Mecânica": 2},
        resource_utilization={"Mecânica": 1.0},
        tardiness_h=0,
        priority_rule="minimum_float",
    )
    return tasks, result


def test_approved_planning_baseline_survives_store_restart(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    tasks, result = _sample_plan()

    baseline = build_planning_baseline(
        project_name="Parada teste",
        source_name="parada.xml",
        hours_per_day=8,
        deadline_h=12,
        capacities={"Mecânica": 2},
        capacity_origins={"Mecânica": "PROJECT"},
        capacities_validated=True,
        tasks=tasks,
        result=result,
        risk={
            "p80_h": 11.0,
            "probability_meet_deadline": 0.85,
        },
        risk_assumptions={
            "simulations": 300,
            "optimistic_pct": -10,
            "most_likely_pct": 0,
            "pessimistic_pct": 30,
        },
        scenario_name="Congelamento mecânico",
        approved_by="Planejamento",
        approval_reason="Cenário validado para execução.",
    )

    first = ExecutionStore(url)
    saved = first.save_planning_baseline(baseline)

    second = ExecutionStore(url)
    restored = second.load_planning_baseline(saved.key)
    listed = second.list_planning_baselines()

    assert restored is not None
    assert restored.key == saved.key
    assert restored.project_name == "Parada teste"
    assert restored.capacities == {"Mecânica": 2.0}
    assert restored.capacity_origins == {"Mecânica": "PROJECT"}
    assert restored.capacities_validated is True
    assert restored.scenario_name == "Congelamento mecânico"
    assert restored.approved_by == "Planejamento"
    assert restored.approval_reason == "Cenário validado para execução."
    assert restored.risk_assumptions is not None
    assert restored.risk_assumptions.simulations == 300
    assert restored.risk_assumptions.optimistic_pct == -10
    assert restored.reference_start_times() == {"1": 0.0, "2": 4.0}
    assert [task.id for task in restored.to_tasks()] == ["1", "2"]
    assert len(listed) == 1
    assert listed[0].key == saved.key


def test_approved_schedule_becomes_advanced_execution_reference(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    tasks, result = _sample_plan()

    baseline = build_planning_baseline(
        project_name="Parada teste",
        source_name="parada.xml",
        hours_per_day=8,
        deadline_h=12,
        capacities={"Mecânica": 2},
        tasks=tasks,
        result=result,
    )
    project = project_from_tasks(
        baseline.to_tasks(),
        baseline.capacities,
        deadline=baseline.deadline_h,
    )
    active_ids = {task.id for task in project.tasks}

    assert approved_schedule_matches_project(
        baseline,
        project,
        active_ids,
    )

    advanced = approved_schedule_to_advanced(
        baseline,
        project,
        active_ids,
    )

    assert advanced.makespan == 10.0
    assert advanced.strategy == "approved-rcpsp:minimum_float"
    assert [(item.task_id, item.start, item.finish) for item in advanced.tasks] == [
        ("1", 0.0, 4.0),
        ("2", 4.0, 10.0),
    ]


def test_legacy_planning_baseline_without_governance_fields_remains_readable():
    tasks, result = _sample_plan()
    baseline = build_planning_baseline(
        project_name="Parada legado",
        source_name="legado.xml",
        hours_per_day=8,
        deadline_h=12,
        capacities={"Mecânica": 2},
        tasks=tasks,
        result=result,
    )

    legacy_payload = baseline.model_dump(mode="json")
    legacy_payload.pop("scenario_name", None)
    legacy_payload.pop("approved_by", None)
    legacy_payload.pop("approval_reason", None)
    legacy_payload.pop("risk_assumptions", None)
    legacy_payload.pop("capacity_origins", None)
    legacy_payload.pop("capacities_validated", None)

    restored = ApprovedPlanningBaseline.model_validate(legacy_payload)

    assert restored.scenario_name is None
    assert restored.approved_by is None
    assert restored.approval_reason is None
    assert restored.risk_assumptions is None
    assert restored.capacity_origins == {}
    assert restored.capacities_validated is False


def test_approved_baseline_can_normalize_legacy_dangling_predecessor():
    tasks = [
        Task(
            id="7",
            name="Atividade potencial",
            duration_h=4,
            predecessors=[Link("5", "FS", 0)],
        )
    ]
    result = TurnaroundResult(
        schedule=[
            ScheduledTask(
                id="7",
                name="Atividade potencial",
                start_h=0,
                finish_h=4,
                duration_h=4,
                resources={},
            )
        ],
        makespan_h=4,
        resource_peak={},
        resource_utilization={},
        tardiness_h=0,
        priority_rule="minimum_float",
    )
    baseline = build_planning_baseline(
        project_name="Parada legado",
        source_name="legado.xml",
        hours_per_day=8,
        deadline_h=8,
        capacities={},
        tasks=tasks,
        result=result,
    )

    assert baseline.dangling_predecessor_links() == [("7", "5")]

    normalized = baseline.to_tasks(drop_dangling_predecessors=True)
    assert normalized[0].predecessors == []

    project = project_from_tasks(
        normalized,
        baseline.capacities,
        deadline=baseline.deadline_h,
    )
    assert [task.id for task in project.tasks] == ["7"]


def test_planning_baseline_persists_resource_calendar_without_schema_change(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    tasks, result = _sample_plan()
    calendar = WorkingCalendar(
        name="Mecânica",
        shifts=(DailyShift(7, 19),),
        origin_hour=6,
    )

    baseline = build_planning_baseline(
        project_name="Parada calendário",
        source_name="parada.xml",
        hours_per_day=24,
        deadline_h=48,
        capacities={"Mecânica": 2},
        capacities_validated=True,
        tasks=tasks,
        result=result,
        resource_calendars={"Mecânica": calendar},
    )

    stored = ExecutionStore(url).save_planning_baseline(baseline)
    restored = ExecutionStore(url).load_planning_baseline(stored.key)

    assert restored is not None
    restored_calendar = restored.to_resource_calendars()["Mecânica"]
    assert restored_calendar.origin_hour == pytest.approx(6)
    assert restored_calendar.shifts[0].start_hour == pytest.approx(7)
    assert restored_calendar.shifts[0].end_hour == pytest.approx(19)


def test_empty_calendar_does_not_change_legacy_baseline_fingerprint():
    tasks, result = _sample_plan()

    legacy_equivalent = build_planning_baseline(
        project_name="Parada teste",
        source_name="parada.xml",
        hours_per_day=8,
        deadline_h=12,
        capacities={"Mecânica": 2},
        tasks=tasks,
        result=result,
    )
    explicit_empty = build_planning_baseline(
        project_name="Parada teste",
        source_name="parada.xml",
        hours_per_day=8,
        deadline_h=12,
        capacities={"Mecânica": 2},
        tasks=tasks,
        result=result,
        resource_calendars={},
    )

    assert explicit_empty.key == legacy_equivalent.key


def test_advanced_project_receives_calendar_from_approved_baseline():
    tasks, result = _sample_plan()
    calendar = WorkingCalendar(
        name="Mecânica",
        shifts=(DailyShift(7, 19),),
        origin_hour=6,
    )
    baseline = build_planning_baseline(
        project_name="Parada teste",
        source_name="parada.xml",
        hours_per_day=24,
        deadline_h=48,
        capacities={"Mecânica": 2},
        tasks=tasks,
        result=result,
        resource_calendars={"Mecânica": calendar},
    )

    project = project_from_tasks(
        baseline.to_tasks(),
        baseline.capacities,
        deadline=baseline.deadline_h,
        resource_calendars=baseline.to_resource_calendars(),
    )

    assert project.resource_calendars["Mecânica"].origin_hour == pytest.approx(6)
