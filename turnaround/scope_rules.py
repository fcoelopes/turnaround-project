from __future__ import annotations

from copy import deepcopy
from typing import Literal

from pydantic import BaseModel, Field, model_validator

from .advanced_models import (
    ActivationRule,
    LogicalGroup,
    TriggerCondition,
    TurnaroundProject,
)

ScopeRuleType = Literal["conditional", "xor", "or", "and"]


class ScopeRuleRow(BaseModel):
    """Linha persistida do editor tabular de regras de escopo."""

    id: str
    enabled: bool = True
    rule_type: ScopeRuleType = "conditional"
    target_task_ref: str | None = None
    trigger_task_ref: str | None = None
    events: list[str] = Field(default_factory=list)
    event_logic: Literal["any", "all"] = "any"
    member_task_refs: list[str] = Field(default_factory=list)
    resolution_mode: Literal["human", "event"] = "human"
    event_routes: dict[str, list[str]] = Field(default_factory=dict)
    notes: str | None = None

    @model_validator(mode="after")
    def validate_rule(self):
        self.id = self.id.strip()
        if not self.id:
            raise ValueError("Regra exige ID")

        self.events = [
            event.strip()
            for event in self.events
            if event and event.strip()
        ]
        self.member_task_refs = [
            ref.strip()
            for ref in self.member_task_refs
            if ref and ref.strip()
        ]

        if len(self.member_task_refs) != len(set(self.member_task_refs)):
            raise ValueError(f"Regra {self.id}: membros duplicados")

        if not self.enabled:
            return self

        if self.rule_type == "conditional":
            if not self.target_task_ref:
                raise ValueError(
                    f"Regra {self.id}: conditional exige atividade alvo"
                )
            if not self.trigger_task_ref:
                raise ValueError(
                    f"Regra {self.id}: conditional exige atividade gatilho"
                )
            if not self.events:
                raise ValueError(
                    f"Regra {self.id}: conditional exige ao menos um evento"
                )
            if self.event_routes:
                raise ValueError(
                    f"Regra {self.id}: conditional não usa rotas de evento"
                )
            return self

        if len(self.member_task_refs) < 2:
            raise ValueError(
                f"Regra {self.id}: grupo {self.rule_type.upper()} exige ao menos dois membros"
            )

        if (self.trigger_task_ref is None) != (not self.events):
            raise ValueError(
                f"Regra {self.id}: gatilho e eventos devem ser informados juntos"
            )

        if self.resolution_mode == "event":
            if self.rule_type == "and":
                raise ValueError(
                    f"Regra {self.id}: grupo AND não usa resolution_mode=event"
                )
            if not self.trigger_task_ref or not self.events:
                raise ValueError(
                    f"Regra {self.id}: resolução event exige gatilho e eventos"
                )
            if not self.event_routes:
                raise ValueError(
                    f"Regra {self.id}: resolução event exige rotas de evento"
                )
        elif self.event_routes:
            raise ValueError(
                f"Regra {self.id}: rotas de evento só podem ser usadas com resolução event"
            )

        return self


def task_reference(task) -> str:
    if task.project_uid is not None:
        return f"uid:{task.project_uid}"
    return f"id:{task.id}"


def task_reference_catalog(project: TurnaroundProject) -> dict[str, str]:
    return {
        task_reference(task): (
            f"{task_reference(task)} · ID {task.id} · {task.name}"
        )
        for task in project.tasks
    }


def resolve_task_reference(
    project: TurnaroundProject,
    reference: str,
    *,
    context: str,
) -> str:
    ref = str(reference).strip()
    task_by_id = {task.id: task for task in project.tasks}
    uid_to_id = {
        str(task.project_uid): task.id
        for task in project.tasks
        if task.project_uid is not None
    }

    if ref.startswith("uid:"):
        uid = ref[4:]
        if uid not in uid_to_id:
            raise ValueError(f"{context}: UID desconhecido {uid}")
        return uid_to_id[uid]

    if ref.startswith("id:"):
        task_id = ref[3:]
        if task_id not in task_by_id:
            raise ValueError(f"{context}: ID desconhecido {task_id}")
        return task_id

    # Compatibilidade prática: referência nua tenta ID primeiro e UID depois.
    if ref in task_by_id:
        return ref
    if ref in uid_to_id:
        return uid_to_id[ref]

    raise ValueError(f"{context}: referência de atividade desconhecida {ref}")


def apply_scope_rule_rows(
    project: TurnaroundProject,
    rows: list[ScopeRuleRow],
) -> TurnaroundProject:
    """Aplica regras tabulares como overlay sem destruir regras do sidecar."""

    enabled = [row for row in rows if row.enabled]
    ids = [row.id for row in enabled]
    if len(ids) != len(set(ids)):
        duplicates = sorted(
            rule_id
            for rule_id in set(ids)
            if ids.count(rule_id) > 1
        )
        raise ValueError(
            "IDs de regras da planilha devem ser únicos: "
            + ", ".join(duplicates)
        )

    existing_group_ids = {group.id for group in project.logical_groups}
    collisions = {
        row.id
        for row in enabled
        if row.rule_type != "conditional"
        and row.id in existing_group_ids
    }
    if collisions:
        raise ValueError(
            "IDs de grupos da planilha já existem no sidecar: "
            + ", ".join(sorted(collisions))
        )

    task_by_id = {
        task.id: task.model_copy(deep=True)
        for task in project.tasks
    }

    conditional_targets: dict[str, str] = {}
    groups: list[LogicalGroup] = [
        group.model_copy(deep=True)
        for group in project.logical_groups
    ]

    for row in enabled:
        if row.rule_type == "conditional":
            assert row.target_task_ref is not None
            assert row.trigger_task_ref is not None

            target_id = resolve_task_reference(
                project,
                row.target_task_ref,
                context=f"Regra {row.id}.atividade_alvo",
            )
            source_id = resolve_task_reference(
                project,
                row.trigger_task_ref,
                context=f"Regra {row.id}.gatilho",
            )

            previous_rule = conditional_targets.get(target_id)
            if previous_rule is not None:
                raise ValueError(
                    f"Atividade {target_id} recebeu duas regras conditional "
                    f"na planilha: {previous_rule} e {row.id}"
                )
            conditional_targets[target_id] = row.id

            task_by_id[target_id] = task_by_id[target_id].model_copy(
                update={
                    "activation": ActivationRule(
                        kind="conditional",
                        conditions=[
                            TriggerCondition(
                                source_task_id=source_id,
                                events=list(row.events),
                                event_logic=row.event_logic,
                                require_completed=True,
                            )
                        ],
                        condition_logic="all",
                    )
                },
                deep=True,
            )
            continue

        member_ids = [
            resolve_task_reference(
                project,
                ref,
                context=f"Regra {row.id}.membros",
            )
            for ref in row.member_task_refs
        ]

        # Um grupo lógico deve controlar seus membros. Transformá-los em optional
        # evita que a ativação-base mandatory vença a semântica XOR/OR/AND.
        for member_id in member_ids:
            task_by_id[member_id] = task_by_id[member_id].model_copy(
                update={"activation": ActivationRule(kind="optional")},
                deep=True,
            )

        when = None
        if row.trigger_task_ref is not None:
            source_id = resolve_task_reference(
                project,
                row.trigger_task_ref,
                context=f"Regra {row.id}.gatilho",
            )
            when = TriggerCondition(
                source_task_id=source_id,
                events=list(row.events),
                event_logic=row.event_logic,
                require_completed=True,
            )

        event_routes: dict[str, list[str]] = {}
        for event_name, refs in row.event_routes.items():
            event_routes[event_name] = [
                resolve_task_reference(
                    project,
                    ref,
                    context=f"Regra {row.id}.rota[{event_name}]",
                )
                for ref in refs
            ]

        groups.append(
            LogicalGroup(
                id=row.id,
                operator=row.rule_type,
                member_task_ids=member_ids,
                when=when,
                resolution_mode=row.resolution_mode,
                event_routes=event_routes,
            )
        )

    return TurnaroundProject(
        tasks=[
            task_by_id[task.id]
            for task in project.tasks
        ],
        capacities=deepcopy(project.capacities),
        deadline=project.deadline,
        logical_groups=groups,
    )
