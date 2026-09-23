from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field, model_validator

ActivationKind = Literal["mandatory", "optional", "conditional"]
ConditionLogic = Literal["any", "all"]
LogicalOperator = Literal["and", "or", "xor"]
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

    @model_validator(mode="after")
    def validate_members(self):
        if len(self.member_task_ids) < 2:
            raise ValueError("Grupo lógico exige pelo menos duas atividades")
        if len(self.member_task_ids) != len(set(self.member_task_ids)):
            raise ValueError("Grupo lógico possui membros duplicados")
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
        known = set(ids)
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
                raise ValueError(f"Grupo {group.id}: atividades desconhecidas {sorted(missing)}")
            if group.when and group.when.source_task_id not in known:
                raise ValueError(f"Grupo {group.id}: trigger desconhecido {group.when.source_task_id}")
        bad_caps = {k: v for k, v in self.capacities.items() if v < 0}
        if bad_caps:
            raise ValueError(f"Capacidades devem ser >= 0: {bad_caps}")
        return self
