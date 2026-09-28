from __future__ import annotations

import pytest

from turnaround import DailyShift, WorkingCalendar


def test_day_shift_moves_activity_to_next_available_window():
    calendar = WorkingCalendar(
        name="Equipe A",
        shifts=(DailyShift(7, 19),),
    )

    assert calendar.next_working_start(6, 4) == pytest.approx(7)
    assert calendar.next_working_start(8, 4) == pytest.approx(8)

    # Às 17 h restam somente 2 h no turno; uma atividade não preemptiva de
    # 4 h deve esperar o início do turno seguinte.
    assert calendar.next_working_start(17, 4) == pytest.approx(31)


def test_blocked_hours_do_not_count_as_worked_time():
    calendar = WorkingCalendar(
        name="Inspeção",
        shifts=(DailyShift(8, 17),),
    )

    assert calendar.is_working_interval(13, 4) is True
    assert calendar.is_working_interval(15, 4) is False
    assert calendar.next_working_start(15, 4) == pytest.approx(32)


def test_overnight_shift_handles_midnight_without_creating_gap():
    calendar = WorkingCalendar(
        name="Equipe B",
        shifts=(DailyShift(19, 7),),
    )

    # H+0 está dentro do turno iniciado às 19 h do dia anterior.
    assert calendar.is_working_interval(0, 6) is True
    assert calendar.is_working_interval(6, 2) is False

    # Depois do fim às 07 h, a próxima janela começa às 19 h.
    assert calendar.next_working_start(8, 5) == pytest.approx(19)


def test_adjacent_shifts_form_continuous_24_hour_operation():
    calendar = WorkingCalendar(
        name="Operação 24 h",
        shifts=(
            DailyShift(7, 19),
            DailyShift(19, 7),
        ),
    )

    assert calendar.is_working_interval(16, 20) is True
    assert calendar.next_working_start(16, 20) == pytest.approx(16)


def test_calendar_rejects_activity_longer_than_any_contiguous_window():
    calendar = WorkingCalendar(
        name="Equipe A",
        shifts=(DailyShift(7, 19),),
    )

    with pytest.raises(ValueError, match="não possui janela contínua"):
        calendar.next_working_start(0, 13, max_days=7)


def test_shift_validation_is_explicit():
    with pytest.raises(ValueError):
        DailyShift(-1, 7)
    with pytest.raises(ValueError):
        DailyShift(8, 8)
    with pytest.raises(ValueError):
        DailyShift(0, 25)
