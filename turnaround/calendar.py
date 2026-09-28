from __future__ import annotations

from dataclasses import dataclass
from math import ceil, floor


@dataclass(frozen=True)
class DailyShift:
    """Janela diária recorrente em horas locais relativas ao dia.

    Exemplos:
    - 07:00-19:00 -> DailyShift(7, 19)
    - 19:00-07:00 -> DailyShift(19, 7), atravessando meia-noite
    - 24 h        -> DailyShift(0, 24)
    """

    start_hour: float
    end_hour: float

    def __post_init__(self) -> None:
        start = float(self.start_hour)
        end = float(self.end_hour)
        if not 0.0 <= start < 24.0:
            raise ValueError("start_hour deve estar em [0, 24)")
        if not 0.0 < end <= 24.0:
            raise ValueError("end_hour deve estar em (0, 24]")
        if abs(start - end) <= 1e-9:
            raise ValueError(
                "Turno com início igual ao fim é ambíguo; use 00:00-24:00 para 24 h."
            )

    @property
    def crosses_midnight(self) -> bool:
        return float(self.end_hour) < float(self.start_hour)

    @property
    def duration_h(self) -> float:
        if self.crosses_midnight:
            return (24.0 - float(self.start_hour)) + float(self.end_hour)
        return float(self.end_hour) - float(self.start_hour)


@dataclass(frozen=True)
class WorkingCalendar:
    """Calendário recorrente formado por uma ou mais janelas diárias."""

    name: str
    shifts: tuple[DailyShift, ...]

    def __post_init__(self) -> None:
        if not self.name.strip():
            raise ValueError("Calendário exige nome")
        if not self.shifts:
            raise ValueError("Calendário exige pelo menos um turno")

    def working_intervals(
        self,
        start_h: float,
        end_h: float,
    ) -> list[tuple[float, float]]:
        """Retorna janelas trabalháveis que intersectam [start_h, end_h]."""
        start = float(start_h)
        end = float(end_h)
        if end < start:
            raise ValueError("end_h deve ser >= start_h")
        if abs(end - start) <= 1e-9:
            return []

        first_day = floor(start / 24.0) - 1
        last_day = ceil(end / 24.0) + 1
        raw: list[tuple[float, float]] = []

        for day in range(first_day, last_day + 1):
            day_start = day * 24.0
            for shift in self.shifts:
                shift_start = day_start + float(shift.start_hour)
                if shift.crosses_midnight:
                    shift_end = (
                        day_start + 24.0 + float(shift.end_hour)
                    )
                else:
                    shift_end = day_start + float(shift.end_hour)

                clipped_start = max(start, shift_start)
                clipped_end = min(end, shift_end)
                if clipped_end > clipped_start + 1e-9:
                    raw.append((clipped_start, clipped_end))

        if not raw:
            return []

        raw.sort()
        merged: list[list[float]] = []
        for interval_start, interval_end in raw:
            if (
                not merged
                or interval_start > merged[-1][1] + 1e-9
            ):
                merged.append([interval_start, interval_end])
                continue
            merged[-1][1] = max(merged[-1][1], interval_end)

        return [
            (interval_start, interval_end)
            for interval_start, interval_end in merged
        ]

    def is_working_interval(
        self,
        start_h: float,
        duration_h: float,
    ) -> bool:
        """Verdadeiro quando toda a execução cabe numa janela contínua."""
        start = float(start_h)
        duration = float(duration_h)
        if duration <= 0:
            raise ValueError("duration_h deve ser > 0")
        finish = start + duration
        return any(
            interval_start <= start + 1e-9
            and interval_end >= finish - 1e-9
            for interval_start, interval_end in self.working_intervals(
                start,
                finish,
            )
        )

    def next_working_start(
        self,
        earliest_h: float,
        duration_h: float,
        *,
        max_days: int = 366,
    ) -> float:
        """Retorna o primeiro início >= earliest_h que comporta toda a duração.

        A busca é deliberadamente não preemptiva nesta primeira versão:
        horas bloqueadas não contam como horas executadas.
        """
        earliest = float(earliest_h)
        duration = float(duration_h)
        if earliest < 0:
            raise ValueError("earliest_h deve ser >= 0")
        if duration <= 0:
            raise ValueError("duration_h deve ser > 0")
        if max_days <= 0:
            raise ValueError("max_days deve ser > 0")

        horizon = earliest + max_days * 24.0
        intervals = self.working_intervals(earliest, horizon)
        for interval_start, interval_end in intervals:
            candidate = max(earliest, interval_start)
            if candidate + duration <= interval_end + 1e-9:
                return candidate

        raise ValueError(
            f"Calendário {self.name!r} não possui janela contínua de "
            f"{duration:g} h no horizonte de {max_days} dia(s)"
        )
