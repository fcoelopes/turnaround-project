from __future__ import annotations

from dataclasses import dataclass
from typing import Literal


StatusTone = Literal["ok", "warn", "danger"]


@dataclass(frozen=True)
class PlanningExecutiveStatus:
    deterministic: str
    risk: str
    overall: str
    tone: StatusTone
    p80: str
    probability: str
    detail: str


def classify_planning_status(
    *,
    makespan_h: float,
    deadline_h: float | None,
    p80_h: float | None,
    probability_meet_deadline: float | None,
    confidence_threshold: float = 0.80,
) -> PlanningExecutiveStatus:
    """Classify planning status without hiding probabilistic exposure.

    The deterministic plan answers whether the base schedule fits the window.
    Risk is considered controlled only when P80 fits the window and the
    simulated probability of meeting the window reaches the configured
    confidence threshold (80% by default).
    """

    if deadline_h is None:
        return PlanningExecutiveStatus(
            deterministic="SEM JANELA",
            risk="NÃO AVALIADO",
            overall="JANELA NÃO DEFINIDA",
            tone="warn",
            p80="SEM REFERÊNCIA",
            probability="SEM REFERÊNCIA",
            detail=(
                "Defina uma janela-alvo para classificar aderência determinística "
                "e exposição probabilística ao prazo."
            ),
        )

    deterministic_inside = float(makespan_h) <= float(deadline_h)
    deterministic = "DENTRO" if deterministic_inside else "FORA"

    if p80_h is None or probability_meet_deadline is None:
        return PlanningExecutiveStatus(
            deterministic=deterministic,
            risk="NÃO AVALIADO",
            overall=(
                "RISCO NÃO AVALIADO"
                if deterministic_inside
                else "FORA DA JANELA"
            ),
            tone="warn" if deterministic_inside else "danger",
            p80="NÃO DISPONÍVEL",
            probability="NÃO DISPONÍVEL",
            detail=(
                "O cenário determinístico foi calculado, mas a simulação de risco "
                "não está disponível para classificar a confiança de prazo."
            ),
        )

    p80_inside = float(p80_h) <= float(deadline_h)
    probability_ok = float(probability_meet_deadline) >= confidence_threshold
    risk_controlled = p80_inside and probability_ok

    if not deterministic_inside:
        overall = "FORA DA JANELA"
        tone: StatusTone = "danger"
    elif not risk_controlled:
        overall = "RISCO DE PRAZO"
        tone = "warn"
    else:
        overall = "DENTRO COM CONFIANÇA P80"
        tone = "ok"

    return PlanningExecutiveStatus(
        deterministic=deterministic,
        risk="CONTROLADO" if risk_controlled else "EXPOSTO",
        overall=overall,
        tone=tone,
        p80="DENTRO" if p80_inside else "FORA",
        probability=(
            f"{float(probability_meet_deadline) * 100:.1f}% "
            f"({'≥' if probability_ok else '<'} "
            f"{confidence_threshold * 100:.0f}%)"
        ),
        detail=(
            f"Critério probabilístico: P80 deve caber na janela e "
            f"P(cumprir janela) deve ser ≥ {confidence_threshold * 100:.0f}%."
        ),
    )
