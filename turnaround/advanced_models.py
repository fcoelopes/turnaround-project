from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ActivationKind = Literal["mandatory", "optional", "conditional"]
ConditionLogic = Literal["any", "all"]
LogicalOperator = Literal["and", "or", "xor"]
DecisionResolutionMode = Literal["human", "optimize", "event"]
TaskStatus = Literal["not_started", "in_progress", "completed", "skipped"]
RelationType = Literal["FS", "SS", "FF", "SF"]


class ExecutionMode(BaseModel):
    name: str
    duration: float = Field(gt=0)
    resources: dict[str, float] = Field(default_factory=dict)
    cost: float = Field(default=0.0, ge=0)

    @model_validator(mode="after")
    def validate_resources(self):
        bad = {k: v for k, v in self.resources.items() if v < 0}
        if bad:
            raise ValueError(f"Demandas de recurso devem ser >= 0: {bad}")
        return self


class Precedence(BaseModel):
    predecessor_id: str
    relation: RelationType = "FS"
    lag: float = 0.0


class TriggerCondition(BaseModel):
    source_task_id: str
    events: list[str] = Field(default_factory=list)
    event_logic: ConditionLogic = "any"
    require_completed: bool = True

    @model_validator(mode="after")
    def events_required(self):
        if not self.events:
            raise ValueError("TriggerCondition exige pelo menos um evento")
        return self


class ActivationRule(BaseModel):
    kind: ActivationKind = "mandatory"
    conditions: list[TriggerCondition] = Field(default_factory=list)
    condition_logic: ConditionLogic = "all"

    @model_validator(mode="after")
    def conditional_requires_condition(self):
        if self.kind == "conditional" and not self.conditions:
            raise ValueError("Atividade conditional exige conditions")
        if self.kind != "conditional" and self.conditions:
            raise ValueError("Somente atividades conditional devem possuir conditions")
        return self


class TurnaroundTask(BaseModel):
    id: str
    name: str
    wbs: str | None = None
    project_uid: str | None = None
    modes: list[ExecutionMode]
    precedences: list[Precedence] = Field(default_factory=list)
    activation: ActivationRule = Field(default_factory=ActivationRule)

    @model_validator(mode="after")
    def validate_modes(self):
        if not self.modes:
            raise ValueError(f"Atividade {self.id} exige pelo menos um modo")
        names = [m.name for m in self.modes]
        if len(names) != len(set(names)):
            raise ValueError(f"Atividade {self.id} possui nomes de modo duplicados")
        return self


class LogicalGroup(BaseModel):
    id: str
    operator: LogicalOperator
    member_task_ids: list[str]
    when: TriggerCondition | None = None
    resolution_mode: DecisionResolutionMode = "human"
    event_routes: dict[str, list[str]] = Field(default_factory=dict)

    @model_validator(mode="after")
    def validate_members(self):
        if len(self.member_task_ids) < 2:
            raise ValueError("Grupo lógico exige pelo menos duas atividades")
        if len(self.member_task_ids) != len(set(self.member_task_ids)):
            raise ValueError("Grupo lógico possui membros duplicados")

        members = set(self.member_task_ids)
        for event_name, route in self.event_routes.items():
            if not event_name.strip():
                raise ValueError(f"Grupo {self.id}: event_routes exige evento não vazio")
            if not route:
                raise ValueError(f"Grupo {self.id}: rota do evento {event_name} está vazia")
            unknown = set(route) - members
            if unknown:
                raise ValueError(
                    f"Grupo {self.id}: event_routes contém membros inválidos {sorted(unknown)}"
                )
            if self.operator == "xor" and len(route) != 1:
                raise ValueError(
                    f"Grupo XOR {self.id}: cada event_route deve selecionar exatamente uma atividade"
                )

        if self.resolution_mode == "event":
            if self.when is None:
                raise ValueError(
                    f"Grupo {self.id}: resolution_mode=event exige condição when"
                )
            if not self.event_routes:
                raise ValueError(
                    f"Grupo {self.id}: resolution_mode=event exige event_routes"
                )
        elif self.event_routes:
            raise ValueError(
                f"Grupo {self.id}: event_routes só pode ser usado com resolution_mode=event"
            )

        return self


class TaskExecution(BaseModel):
    status: TaskStatus = "not_started"
    start: float | None = None
    finish: float | None = None
    mode_name: str | None = None

    @model_validator(mode="after")
    def validate_times(self):
        if self.status in {"in_progress", "completed"} and self.start is None:
            raise ValueError(f"status={self.status} exige start")
        if self.status == "in_progress" and self.finish is None:
            raise ValueError("atividade in_progress exige finish previsto")
        if self.status == "completed" and self.finish is None:
            raise ValueError("atividade completed exige finish")
        if self.start is not None and self.finish is not None and self.finish < self.start:
            raise ValueError("finish deve ser >= start")
        return self


class ExecutionState(BaseModel):
    current_time: float = Field(default=0.0, ge=0)
    events: dict[str, list[str]] = Field(default_factory=dict)
    selected_optional_ids: list[str] = Field(default_factory=list)
    group_selections: dict[str, list[str]] = Field(default_factory=dict)
    executions: dict[str, TaskExecution] = Field(default_factory=dict)


class TurnaroundProject(BaseModel):
    tasks: list[TurnaroundTask]
    capacities: dict[str, float]
    deadline: float | None = Field(default=None, gt=0)
    logical_groups: list[LogicalGroup] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_project(self):
        ids = [t.id for t in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("IDs de atividades devem ser únicos")

        project_uids = [
            t.project_uid
            for t in self.tasks
            if t.project_uid is not None
        ]
        if len(project_uids) != len(set(project_uids)):
            raise ValueError("UIDs do Microsoft Project devem ser únicos")

        group_ids = [group.id for group in self.logical_groups]
        if len(group_ids) != len(set(group_ids)):
            duplicates = sorted(
                group_id
                for group_id in set(group_ids)
                if group_ids.count(group_id) > 1
            )
            raise ValueError(
                "IDs de grupos lógicos devem ser únicos: "
                + ", ".join(duplicates)
            )

        known = set(ids)
        selective_membership: dict[str, str] = {}

        for task in self.tasks:
            for p in task.precedences:
                if p.predecessor_id not in known:
                    raise ValueError(
                        f"Atividade {task.id}: predecessora desconhecida {p.predecessor_id}"
                    )
            for c in task.activation.conditions:
                if c.source_task_id not in known:
                    raise ValueError(
                        f"Atividade {task.id}: trigger desconhecido {c.source_task_id}"
                    )

        for group in self.logical_groups:
            missing = set(group.member_task_ids) - known
            if missing:
                raise ValueError(
                    f"Grupo {group.id}: atividades desconhecidas {sorted(missing)}"
                )
            if group.when and group.when.source_task_id not in known:
                raise ValueError(
                    f"Grupo {group.id}: trigger desconhecido {group.when.source_task_id}"
                )

            # XOR/OR são grupos seletivos. Uma mesma atividade em dois grupos
            # seletivos criaria duas regras concorrentes capazes de sobrescrever
            # seu estado de ativação. Rejeitamos essa ambiguidade na carga.
            if group.operator in {"xor", "or"}:
                for task_id in group.member_task_ids:
                    previous = selective_membership.get(task_id)
                    if previous is not None:
                        raise ValueError(
                            f"Atividade {task_id} pertence a mais de um grupo "
                            f"seletivo: {previous} e {group.id}"
                        )
                    selective_membership[task_id] = group.id

        bad_caps = {k: v for k, v in self.capacities.items() if v < 0}
        if bad_caps:
            raise ValueError(f"Capacidades devem ser >= 0: {bad_caps}")
        return self
