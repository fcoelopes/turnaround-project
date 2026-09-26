from __future__ import annotations

from turnaround import (
    ExecutionStore,
    approved_schedule_matches_project,
    approved_schedule_to_advanced,
    build_planning_baseline,
    project_from_tasks,
    upgrade_database,
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
        tasks=tasks,
        result=result,
        risk={
            "p80_h": 11.0,
            "probability_meet_deadline": 0.85,
        },
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
