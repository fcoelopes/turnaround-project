from __future__ import annotations

from dataclasses import dataclass

from turnaround import ExecutionState, TaskExecution, project_from_tasks, resolve_activation
from turnaround.models import Link, Task
from turnaround.planning_scope_risk import (
    PlanningScopeRisk,
    apply_planning_scope_risks_to_project,
    extract_scope_risk_candidates,
    materialize_planning_scope,
)
from turnaround.risk import simulate_deadline_risk


@dataclass
class _Upload:
    name: str
    content: bytes

    def getvalue(self) -> bytes:
        return self.content


def _tasks() -> list[Task]:
    return [
        Task(id="1", name="Inspecionar", duration_h=2),
        Task(
            id="2",
            name="Reparo potencial",
            duration_h=8,
            predecessors=[Link("1")],
        ),
        Task(
            id="3",
            name="Fechar equipamento",
            duration_h=2,
            predecessors=[Link("2")],
        ),
    ]


def test_materialize_planning_scope_removes_dormant_gate():
    risk = PlanningScopeRisk(
        task_id="2",
        trigger_task_id="1",
        event_name="damage_found",
        probability=0.25,
    )

    base = materialize_planning_scope(
        _tasks(),
        [risk],
        active_scope_task_ids=set(),
    )
    assert [task.id for task in base] == ["1", "3"]
    assert base[1].predecessors == []

    expanded = materialize_planning_scope(
        _tasks(),
        [risk],
        active_scope_task_ids={"2"},
    )
    assert [task.id for task in expanded] == ["1", "2", "3"]
    assert expanded[2].predecessors[0].predecessor_id == "2"


def test_scope_probability_changes_monte_carlo_project_scope():
    always = PlanningScopeRisk(
        task_id="2",
        trigger_task_id="1",
        event_name="damage_found",
        probability=1.0,
    )
    never = always.model_copy(update={"probability": 0.0})

    common = dict(
        tasks=_tasks(),
        capacities={},
        priority_rule="minimum_float",
        deadline_h=20,
        n=80,
        optimistic_factor=0.99,
        most_likely_factor=1.0,
        pessimistic_factor=1.01,
        seed=7,
    )

    without_scope = simulate_deadline_risk(
        **common,
        scope_risks=[never],
    )
    with_scope = simulate_deadline_risk(
        **common,
        scope_risks=[always],
    )

    assert without_scope["probability_any_scope_simulated"] == 0.0
    assert with_scope["probability_any_scope_simulated"] == 1.0
    assert with_scope["mean_scope_impact_h"] > 0
    assert with_scope["p80_h"] > without_scope["p80_h"]
    assert with_scope["task_activation_frequency"]["2"] == 1.0


def test_imported_conditional_columns_seed_planning_scope_risk():
    upload = _Upload(
        name="parada.csv",
        content=(
            "ID,Nome,Duracao_h,Tipo_escopo,Gatilho_ID,"
            "Evento_sugerido,Probabilidade_%\n"
            "1,Inspecionar,2,mandatory,,,\n"
            "2,Reparar,8,conditional,1,damage_found,20\n"
        ).encode("utf-8"),
    )

    risks = extract_scope_risk_candidates(upload)

    assert len(risks) == 1
    assert risks[0].task_id == "2"
    assert risks[0].trigger_task_id == "1"
    assert risks[0].event_name == "damage_found"
    assert risks[0].probability == 0.20


def test_planning_scope_risk_becomes_execution_condition_after_approval():
    risk = PlanningScopeRisk(
        task_id="2",
        trigger_task_id="1",
        event_name="damage_found",
        probability=0.30,
    )
    project = project_from_tasks(_tasks(), {})
    project = apply_planning_scope_risks_to_project(project, [risk])

    before_event = resolve_activation(
        project,
        ExecutionState(current_time=0),
    )
    assert "2" not in before_event.active_ids

    after_event = resolve_activation(
        project,
        ExecutionState(
            current_time=2,
            events={"1": ["damage_found"]},
            executions={
                "1": TaskExecution(
                    status="completed",
                    start=0,
                    finish=2,
                    mode_name="base",
                )
            },
        ),
    )
    assert "2" in after_event.active_ids
