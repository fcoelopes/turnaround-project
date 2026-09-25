import io

import pytest

from turnaround import (
    ActivationRule,
    DiscoveredTask,
    ExecutionMode,
    ExecutionState,
    LogicalGroup,
    Precedence,
    TaskExecution,
    TriggerCondition,
    TurnaroundProject,
    TurnaroundTask,
    apply_scope_config,
    discover_resource_catalog,
    evaluate_scope_decisions,
    materialize_dynamic_scope,
    next_discovered_task_id,
    project_from_tasks,
    resolve_activation,
    reschedule_from_state,
    solve_mrcpsp,
)
from turnaround.io import project_xml_to_tasks


def task(
    tid,
    name,
    duration,
    resources=None,
    predecessors=None,
    activation=None,
    modes=None,
):
    return TurnaroundTask(
        id=tid,
        name=name,
        modes=modes
        or [
            ExecutionMode(
                name="base",
                duration=duration,
                resources=resources or {},
            )
        ],
        precedences=[
            Precedence(predecessor_id=p)
            for p in (predecessors or [])
        ],
        activation=activation or ActivationRule(kind="mandatory"),
    )


def test_existing_task_domain_adapts_without_changing_semantics():
    xml = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Project xmlns="http://schemas.microsoft.com/project">
      <Tasks>
        <Task><UID>1</UID><ID>1</ID><Name>A</Name><WBS>1</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT2H0M0S</Duration></Task>
        <Task><UID>2</UID><ID>2</ID><Name>B</Name><WBS>2</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT3H0M0S</Duration>
          <PredecessorLink><PredecessorUID>1</PredecessorUID><Type>1</Type><LinkLag>0</LinkLag></PredecessorLink>
        </Task>
      </Tasks>
      <Resources><Resource><UID>1</UID><ID>1</ID><Name>Mec</Name><MaxUnits>1</MaxUnits></Resource></Resources>
      <Assignments>
        <Assignment><TaskUID>1</TaskUID><ResourceUID>1</ResourceUID><Units>1</Units></Assignment>
        <Assignment><TaskUID>2</TaskUID><ResourceUID>1</ResourceUID><Units>1</Units></Assignment>
      </Assignments>
    </Project>'''
    tasks, capacities = project_xml_to_tasks(xml)
    project = project_from_tasks(tasks, capacities)
    activation = resolve_activation(project, ExecutionState())

    assert activation.active_ids == {"1", "2"}
    result = solve_mrcpsp(project.tasks, project.capacities)
    assert result.makespan == pytest.approx(5)


def test_kinder_ovo_can_activate_multiple_findings():
    inspect = task("I", "Inspecionar", 2, {"Insp": 1})
    bearing = task(
        "B",
        "Trocar rolamento",
        4,
        {"Mec": 2},
        ["I"],
        ActivationRule(
            kind="conditional",
            conditions=[
                TriggerCondition(
                    source_task_id="I",
                    events=["bearing_damage"],
                )
            ],
        ),
    )
    seal = task(
        "S",
        "Trocar selo",
        3,
        {"Mec": 1},
        ["I"],
        ActivationRule(
            kind="conditional",
            conditions=[
                TriggerCondition(
                    source_task_id="I",
                    events=["seal_damage"],
                )
            ],
        ),
    )
    shaft = task(
        "X",
        "Reparar eixo",
        6,
        {"Mec": 2},
        ["I"],
        ActivationRule(
            kind="conditional",
            conditions=[
                TriggerCondition(
                    source_task_id="I",
                    events=["shaft_damage"],
                )
            ],
        ),
    )
    project = TurnaroundProject(
        tasks=[inspect, bearing, seal, shaft],
        capacities={"Insp": 1, "Mec": 3},
    )
    state = ExecutionState(
        current_time=2,
        events={"I": ["bearing_damage", "seal_damage"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=2,
                mode_name="base",
            )
        },
    )

    activation = resolve_activation(project, state)
    assert activation.active_ids == {"I", "B", "S"}
    assert "X" in activation.inactive_ids


def test_xor_group_waits_for_choice_and_enforces_single_path():
    inspection = task("I", "Inspecionar", 1)
    minor = task(
        "M",
        "Reparo leve",
        2,
        activation=ActivationRule(kind="optional"),
    )
    major = task(
        "G",
        "Reparo pesado",
        5,
        activation=ActivationRule(kind="optional"),
    )
    group = LogicalGroup(
        id="severity",
        operator="xor",
        member_task_ids=["M", "G"],
        when=TriggerCondition(
            source_task_id="I",
            events=["defect_found"],
        ),
    )
    project = TurnaroundProject(
        tasks=[inspection, minor, major],
        capacities={},
        logical_groups=[group],
    )
    base_state = ExecutionState(
        events={"I": ["defect_found"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=1,
            )
        },
    )

    unresolved = resolve_activation(project, base_state)
    assert unresolved.group_states["severity"] == "pending_selection"
    assert {"M", "G"}.issubset(unresolved.pending_ids)

    chosen = base_state.model_copy(
        update={"group_selections": {"severity": ["G"]}}
    )
    resolved = resolve_activation(project, chosen)
    assert "G" in resolved.active_ids
    assert "M" in resolved.inactive_ids

    invalid = base_state.model_copy(
        update={"group_selections": {"severity": ["M", "G"]}}
    )
    with pytest.raises(ValueError, match="exatamente uma"):
        resolve_activation(project, invalid)


def test_rescheduling_freezes_past_and_selects_faster_mode():
    inspection = task("I", "Inspecionar", 2, {"Insp": 1})
    repair = TurnaroundTask(
        id="R",
        name="Reparo",
        modes=[
            ExecutionMode(
                name="normal",
                duration=6,
                resources={"Mec": 2},
            ),
            ExecutionMode(
                name="reforco",
                duration=3,
                resources={"Mec": 4},
                cost=1000,
            ),
        ],
        precedences=[Precedence(predecessor_id="I")],
        activation=ActivationRule(
            kind="conditional",
            conditions=[
                TriggerCondition(
                    source_task_id="I",
                    events=["crack"],
                )
            ],
        ),
    )
    test = task(
        "T",
        "END pós-reparo",
        2,
        {"Insp": 1},
        ["R"],
        ActivationRule(
            kind="conditional",
            conditions=[
                TriggerCondition(
                    source_task_id="I",
                    events=["crack"],
                )
            ],
        ),
    )
    project = TurnaroundProject(
        tasks=[inspection, repair, test],
        capacities={"Insp": 1, "Mec": 4},
        deadline=8,
    )
    state = ExecutionState(
        current_time=2,
        events={"I": ["crack"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=2,
                mode_name="base",
            )
        },
    )

    result = reschedule_from_state(project, state)
    assert result.frozen_tasks[0].task_id == "I"
    repair_item = next(
        x for x in result.schedule.tasks if x.task_id == "R"
    )
    assert repair_item.start >= 2
    assert repair_item.mode_name == "reforco"
    assert result.schedule.makespan == pytest.approx(7)


def test_optional_predecessor_is_ignored_when_not_selected():
    optional = task(
        "O",
        "Opcional",
        5,
        activation=ActivationRule(kind="optional"),
    )
    mandatory = task(
        "M",
        "Obrigatória",
        2,
        predecessors=["O"],
    )
    project = TurnaroundProject(
        tasks=[optional, mandatory],
        capacities={},
    )
    activation = resolve_activation(project, ExecutionState())
    active = [
        t for t in project.tasks
        if t.id in activation.active_ids
    ]

    result = solve_mrcpsp(active, project.capacities)
    assert [x.task_id for x in result.tasks] == ["M"]
    assert result.makespan == pytest.approx(2)


def test_existing_xml_parser_preserves_ss_lag_and_resources():
    xml = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Project xmlns="http://schemas.microsoft.com/project">
      <Tasks>
        <Task><UID>1</UID><ID>1</ID><Name>A</Name><WBS>1</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT2H0M0S</Duration></Task>
        <Task><UID>2</UID><ID>2</ID><Name>B</Name><WBS>2</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT1H0M0S</Duration>
          <PredecessorLink><PredecessorUID>1</PredecessorUID><Type>3</Type><LinkLag>600</LinkLag></PredecessorLink>
        </Task>
      </Tasks>
      <Resources><Resource><UID>1</UID><ID>1</ID><Name>Mec</Name><MaxUnits>4</MaxUnits></Resource></Resources>
      <Assignments><Assignment><TaskUID>1</TaskUID><ResourceUID>1</ResourceUID><Units>2</Units></Assignment></Assignments>
    </Project>'''

    tasks, capacities = project_xml_to_tasks(xml)
    assert capacities["Mec"] == 4
    assert tasks[0].resources["Mec"] == 2
    assert tasks[1].predecessors[0].relation == "SS"
    assert tasks[1].predecessors[0].lag_h == pytest.approx(1)


def test_conditional_chain_becomes_inactive_if_optional_trigger_is_not_selected():
    optional = task(
        "O",
        "Abrir opcionalmente",
        1,
        activation=ActivationRule(kind="optional"),
    )
    dependent = task(
        "D",
        "Achado dependente",
        2,
        activation=ActivationRule(
            kind="conditional",
            conditions=[
                TriggerCondition(
                    source_task_id="O",
                    events=["found"],
                )
            ],
        ),
    )
    project = TurnaroundProject(
        tasks=[optional, dependent],
        capacities={},
    )

    result = resolve_activation(project, ExecutionState())
    assert result.states["O"].value == "inactive"
    assert result.states["D"].value == "inactive"


def test_scope_config_adds_modes_without_mutating_base_project():
    base = TurnaroundProject(
        tasks=[
            task("I", "Inspect", 1),
            task("R", "Repair", 4, predecessors=["I"]),
        ],
        capacities={"Mec": 4},
    )

    configured = apply_scope_config(
        base,
        {
            "task_overrides": {
                "R": {
                    "activation": {
                        "kind": "conditional",
                        "conditions": [
                            {
                                "source_task_id": "I",
                                "events": ["defect"],
                            }
                        ],
                        "condition_logic": "all",
                    },
                    "modes": [
                        {
                            "name": "normal",
                            "duration": 4,
                            "resources": {"Mec": 2},
                            "cost": 0,
                        },
                        {
                            "name": "ataque",
                            "duration": 2,
                            "resources": {"Mec": 4},
                            "cost": 500,
                        },
                    ],
                }
            }
        },
    )

    assert base.tasks[1].activation.kind == "mandatory"
    assert configured.tasks[1].activation.kind == "conditional"
    assert len(configured.tasks[1].modes) == 2


def test_late_scope_blocks_successor_of_frozen_activity():
    inspection = task("I", "Inspecionar", 2)
    repair = task(
        "R",
        "Reparo descoberto",
        2,
        predecessors=["I"],
        activation=ActivationRule(
            kind="conditional",
            conditions=[
                TriggerCondition(
                    source_task_id="I",
                    events=["defect"],
                )
            ],
        ),
    )
    close = task(
        "C",
        "Fechar equipamento",
        2,
        predecessors=["I", "R"],
    )
    startup = task(
        "S",
        "Teste e partida",
        1,
        predecessors=["C"],
    )
    project = TurnaroundProject(
        tasks=[inspection, repair, close, startup],
        capacities={},
    )
    state = ExecutionState(
        current_time=3,
        events={"I": ["defect"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=2,
                mode_name="base",
            ),
            "C": TaskExecution(
                status="in_progress",
                start=2,
                finish=4,
                mode_name="base",
            ),
        },
    )

    result = reschedule_from_state(project, state)
    repair_item = next(
        item for item in result.schedule.tasks if item.task_id == "R"
    )
    startup_item = next(
        item for item in result.schedule.tasks if item.task_id == "S"
    )

    assert repair_item.start == pytest.approx(3)
    assert repair_item.finish == pytest.approx(5)
    assert startup_item.start >= repair_item.finish
    assert startup_item.start == pytest.approx(5)
    assert result.schedule.makespan == pytest.approx(6)


def test_numeric_task_ids_use_natural_order_for_solver_ties():
    ten = task(
        "10",
        "Task 10",
        1,
        resources={"Mec": 1},
    )
    two = task(
        "2",
        "Task 2",
        1,
        resources={"Mec": 1},
    )
    project = TurnaroundProject(
        tasks=[ten, two],
        capacities={"Mec": 1},
    )

    result = solve_mrcpsp(project.tasks, project.capacities)

    by_start = sorted(result.tasks, key=lambda item: item.start)
    assert [item.task_id for item in by_start] == ["2", "10"]
    assert by_start[0].start == pytest.approx(0)
    assert by_start[1].start == pytest.approx(1)


def test_kinder_ovo_resource_scenario_switches_mode_and_recovers_hours():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tasks, capacities = project_xml_to_tasks(
        (root / "sample_data" / "turnaround_conditional_model.xml").read_bytes()
    )
    project = project_from_tasks(tasks, capacities)
    project = apply_scope_config(
        project,
        root / "sample_data" / "turnaround_conditional_scope.json",
    )

    state = ExecutionState(
        current_time=7,
        events={"4": ["bearing_damage"]},
        executions={
            "1": TaskExecution(status="completed", start=0, finish=1, mode_name="base"),
            "2": TaskExecution(status="completed", start=1, finish=3, mode_name="base"),
            "3": TaskExecution(status="completed", start=3, finish=5, mode_name="base"),
            "4": TaskExecution(status="completed", start=5, finish=7, mode_name="base"),
            "13": TaskExecution(status="completed", start=3, finish=5, mode_name="base"),
        },
    )

    assert project.capacities["Mecânica"] == pytest.approx(4)

    base_result = reschedule_from_state(project, state)
    base_bearing = next(
        item for item in base_result.schedule.tasks if item.task_id == "5"
    )
    assert base_bearing.mode_name == "normal"
    assert base_bearing.duration == pytest.approx(5)
    assert base_result.schedule.makespan == pytest.approx(17)

    scenario = project.model_copy(
        update={
            "capacities": {
                **project.capacities,
                "Mecânica": 5,
            }
        }
    )
    scenario_result = reschedule_from_state(scenario, state)
    scenario_bearing = next(
        item for item in scenario_result.schedule.tasks if item.task_id == "5"
    )
    assert scenario_bearing.mode_name == "reforco"
    assert scenario_bearing.duration == pytest.approx(3)
    assert scenario_result.schedule.makespan == pytest.approx(15)


def test_duplicate_logical_group_ids_are_rejected():
    inspection = task("I", "Inspecionar", 1)
    a = task("A", "Alternativa A", 1, activation=ActivationRule(kind="optional"))
    b = task("B", "Alternativa B", 1, activation=ActivationRule(kind="optional"))
    c = task("C", "Alternativa C", 1, activation=ActivationRule(kind="optional"))

    with pytest.raises(ValueError, match="grupos lógicos devem ser únicos"):
        TurnaroundProject(
            tasks=[inspection, a, b, c],
            capacities={},
            logical_groups=[
                LogicalGroup(
                    id="decision",
                    operator="xor",
                    member_task_ids=["A", "B"],
                ),
                LogicalGroup(
                    id="decision",
                    operator="xor",
                    member_task_ids=["B", "C"],
                ),
            ],
        )


def test_task_cannot_belong_to_two_selective_groups():
    a = task("A", "A", 1, activation=ActivationRule(kind="optional"))
    b = task("B", "B", 1, activation=ActivationRule(kind="optional"))
    c = task("C", "C", 1, activation=ActivationRule(kind="optional"))

    with pytest.raises(ValueError, match="mais de um grupo seletivo"):
        TurnaroundProject(
            tasks=[a, b, c],
            capacities={},
            logical_groups=[
                LogicalGroup(
                    id="xor_1",
                    operator="xor",
                    member_task_ids=["A", "B"],
                ),
                LogicalGroup(
                    id="or_2",
                    operator="or",
                    member_task_ids=["B", "C"],
                ),
            ],
        )


def test_and_group_can_share_member_with_selective_group():
    a = task("A", "A", 1, activation=ActivationRule(kind="optional"))
    b = task("B", "B", 1, activation=ActivationRule(kind="optional"))
    c = task("C", "C", 1, activation=ActivationRule(kind="optional"))

    project = TurnaroundProject(
        tasks=[a, b, c],
        capacities={},
        logical_groups=[
            LogicalGroup(
                id="xor_1",
                operator="xor",
                member_task_ids=["A", "B"],
            ),
            LogicalGroup(
                id="and_1",
                operator="and",
                member_task_ids=["B", "C"],
            ),
        ],
    )
    assert len(project.logical_groups) == 2


def test_project_uid_survives_visual_id_change_and_scope_rules_follow_uid():
    xml_before = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Project xmlns="http://schemas.microsoft.com/project">
      <Tasks>
        <Task><UID>9004</UID><ID>104</ID><Name>Inspecionar P-101</Name><WBS>1</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT2H0M0S</Duration></Task>
        <Task><UID>9009</UID><ID>109</ID><Name>Recuperar impelidor P-101</Name><WBS>2</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT5H0M0S</Duration></Task>
        <Task><UID>9010</UID><ID>110</ID><Name>Substituir impelidor P-101</Name><WBS>3</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT4H0M0S</Duration></Task>
      </Tasks>
    </Project>'''

    xml_after = b'''<?xml version="1.0" encoding="UTF-8"?>
    <Project xmlns="http://schemas.microsoft.com/project">
      <Tasks>
        <Task><UID>9004</UID><ID>204</ID><Name>Inspecionar P-101</Name><WBS>1</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT2H0M0S</Duration></Task>
        <Task><UID>9009</UID><ID>209</ID><Name>Recuperar impelidor P-101</Name><WBS>2</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT5H0M0S</Duration></Task>
        <Task><UID>9010</UID><ID>210</ID><Name>Substituir impelidor P-101</Name><WBS>3</WBS><Summary>0</Summary><Milestone>0</Milestone><Duration>PT4H0M0S</Duration></Task>
      </Tasks>
    </Project>'''

    sidecar = {
        "task_uid_overrides": {
            "9009": {"activation": {"kind": "optional"}},
            "9010": {"activation": {"kind": "optional"}},
        },
        "logical_groups": [
            {
                "id": "P101_impeller_disposition",
                "operator": "xor",
                "member_task_uids": ["9009", "9010"],
                "when": {
                    "source_task_uid": "9004",
                    "events": ["impeller_damage"],
                },
            }
        ],
    }

    tasks_before, caps_before = project_xml_to_tasks(xml_before)
    before = apply_scope_config(
        project_from_tasks(tasks_before, caps_before),
        sidecar,
    )

    tasks_after, caps_after = project_xml_to_tasks(xml_after)
    after = apply_scope_config(
        project_from_tasks(tasks_after, caps_after),
        sidecar,
    )

    assert {task.project_uid for task in before.tasks} == {"9004", "9009", "9010"}
    assert {task.project_uid for task in after.tasks} == {"9004", "9009", "9010"}

    assert before.logical_groups[0].member_task_ids == ["109", "110"]
    assert before.logical_groups[0].when.source_task_id == "104"

    assert after.logical_groups[0].member_task_ids == ["209", "210"]
    assert after.logical_groups[0].when.source_task_id == "204"

    after_by_uid = {task.project_uid: task for task in after.tasks}
    assert after_by_uid["9009"].activation.kind == "optional"
    assert after_by_uid["9010"].activation.kind == "optional"


def test_kinder_ovo_motor_test_can_discover_motor_replacement():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tasks, capacities = project_xml_to_tasks(
        (root / "sample_data" / "turnaround_conditional_model.xml").read_bytes()
    )
    project = project_from_tasks(tasks, capacities)
    project = apply_scope_config(
        project,
        root / "sample_data" / "turnaround_conditional_scope.json",
    )

    by_uid = {task.project_uid: task for task in project.tasks}
    motor_test = by_uid["13"]
    motor_replace = by_uid["14"]

    assert motor_test.name == "Ensaiar motor elétrico P-101"
    assert motor_test.modes[0].duration == pytest.approx(2)
    assert motor_test.modes[0].resources == {"Elétrica": 2.0}

    assert motor_replace.activation.kind == "conditional"
    assert motor_replace.modes[0].duration == pytest.approx(6)
    assert motor_replace.modes[0].resources == {
        "Elétrica": 2.0,
        "Mecânica": 2.0,
        "Guindaste": 1.0,
    }

    baseline_activation = resolve_activation(project, ExecutionState())
    assert motor_test.id in baseline_activation.active_ids
    assert motor_replace.id in baseline_activation.pending_ids

    baseline_tasks = [
        task for task in project.tasks if task.id in baseline_activation.active_ids
    ]
    baseline = solve_mrcpsp(
        baseline_tasks,
        project.capacities,
        deadline=project.deadline,
    )

    executions = {}
    for item in baseline.tasks:
        if item.finish <= 7 + 1e-9:
            executions[item.task_id] = TaskExecution(
                status="completed",
                start=item.start,
                finish=item.finish,
                mode_name=item.mode_name,
            )

    assert motor_test.id in executions

    state = ExecutionState(
        current_time=7,
        events={motor_test.id: ["motor_replacement_required"]},
        executions=executions,
    )
    discovered = reschedule_from_state(project, state)

    assert motor_replace.id in discovered.activation.active_ids

    replacement_item = next(
        item
        for item in discovered.schedule.tasks
        if item.task_id == motor_replace.id
    )
    closing_item = next(
        item
        for item in discovered.schedule.tasks
        if item.task_id == by_uid["11"].id
    )

    assert replacement_item.duration == pytest.approx(6)
    assert replacement_item.resources == {
        "Elétrica": 2.0,
        "Mecânica": 2.0,
        "Guindaste": 1.0,
    }
    assert closing_item.start >= replacement_item.finish


def test_resource_catalog_includes_resources_used_only_by_modes():
    base = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Reparo",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Mecânica": 2},
                    ),
                    ExecutionMode(
                        name="caldeiraria",
                        duration=3,
                        resources={
                            "Mecânica": 1,
                            "Caldeiraria": 2,
                        },
                    ),
                ],
            )
        ],
        capacities={"Mecânica": 3},
    )

    catalog = discover_resource_catalog(base)

    assert set(catalog) == {"Mecânica", "Caldeiraria"}
    assert catalog["Mecânica"].base_capacity == pytest.approx(3)
    assert catalog["Mecânica"].max_demand == pytest.approx(2)
    assert catalog["Caldeiraria"].base_capacity is None
    assert catalog["Caldeiraria"].max_demand == pytest.approx(2)
    assert catalog["Caldeiraria"].task_ids == ("A",)


def test_missing_resource_capacity_is_explicit_and_scenario_can_enable_it():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Reparo de vaso",
                modes=[
                    ExecutionMode(
                        name="caldeiraria",
                        duration=3,
                        resources={"Caldeiraria": 2},
                    )
                ],
            )
        ],
        capacities={},
    )

    catalog = discover_resource_catalog(project)
    assert catalog["Caldeiraria"].base_capacity is None

    with pytest.raises(ValueError, match="não possui modo factível"):
        solve_mrcpsp(project.tasks, project.capacities)

    scenario = project.model_copy(
        update={"capacities": {"Caldeiraria": 2}}
    )
    result = solve_mrcpsp(scenario.tasks, scenario.capacities)
    assert result.makespan == pytest.approx(3)


def test_lazy_human_decision_stays_dormant_until_trigger_occurs():
    inspection = task("I", "Inspecionar", 1)
    repair = task(
        "R",
        "Reparar",
        4,
        activation=ActivationRule(kind="optional"),
    )
    replace = task(
        "S",
        "Substituir",
        2,
        activation=ActivationRule(kind="optional"),
    )
    project = TurnaroundProject(
        tasks=[inspection, repair, replace],
        capacities={},
        logical_groups=[
            LogicalGroup(
                id="disposition",
                operator="xor",
                member_task_ids=["R", "S"],
                resolution_mode="human",
                when=TriggerCondition(
                    source_task_id="I",
                    events=["damage"],
                ),
            )
        ],
    )

    dormant = evaluate_scope_decisions(project, ExecutionState())
    assert dormant.pending_human == []
    assert dormant.auto_resolved == {}

    triggered_state = ExecutionState(
        current_time=1,
        events={"I": ["damage"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=1,
                mode_name="base",
            )
        },
    )
    triggered = evaluate_scope_decisions(project, triggered_state)

    assert len(triggered.pending_human) == 1
    decision = triggered.pending_human[0]
    assert decision.group_id == "disposition"
    assert len(decision.impacts) == 2
    assert {impact.selection for impact in decision.impacts} == {
        ("R",),
        ("S",),
    }
    assert triggered.state.group_selections == {}


def test_scheduler_cannot_auto_select_technical_action():
    with pytest.raises(ValueError):
        LogicalGroup(
            id="disposition",
            operator="xor",
            member_task_ids=["R", "S"],
            resolution_mode="optimize",
        )


def test_human_decision_reports_resource_gap_without_selecting_action():
    inspection = task("I", "Inspecionar", 1)
    repair = task(
        "R",
        "Reparar rotor",
        4,
        resources={"Soldador": 1},
        predecessors=["I"],
        activation=ActivationRule(kind="optional"),
    )
    replace = task(
        "S",
        "Substituir rotor",
        6,
        resources={"Mecânica": 2},
        predecessors=["I"],
        activation=ActivationRule(kind="optional"),
    )
    project = TurnaroundProject(
        tasks=[inspection, repair, replace],
        capacities={"Mecânica": 2},
        logical_groups=[
            LogicalGroup(
                id="disposition",
                operator="xor",
                member_task_ids=["R", "S"],
                resolution_mode="human",
                when=TriggerCondition(
                    source_task_id="I",
                    events=["damage"],
                ),
            )
        ],
    )
    state = ExecutionState(
        current_time=1,
        events={"I": ["damage"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=1,
                mode_name="base",
            )
        },
    )

    decision_result = evaluate_scope_decisions(project, state)

    assert decision_result.auto_resolved == {}
    assert decision_result.state.group_selections == {}
    assert len(decision_result.pending_human) == 1

    decision = decision_result.pending_human[0]
    impacts = {
        impact.selection: impact
        for impact in decision.impacts
    }

    assert impacts[("R",)].feasible is False
    assert impacts[("R",)].resource_gaps == {"Soldador": pytest.approx(1)}
    assert impacts[("S",)].feasible is True
    assert impacts[("S",)].makespan == pytest.approx(7)

def test_event_decision_routes_without_human_selection():
    inspection = task("I", "Inspecionar", 1)
    repair = task(
        "R",
        "Reparar",
        4,
        activation=ActivationRule(kind="optional"),
    )
    replace = task(
        "S",
        "Substituir",
        3,
        activation=ActivationRule(kind="optional"),
    )
    project = TurnaroundProject(
        tasks=[inspection, repair, replace],
        capacities={},
        logical_groups=[
            LogicalGroup(
                id="disposition",
                operator="xor",
                member_task_ids=["R", "S"],
                resolution_mode="event",
                event_routes={
                    "repairable": ["R"],
                    "replacement_required": ["S"],
                },
                when=TriggerCondition(
                    source_task_id="I",
                    events=["repairable", "replacement_required"],
                ),
            )
        ],
    )
    state = ExecutionState(
        current_time=1,
        events={"I": ["replacement_required"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=1,
                mode_name="base",
            )
        },
    )

    decision_result = evaluate_scope_decisions(project, state)

    assert decision_result.pending_human == []
    assert decision_result.auto_resolved == {
        "disposition": ["S"]
    }
    activation = resolve_activation(project, decision_result.state)
    assert "S" in activation.active_ids
    assert "R" in activation.inactive_ids


def test_event_decision_reports_trigger_without_matching_route():
    inspection = task("I", "Inspecionar", 1)
    repair = task(
        "R",
        "Reparar",
        4,
        activation=ActivationRule(kind="optional"),
    )
    replace = task(
        "S",
        "Substituir",
        3,
        activation=ActivationRule(kind="optional"),
    )
    project = TurnaroundProject(
        tasks=[inspection, repair, replace],
        capacities={},
        logical_groups=[
            LogicalGroup(
                id="disposition",
                operator="xor",
                member_task_ids=["R", "S"],
                resolution_mode="event",
                event_routes={"repairable": ["R"]},
                when=TriggerCondition(
                    source_task_id="I",
                    events=["damage"],
                ),
            )
        ],
    )
    state = ExecutionState(
        current_time=1,
        events={"I": ["damage"]},
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=1,
                mode_name="base",
            )
        },
    )

    decision_result = evaluate_scope_decisions(project, state)

    assert decision_result.auto_resolved == {}
    assert decision_result.unresolved_event_groups == ["disposition"]


def test_kinder_ovo_impeller_disposition_remains_human_decision():
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    tasks, capacities = project_xml_to_tasks(
        (root / "sample_data" / "turnaround_conditional_model.xml").read_bytes()
    )
    project = project_from_tasks(tasks, capacities)
    project = apply_scope_config(
        project,
        root / "sample_data" / "turnaround_conditional_scope.json",
    )

    group = next(
        group
        for group in project.logical_groups
        if group.id == "impeller_disposition"
    )
    assert group.resolution_mode == "human"

    state = ExecutionState(
        current_time=7,
        events={"4": ["impeller_damage"]},
        executions={
            "1": TaskExecution(status="completed", start=0, finish=1, mode_name="base"),
            "2": TaskExecution(status="completed", start=1, finish=3, mode_name="base"),
            "3": TaskExecution(status="completed", start=3, finish=5, mode_name="base"),
            "4": TaskExecution(status="completed", start=5, finish=7, mode_name="base"),
            "13": TaskExecution(status="completed", start=3, finish=5, mode_name="base"),
        },
    )

    decisions = evaluate_scope_decisions(project, state)

    assert decisions.auto_resolved == {}
    assert decisions.state.group_selections == {}
    assert len(decisions.pending_human) == 1

    decision = decisions.pending_human[0]
    assert decision.group_id == "impeller_disposition"
    assert decision.applied_selection is None
    assert len(decision.impacts) == 2

    impacts = {
        impact.selection: impact
        for impact in decision.impacts
    }
    assert impacts[("9",)].makespan == pytest.approx(17)
    assert impacts[("10",)].makespan == pytest.approx(16)



def test_stability_aware_solver_prefers_smaller_start_time_changes():
    tasks = [
        task("A", "Atividade A", 4, {"Equipe": 1}),
        task("B", "Atividade B", 1, {"Equipe": 1}),
    ]
    capacities = {"Equipe": 1}
    reference_starts = {"A": 0.0, "B": 4.0}

    legacy = solve_mrcpsp(
        tasks,
        capacities,
        reference_start_times=reference_starts,
        stability_weight=0.0,
    )
    legacy_by_id = {item.task_id: item for item in legacy.tasks}

    # Sem estabilidade, o comportamento histórico prioriza primeiro a atividade
    # curta quando makespan/custo empatam.
    assert legacy_by_id["B"].start == pytest.approx(0)
    assert legacy_by_id["A"].start == pytest.approx(1)
    assert legacy.total_start_deviation == pytest.approx(5)

    stable = solve_mrcpsp(
        tasks,
        capacities,
        reference_start_times=reference_starts,
        stability_weight=1.0,
    )
    stable_by_id = {item.task_id: item for item in stable.tasks}

    assert stable.makespan == pytest.approx(5)
    assert stable_by_id["A"].start == pytest.approx(0)
    assert stable_by_id["B"].start == pytest.approx(4)
    assert stable.total_start_deviation == pytest.approx(0)
    assert stable.max_start_deviation == pytest.approx(0)
    assert stable.stability_compared_tasks == 2
    assert stable.stability_weight == pytest.approx(1)
    assert stable.strategy.endswith("+stability")


def test_stability_metrics_ignore_new_scope_without_reference_start():
    tasks = [
        task("A", "Planejada", 2),
        task("NEW", "Descoberta", 3, predecessors=["A"]),
    ]

    result = solve_mrcpsp(
        tasks,
        capacities={},
        reference_start_times={"A": 0.0},
        stability_weight=1.0,
    )

    assert result.stability_compared_tasks == 1
    assert result.total_start_deviation == pytest.approx(0)
    assert result.max_start_deviation == pytest.approx(0)


def test_dynamic_scope_discovery_injects_unplanned_task_and_successor_gate():
    inspection = task("I", "Inspecionar equipamento", 2)
    close = task("C", "Fechar equipamento", 1, predecessors=["I"])
    base = TurnaroundProject(
        tasks=[inspection, close],
        capacities={"Mecânica": 1},
        deadline=10,
    )

    discovered = DiscoveredTask(
        id="DS-001",
        name="Reparar trinca descoberta",
        discovered_at=2,
        source_task_id="I",
        source_event="crack_detected",
        modes=[
            ExecutionMode(
                name="campo",
                duration=3,
                resources={"Soldador": 1},
            )
        ],
        precedences=[Precedence(predecessor_id="I")],
        successor_task_ids=["C"],
    )

    materialized = materialize_dynamic_scope(base, [discovered])

    # O cronograma-base permanece intacto.
    original_close = next(task for task in base.tasks if task.id == "C")
    assert [p.predecessor_id for p in original_close.precedences] == ["I"]

    effective = materialized.project
    effective_close = next(task for task in effective.tasks if task.id == "C")
    assert {p.predecessor_id for p in effective_close.precedences} == {
        "I",
        "DS-001",
    }

    dynamic = next(task for task in effective.tasks if task.id == "DS-001")
    assert dynamic.activation.kind == "mandatory"
    assert materialized.discovered_ids == {"DS-001"}

    catalog = discover_resource_catalog(effective)
    assert catalog["Soldador"].base_capacity is None
    assert catalog["Soldador"].max_demand == pytest.approx(1)

    scenario = effective.model_copy(
        update={
            "capacities": {
                **effective.capacities,
                "Soldador": 1,
            }
        }
    )
    state = ExecutionState(
        current_time=2,
        executions={
            "I": TaskExecution(
                status="completed",
                start=0,
                finish=2,
                mode_name="base",
            )
        },
    )

    result = reschedule_from_state(
        scenario,
        state,
        reference_start_times={"C": 2.0},
        stability_weight=1.0,
    )
    by_id = {item.task_id: item for item in result.schedule.tasks}

    assert by_id["DS-001"].start == pytest.approx(2)
    assert by_id["DS-001"].finish == pytest.approx(5)
    assert by_id["C"].start >= by_id["DS-001"].finish
    assert result.schedule.makespan == pytest.approx(6)

    # O novo trabalho não tinha início no plano anterior e não entra na métrica
    # de estabilidade; somente C é comparada.
    assert result.schedule.stability_compared_tasks == 1
    assert result.schedule.total_start_deviation == pytest.approx(3)


def test_dynamic_scope_can_chain_multiple_runtime_discoveries():
    base = TurnaroundProject(
        tasks=[
            task("I", "Inspecionar", 1),
            task("C", "Fechar", 1, predecessors=["I"]),
        ],
        capacities={},
    )
    first = DiscoveredTask(
        id="DS-001",
        name="Preparar reparo",
        discovered_at=1,
        modes=[ExecutionMode(name="campo", duration=2)],
        precedences=[Precedence(predecessor_id="I")],
    )
    second = DiscoveredTask(
        id="DS-002",
        name="Executar reparo",
        discovered_at=1,
        modes=[ExecutionMode(name="campo", duration=3)],
        precedences=[Precedence(predecessor_id="DS-001")],
        successor_task_ids=["C"],
    )

    effective = materialize_dynamic_scope(
        base,
        [first, second],
    ).project
    result = reschedule_from_state(
        effective,
        ExecutionState(
            current_time=1,
            executions={
                "I": TaskExecution(
                    status="completed",
                    start=0,
                    finish=1,
                    mode_name="base",
                )
            },
        ),
    )
    by_id = {item.task_id: item for item in result.schedule.tasks}

    assert by_id["DS-001"].start == pytest.approx(1)
    assert by_id["DS-002"].start == pytest.approx(3)
    assert by_id["C"].start == pytest.approx(6)
    assert result.schedule.makespan == pytest.approx(7)


def test_dynamic_scope_rejects_id_collision_and_unknown_reference():
    base = TurnaroundProject(
        tasks=[task("A", "A", 1)],
        capacities={},
    )

    collision = DiscoveredTask(
        id="A",
        name="Colisão",
        discovered_at=1,
        modes=[ExecutionMode(name="campo", duration=1)],
    )
    with pytest.raises(ValueError, match="colidem"):
        materialize_dynamic_scope(base, [collision])

    unknown = DiscoveredTask(
        id="DS-001",
        name="Referência inválida",
        discovered_at=1,
        modes=[ExecutionMode(name="campo", duration=1)],
        successor_task_ids=["ZZZ"],
    )
    with pytest.raises(ValueError, match="referências desconhecidas"):
        materialize_dynamic_scope(base, [unknown])


def test_dynamic_scope_generates_stable_session_ids():
    base = TurnaroundProject(
        tasks=[task("DS-001", "ID já usado no plano", 1)],
        capacities={},
    )
    existing = DiscoveredTask(
        id="DS-002",
        name="Descoberta anterior",
        discovered_at=1,
        modes=[ExecutionMode(name="campo", duration=1)],
    )

    assert next_discovered_task_id(base, [existing]) == "DS-003"
