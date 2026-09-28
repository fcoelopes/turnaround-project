from __future__ import annotations

import pytest

from turnaround.models import Task
from turnaround.rcpsp import serial_schedule_generation

from turnaround import (
    CalendarBlock,
    DailyShift,
    ExecutionMode,
    ExecutionState,
    OvertimeWindow,
    TurnaroundProject,
    TurnaroundTask,
    WorkingCalendar,
    reschedule_from_state,
    solve_mrcpsp,
)


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


def test_mrcpsp_respects_distinct_resource_calendars():
    tasks = [
        TurnaroundTask(
            id="A",
            name="Içamento",
            modes=[
                ExecutionMode(
                    name="base",
                    duration=4,
                    resources={"Guindaste": 1},
                )
            ],
        ),
        TurnaroundTask(
            id="B",
            name="Inspeção",
            modes=[
                ExecutionMode(
                    name="base",
                    duration=4,
                    resources={"Inspeção": 1},
                )
            ],
        ),
    ]
    result = solve_mrcpsp(
        tasks,
        capacities={"Guindaste": 1, "Inspeção": 1},
        resource_calendars={
            "Guindaste": WorkingCalendar(
                name="Guindaste",
                shifts=(DailyShift(6, 18),),
            ),
            "Inspeção": WorkingCalendar(
                name="Inspeção",
                shifts=(DailyShift(8, 17),),
            ),
        },
    )
    by_id = {item.task_id: item for item in result.tasks}

    assert by_id["A"].start == pytest.approx(6)
    assert by_id["A"].finish == pytest.approx(10)
    assert by_id["B"].start == pytest.approx(8)
    assert by_id["B"].finish == pytest.approx(12)


def test_mrcpsp_uses_common_window_for_all_resources_of_activity():
    task = TurnaroundTask(
        id="A",
        name="Içamento com equipe",
        modes=[
            ExecutionMode(
                name="base",
                duration=4,
                resources={"Guindaste": 1, "Equipe": 1},
            )
        ],
    )

    result = solve_mrcpsp(
        [task],
        capacities={"Guindaste": 1, "Equipe": 1},
        resource_calendars={
            "Guindaste": WorkingCalendar(
                name="Guindaste",
                shifts=(DailyShift(6, 18),),
            ),
            "Equipe": WorkingCalendar(
                name="Equipe",
                shifts=(DailyShift(7, 19),),
            ),
        },
    )

    assert result.tasks[0].start == pytest.approx(7)
    assert result.tasks[0].finish == pytest.approx(11)


def test_reschedule_moves_work_to_next_resource_shift():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Serviço mecânico",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Equipe": 1},
                    )
                ],
            )
        ],
        capacities={"Equipe": 1},
        resource_calendars={
            "Equipe": WorkingCalendar(
                name="Equipe",
                shifts=(DailyShift(7, 19),),
            )
        },
    )

    result = reschedule_from_state(
        project,
        ExecutionState(current_time=17),
    )

    assert result.schedule.tasks[0].start == pytest.approx(31)
    assert result.schedule.tasks[0].finish == pytest.approx(35)


def test_project_rejects_calendar_for_unknown_resource():
    with pytest.raises(ValueError, match="recursos desconhecidos"):
        TurnaroundProject(
            tasks=[
                TurnaroundTask(
                    id="A",
                    name="Atividade",
                    modes=[ExecutionMode(name="base", duration=1)],
                )
            ],
            capacities={},
            resource_calendars={
                "Fantasma": WorkingCalendar(
                    name="Fantasma",
                    shifts=(DailyShift(7, 19),),
                )
            },
        )


def test_absolute_unavailability_splits_working_window():
    calendar = WorkingCalendar(
        name="Guindaste",
        shifts=(DailyShift(6, 18),),
        blocks=(
            CalendarBlock(
                start_h=30,
                end_h=36,
                reason="manutenção corretiva",
            ),
        ),
    )

    intervals = calendar.working_intervals(24, 48)

    assert intervals == [(36.0, 42.0)]
    assert calendar.is_working_interval(30, 2) is False
    assert calendar.next_working_start(30, 4) == pytest.approx(36)


def test_unavailability_inside_shift_forces_start_after_block():
    calendar = WorkingCalendar(
        name="Equipe",
        shifts=(DailyShift(7, 19),),
        blocks=(CalendarBlock(start_h=10, end_h=12),),
    )

    assert calendar.next_working_start(9, 4) == pytest.approx(12)
    assert calendar.is_working_interval(8, 2) is True
    assert calendar.is_working_interval(9, 2) is False


def test_full_shift_unavailability_moves_to_next_day():
    calendar = WorkingCalendar(
        name="Guindaste",
        shifts=(DailyShift(7, 19),),
        blocks=(CalendarBlock(start_h=31, end_h=43),),
    )

    assert calendar.next_working_start(31, 4) == pytest.approx(55)


def test_mrcpsp_avoids_resource_unavailability_block():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Içamento",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Guindaste": 1},
                    )
                ],
            )
        ],
        capacities={"Guindaste": 1},
        resource_calendars={
            "Guindaste": WorkingCalendar(
                name="Guindaste",
                shifts=(DailyShift(6, 18),),
                blocks=(CalendarBlock(start_h=6, end_h=12),),
            )
        },
    )

    result = reschedule_from_state(
        project,
        ExecutionState(current_time=6),
    )

    assert result.schedule.tasks[0].start == pytest.approx(12)
    assert result.schedule.tasks[0].finish == pytest.approx(16)


def test_calendar_block_validation_is_explicit():
    with pytest.raises(ValueError):
        CalendarBlock(start_h=-1, end_h=2)
    with pytest.raises(ValueError):
        CalendarBlock(start_h=4, end_h=4)
    with pytest.raises(ValueError):
        CalendarBlock(start_h=5, end_h=4)


def test_overtime_extends_regular_shift_continuously():
    calendar = WorkingCalendar(
        name="Equipe",
        shifts=(DailyShift(7, 19),),
        overtime_windows=(
            OvertimeWindow(
                start_h=19,
                end_h=23,
                reason="janela extraordinária aprovada",
            ),
        ),
    )

    assert calendar.is_working_interval(17, 4) is True
    assert calendar.next_working_start(17, 4) == pytest.approx(17)


def test_isolated_overtime_window_can_enable_work_outside_regular_shift():
    calendar = WorkingCalendar(
        name="Guindaste",
        shifts=(DailyShift(6, 18),),
        overtime_windows=(OvertimeWindow(start_h=22, end_h=26),),
    )

    assert calendar.is_working_interval(22, 3) is True
    assert calendar.next_working_start(21, 3) == pytest.approx(22)


def test_unavailability_block_overrides_overtime_window():
    calendar = WorkingCalendar(
        name="Equipe",
        shifts=(DailyShift(7, 19),),
        overtime_windows=(OvertimeWindow(start_h=19, end_h=23),),
        blocks=(CalendarBlock(start_h=20, end_h=21),),
    )

    assert calendar.is_working_interval(19, 3) is False
    assert calendar.next_working_start(19, 3) == pytest.approx(31)


def test_mrcpsp_can_use_approved_overtime_instead_of_waiting_next_shift():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="A",
                name="Serviço crítico",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=4,
                        resources={"Equipe": 1},
                    )
                ],
            )
        ],
        capacities={"Equipe": 1},
        resource_calendars={
            "Equipe": WorkingCalendar(
                name="Equipe",
                shifts=(DailyShift(7, 19),),
                overtime_windows=(
                    OvertimeWindow(
                        start_h=19,
                        end_h=23,
                        reason="janela aprovada",
                    ),
                ),
            )
        },
    )

    result = reschedule_from_state(
        project,
        ExecutionState(current_time=17),
    )

    assert result.schedule.tasks[0].start == pytest.approx(17)
    assert result.schedule.tasks[0].finish == pytest.approx(21)


def test_overtime_window_validation_is_explicit():
    with pytest.raises(ValueError):
        OvertimeWindow(start_h=-1, end_h=2)
    with pytest.raises(ValueError):
        OvertimeWindow(start_h=4, end_h=4)
    with pytest.raises(ValueError):
        OvertimeWindow(start_h=5, end_h=4)


def test_calendar_origin_aligns_clock_time_to_h_plus_axis():
    calendar = WorkingCalendar(
        name="Equipe",
        shifts=(DailyShift(7, 19),),
        origin_hour=6,
    )

    # H+0 = 06:00; turno 07:00-19:00 começa em H+1.
    assert calendar.next_working_start(0, 4) == pytest.approx(1)
    assert calendar.is_working_interval(1, 4) is True
    assert calendar.is_working_interval(0, 1) is False


def test_rcpsp_respects_resource_calendar_and_common_window():
    legacy_task = Task(
        id="A",
        name="Içamento com equipe",
        duration_h=4,
        resources={"Guindaste": 1, "Equipe": 1},
    )
    result = serial_schedule_generation(
        [legacy_task],
        capacities={"Guindaste": 1, "Equipe": 1},
        resource_calendars={
            "Guindaste": WorkingCalendar(
                name="Guindaste",
                shifts=(DailyShift(6, 18),),
                origin_hour=6,
            ),
            "Equipe": WorkingCalendar(
                name="Equipe",
                shifts=(DailyShift(7, 19),),
                origin_hour=6,
            ),
        },
    )

    assert result.schedule[0].start_h == 1
    assert result.schedule[0].finish_h == 5
    assert result.resource_utilization["Equipe"] == pytest.approx(1.0)


def test_rcpsp_rejects_activity_longer_than_resource_work_window():
    legacy_task = Task(
        id="A",
        name="Serviço longo",
        duration_h=13,
        resources={"Equipe": 1},
    )

    with pytest.raises(ValueError, match="não cabe no calendário"):
        serial_schedule_generation(
            [legacy_task],
            capacities={"Equipe": 1},
            resource_calendars={
                "Equipe": WorkingCalendar(
                    name="Equipe",
                    shifts=(DailyShift(7, 19),),
                )
            },
        )
