from __future__ import annotations

import pytest

from turnaround import (
    ActivationRule,
    ExecutionMode,
    ExecutionState,
    LogicalGroup,
    ScopeRuleRow,
    TriggerCondition,
    TurnaroundProject,
    TurnaroundTask,
    apply_scope_rule_rows,
    resolve_activation,
)
from turnaround.persistence import ExecutionStore, upgrade_database


def _task(
    task_id: str,
    name: str,
    *,
    uid: str | None = None,
    activation: ActivationRule | None = None,
) -> TurnaroundTask:
    return TurnaroundTask(
        id=task_id,
        project_uid=uid,
        name=name,
        modes=[ExecutionMode(name="base", duration=1)],
        activation=activation or ActivationRule(kind="mandatory"),
    )


def test_spreadsheet_conditional_rule_uses_stable_uid_reference():
    project = TurnaroundProject(
        tasks=[
            _task("4", "Inspecionar", uid="9004"),
            _task("9", "Reparar", uid="9009"),
        ],
        capacities={},
    )
    rows = [
        ScopeRuleRow(
            id="RULE-001",
            rule_type="conditional",
            target_task_ref="uid:9009",
            trigger_task_ref="uid:9004",
            events=["damage"],
        )
    ]

    effective = apply_scope_rule_rows(project, rows)
    repair = next(task for task in effective.tasks if task.id == "9")

    assert repair.activation.kind == "conditional"
    assert repair.activation.conditions[0].source_task_id == "4"
    assert repair.activation.conditions[0].events == ["damage"]

    pending = resolve_activation(effective, ExecutionState())
    assert "9" in pending.pending_ids


def test_spreadsheet_xor_rule_controls_members_and_preserves_sidecar_groups():
    project = TurnaroundProject(
        tasks=[
            _task("4", "Inspecionar", uid="9004"),
            _task("9", "Reparar", uid="9009"),
            _task("10", "Substituir", uid="9010"),
            _task("20", "Outra A", uid="9020"),
            _task("21", "Outra B", uid="9021"),
        ],
        capacities={},
        logical_groups=[
            LogicalGroup(
                id="existing_group",
                operator="xor",
                member_task_ids=["20", "21"],
            )
        ],
    )
    rows = [
        ScopeRuleRow(
            id="impeller_disposition",
            rule_type="xor",
            trigger_task_ref="uid:9004",
            events=["impeller_damage"],
            member_task_refs=["uid:9009", "uid:9010"],
            resolution_mode="human",
        )
    ]

    effective = apply_scope_rule_rows(project, rows)

    assert {group.id for group in effective.logical_groups} == {
        "existing_group",
        "impeller_disposition",
    }
    spreadsheet_group = next(
        group
        for group in effective.logical_groups
        if group.id == "impeller_disposition"
    )
    assert spreadsheet_group.member_task_ids == ["9", "10"]
    assert spreadsheet_group.when is not None
    assert spreadsheet_group.when.source_task_id == "4"

    member_activation = {
        task.id: task.activation.kind
        for task in effective.tasks
        if task.id in {"9", "10"}
    }
    assert member_activation == {"9": "optional", "10": "optional"}


def test_spreadsheet_event_rule_resolves_routes_from_uids():
    project = TurnaroundProject(
        tasks=[
            _task("4", "Ensaio", uid="9004"),
            _task("9", "Reparar", uid="9009"),
            _task("10", "Substituir", uid="9010"),
        ],
        capacities={},
    )
    rows = [
        ScopeRuleRow(
            id="motor_disposition",
            rule_type="xor",
            trigger_task_ref="uid:9004",
            events=["repairable", "replacement_required"],
            member_task_refs=["uid:9009", "uid:9010"],
            resolution_mode="event",
            event_routes={
                "repairable": ["uid:9009"],
                "replacement_required": ["uid:9010"],
            },
        )
    ]

    effective = apply_scope_rule_rows(project, rows)
    group = effective.logical_groups[0]

    assert group.resolution_mode == "event"
    assert group.event_routes == {
        "repairable": ["9"],
        "replacement_required": ["10"],
    }


def test_spreadsheet_rejects_duplicate_conditional_target():
    project = TurnaroundProject(
        tasks=[
            _task("1", "Inspeção", uid="1"),
            _task("2", "Atividade", uid="2"),
        ],
        capacities={},
    )
    rows = [
        ScopeRuleRow(
            id="R1",
            rule_type="conditional",
            target_task_ref="uid:2",
            trigger_task_ref="uid:1",
            events=["a"],
        ),
        ScopeRuleRow(
            id="R2",
            rule_type="conditional",
            target_task_ref="uid:2",
            trigger_task_ref="uid:1",
            events=["b"],
        ),
    ]

    with pytest.raises(ValueError, match="duas regras conditional"):
        apply_scope_rule_rows(project, rows)


def test_spreadsheet_rule_rows_persist_across_store_restart(tmp_path):
    url = f"sqlite:///{tmp_path / 'scope-rules.db'}"
    upgrade_database(url)

    project_key = "baseline-abc"
    rows = [
        ScopeRuleRow(
            id="RULE-001",
            rule_type="conditional",
            target_task_ref="uid:9009",
            trigger_task_ref="uid:9004",
            events=["damage"],
            notes="criada pela planilha",
        ),
        ScopeRuleRow(
            id="RULE-002",
            enabled=False,
            rule_type="xor",
        ),
    ]

    first = ExecutionStore(url)
    first.replace_scope_rules(project_key, rows)

    second = ExecutionStore(url)
    restored = second.load_scope_rules(project_key)

    assert restored == rows


def test_spreadsheet_group_id_cannot_collide_with_sidecar_group():
    project = TurnaroundProject(
        tasks=[
            _task("1", "A", uid="1"),
            _task("2", "B", uid="2"),
        ],
        capacities={},
        logical_groups=[
            LogicalGroup(
                id="same-id",
                operator="xor",
                member_task_ids=["1", "2"],
            )
        ],
    )
    rows = [
        ScopeRuleRow(
            id="same-id",
            rule_type="xor",
            member_task_refs=["uid:1", "uid:2"],
        )
    ]

    with pytest.raises(ValueError, match="já existem no sidecar"):
        apply_scope_rule_rows(project, rows)


def test_spreadsheet_rejects_conditional_target_that_is_also_group_member():
    project = TurnaroundProject(
        tasks=[
            _task("1", "Inspeção", uid="1"),
            _task("2", "Reparar", uid="2"),
            _task("3", "Substituir", uid="3"),
        ],
        capacities={},
    )
    rows = [
        ScopeRuleRow(
            id="conditional-2",
            rule_type="conditional",
            target_task_ref="uid:2",
            trigger_task_ref="uid:1",
            events=["damage"],
        ),
        ScopeRuleRow(
            id="choice",
            rule_type="xor",
            member_task_refs=["uid:2", "uid:3"],
        ),
    ]

    with pytest.raises(ValueError, match="alvo conditional e membro"):
        apply_scope_rule_rows(project, rows)


def test_spreadsheet_event_rule_requires_route_for_every_trigger_event():
    with pytest.raises(ValueError, match="faltam rotas"):
        ScopeRuleRow(
            id="event-choice",
            rule_type="xor",
            trigger_task_ref="uid:1",
            events=["repairable", "replacement_required"],
            member_task_refs=["uid:2", "uid:3"],
            resolution_mode="event",
            event_routes={"repairable": ["uid:2"]},
        )
