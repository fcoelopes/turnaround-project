from __future__ import annotations

from collections.abc import Iterable

from pydantic import BaseModel, Field, model_validator


class Person(BaseModel):
    """Pessoa individual disponível para o MS-RCPSP."""

    id: str
    name: str
    active: bool = True
    skills: list[str] = Field(default_factory=list)
    notes: str | None = None

    @model_validator(mode="after")
    def normalize_person(self):
        self.id = self.id.strip()
        self.name = self.name.strip()
        if not self.id:
            raise ValueError("Pessoa exige ID")
        if not self.name:
            raise ValueError(f"Pessoa {self.id}: informe o nome")

        normalized: list[str] = []
        for raw in self.skills:
            skill = str(raw).strip()
            if skill and skill not in normalized:
                normalized.append(skill)
        self.skills = normalized
        if self.notes is not None:
            self.notes = self.notes.strip() or None
        return self


class WorkforceProfile(BaseModel):
    """Catálogo de habilidades e pessoas usado pelo MS-RCPSP.

    O campo skills explicita quais nomes de recurso do cronograma devem ser
    tratados como habilidades humanas. Isso evita confundir equipamentos
    (Guindaste, Munck, etc.) com competências da equipe.
    """

    enabled: bool = True
    skills: list[str] = Field(default_factory=list)
    people: list[Person] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_profile(self):
        normalized_skills: list[str] = []
        for raw in self.skills:
            skill = str(raw).strip()
            if skill and skill not in normalized_skills:
                normalized_skills.append(skill)

        for person in self.people:
            for skill in person.skills:
                if skill not in normalized_skills:
                    normalized_skills.append(skill)
        self.skills = normalized_skills

        ids = [person.id for person in self.people]
        if len(ids) != len(set(ids)):
            raise ValueError("IDs das pessoas devem ser únicos")
        return self

    @property
    def active_people(self) -> list[Person]:
        return [person for person in self.people if person.active]

    @property
    def active(self) -> bool:
        return self.enabled and bool(self.skills) and bool(self.active_people)


def skill_capacities(profile: WorkforceProfile | None) -> dict[str, float]:
    if profile is None or not profile.enabled:
        return {}
    return {
        skill: float(
            sum(
                1
                for person in profile.active_people
                if skill in person.skills
            )
        )
        for skill in profile.skills
    }


def effective_capacities(
    capacities: dict[str, float],
    profile: WorkforceProfile | None,
) -> dict[str, float]:
    """Substitui capacidade agregada de skills pela cobertura real da equipe."""

    result = {
        str(resource): float(value)
        for resource, value in capacities.items()
    }
    if profile is None or not profile.enabled:
        return result
    result.update(skill_capacities(profile))
    return result


def skill_requirements(
    resources: dict[str, float],
    profile: WorkforceProfile | None,
) -> dict[str, int]:
    """Extrai demandas humanas de um modo sem arredondar pessoas."""

    if profile is None or not profile.enabled:
        return {}

    requirements: dict[str, int] = {}
    known = set(profile.skills)
    for resource, raw in resources.items():
        if resource not in known or raw <= 0:
            continue
        value = float(raw)
        rounded = round(value)
        if abs(value - rounded) > 1e-9:
            raise ValueError(
                f"Habilidade {resource} exige demanda inteira de pessoas; "
                f"recebido {value:g}"
            )
        requirements[resource] = int(rounded)
    return requirements


def assigned_people(
    assignments: dict[str, Iterable[str]] | None,
) -> set[str]:
    result: set[str] = set()
    for values in (assignments or {}).values():
        result.update(str(value) for value in values)
    return result


def assign_people_to_skills(
    requirements: dict[str, int],
    profile: WorkforceProfile | None,
    *,
    busy_person_ids: set[str] | None = None,
) -> dict[str, tuple[str, ...]] | None:
    """Resolve um matching pessoa-vaga de habilidade sem dupla contagem."""

    if not requirements:
        return {}
    if profile is None or not profile.enabled:
        return None

    busy = set(busy_person_ids or set())
    people = [
        person
        for person in profile.active_people
        if person.id not in busy
    ]
    by_id = {person.id: person for person in people}

    slots: list[tuple[str, int]] = []
    for skill, count in requirements.items():
        if count < 0:
            raise ValueError(f"Demanda de {skill} deve ser >= 0")
        for index in range(count):
            slots.append((skill, index))

    candidates: dict[tuple[str, int], list[str]] = {}
    for slot in slots:
        skill, _ = slot
        candidates[slot] = [
            person.id
            for person in people
            if skill in person.skills
        ]
        if not candidates[slot]:
            return None

    ordered_slots = sorted(
        slots,
        key=lambda slot: (len(candidates[slot]), slot[0], slot[1]),
    )
    used: set[str] = set()
    chosen: dict[tuple[str, int], str] = {}

    def search(position: int) -> bool:
        if position >= len(ordered_slots):
            return True
        slot = ordered_slots[position]

        ordered_people = sorted(
            candidates[slot],
            key=lambda person_id: (
                len(by_id[person_id].skills),
                by_id[person_id].name.casefold(),
                person_id,
            ),
        )
        for person_id in ordered_people:
            if person_id in used:
                continue
            used.add(person_id)
            chosen[slot] = person_id
            if search(position + 1):
                return True
            chosen.pop(slot, None)
            used.remove(person_id)
        return False

    if not search(0):
        return None

    result: dict[str, list[str]] = {
        skill: []
        for skill in requirements
    }
    for (skill, _), person_id in chosen.items():
        result[skill].append(person_id)

    return {
        skill: tuple(values)
        for skill, values in result.items()
    }


def workforce_summary(profile: WorkforceProfile | None) -> list[dict[str, object]]:
    if profile is None:
        return []
    capacities = skill_capacities(profile)
    return [
        {
            "skill": skill,
            "qualified_active": int(capacities.get(skill, 0.0)),
            "qualified_total": sum(
                1 for person in profile.people if skill in person.skills
            ),
        }
        for skill in profile.skills
    ]
