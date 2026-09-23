from __future__ import annotations

from dataclasses import dataclass, field
from typing import Dict, List, Optional


@dataclass(frozen=True)
class Link:
    predecessor_id: str
    relation: str = "FS"  # FS, SS, FF, SF
    lag_h: int = 0


@dataclass
class Task:
    id: str
    name: str
    duration_h: int
    predecessors: List[Link] = field(default_factory=list)
    resources: Dict[str, int] = field(default_factory=dict)
    baseline_start: Optional[str] = None
    baseline_finish: Optional[str] = None
    wbs: Optional[str] = None


@dataclass
class ScheduledTask:
    id: str
    name: str
    start_h: int
    finish_h: int
    duration_h: int
    resources: Dict[str, int]
    wbs: Optional[str] = None


@dataclass
class TurnaroundResult:
    schedule: List[ScheduledTask]
    makespan_h: int
    resource_peak: Dict[str, int]
    resource_utilization: Dict[str, float]
    tardiness_h: int
    priority_rule: str
