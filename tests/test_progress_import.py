from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import pytest

from turnaround import (
    ExecutionMode,
    DailyShift,
    TurnaroundProject,
    TurnaroundTask,
    WorkingCalendar,
    forecast_remaining_finish,
    merge_progress_snapshot,
    parse_progress_file,
    reconcile_progress,
)


@dataclass
class _Upload:
    name: str
    content: bytes

    def getvalue(self) -> bytes:
        return self.content


def _project() -> TurnaroundProject:
    return TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="10",
                project_uid="1010",
                name="Abrir equipamento",
                modes=[ExecutionMode(name="base", duration=2)],
            ),
            TurnaroundTask(
                id="20",
                project_uid="2020",
                name="Inspecionar",
                modes=[ExecutionMode(name="base", duration=3)],
            ),
        ],
        capacities={},
    )


def test_csv_progress_reconciles_by_uid_before_id():
    upload = _Upload(
        name="progresso.csv",
        content=(
            "ID,UID Project,Nome,% concluído\n"
            "999,1010,Abrir equipamento,100\n"
            "20,,Inspecionar,40\n"
        ).encode("utf-8"),
    )

    rows = parse_progress_file(upload)
    result = reconcile_progress(rows, _project())

    assert result.source_rows == 2
    assert result.matched_rows == 2
    assert result.rows[0].task_id == "10"
    assert result.rows[0].source_reference == "UID 1010"
    assert result.rows[0].status == "completed"
    assert result.rows[1].task_id == "20"
    assert result.rows[1].status == "in_progress"
    assert result.rows[1].percent_complete == pytest.approx(40.0)


def test_project_xml_progress_reads_percent_complete_and_skips_summary():
    upload = _Upload(
        name="progresso.xml",
        content=b'''<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
  <Tasks>
    <Task>
      <UID>1</UID><ID>1</ID><Name>Resumo</Name>
      <Summary>1</Summary><PercentComplete>50</PercentComplete>
    </Task>
    <Task>
      <UID>1010</UID><ID>10</ID><Name>Abrir equipamento</Name>
      <Summary>0</Summary><PercentComplete>100</PercentComplete>
    </Task>
    <Task>
      <UID>2020</UID><ID>20</ID><Name>Inspecionar</Name>
      <Summary>0</Summary><PercentComplete>25</PercentComplete>
    </Task>
  </Tasks>
</Project>''',
    )

    rows = parse_progress_file(upload)
    result = reconcile_progress(rows, _project())

    assert len(rows) == 2
    assert [row.status for row in result.rows] == [
        "completed",
        "in_progress",
    ]
    assert [row.percent_complete for row in result.rows] == [100.0, 25.0]


def test_status_only_import_does_not_invent_percent_complete():
    upload = _Upload(
        name="status.csv",
        content=(
            "ID,Status\n"
            "10,completed\n"
            "20,in progress\n"
        ).encode("utf-8"),
    )

    result = reconcile_progress(
        parse_progress_file(upload),
        _project(),
    )

    assert result.rows[0].status == "completed"
    assert result.rows[0].percent_complete is None
    assert result.rows[1].status == "in_progress"
    assert result.rows[1].percent_complete is None


def test_progress_import_warns_for_unknown_task_and_name_mismatch():
    upload = _Upload(
        name="progresso.csv",
        content=(
            "ID,Nome,% complete\n"
            "10,Nome divergente,100\n"
            "999,Atividade externa,50\n"
        ).encode("utf-8"),
    )

    result = reconcile_progress(
        parse_progress_file(upload),
        _project(),
    )

    assert result.matched_rows == 1
    assert len(result.warnings) == 2
    assert any("nome no arquivo" in item for item in result.warnings)
    assert any("atividade não existe" in item for item in result.warnings)


def test_progress_import_rejects_duplicate_rows_for_same_task():
    upload = _Upload(
        name="duplicado.csv",
        content=(
            "ID,UID,% complete\n"
            "10,,20\n"
            "999,1010,30\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="aparece mais de uma vez"):
        reconcile_progress(
            parse_progress_file(upload),
            _project(),
        )


def test_progress_import_rejects_inconsistent_status_and_percent():
    upload = _Upload(
        name="inconsistente.csv",
        content=(
            "ID,Status,% complete\n"
            "10,completed,70\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="concluído exige 100"):
        parse_progress_file(upload)


def test_csv_actual_datetimes_are_converted_to_h_plus_from_project_origin():
    upload = _Upload(
        name="actuals.csv",
        content=(
            "ID,% concluído,Actual Start,Actual Finish\n"
            "10,100,2026-10-06 07:30,2026-10-06 09:30\n"
        ).encode("utf-8"),
    )

    result = reconcile_progress(
        parse_progress_file(
            upload,
            calendar_origin=datetime(2026, 10, 6, 6, 0),
        ),
        _project(),
    )

    row = result.rows[0]
    assert row.actual_start_h == pytest.approx(1.5)
    assert row.actual_finish_h == pytest.approx(3.5)


def test_csv_can_import_actuals_directly_in_h_plus_without_calendar_origin():
    upload = _Upload(
        name="actuals_h.csv",
        content=(
            "ID,Status,Actual Start H,Actual Finish H\n"
            "10,completed,2.5,5.0\n"
            "20,in progress,4.0,\n"
        ).encode("utf-8"),
    )

    result = reconcile_progress(
        parse_progress_file(upload),
        _project(),
    )

    assert result.rows[0].actual_start_h == pytest.approx(2.5)
    assert result.rows[0].actual_finish_h == pytest.approx(5.0)
    assert result.rows[1].actual_start_h == pytest.approx(4.0)
    assert result.rows[1].actual_finish_h is None


def test_project_xml_imports_actual_start_and_finish_against_baseline_origin():
    upload = _Upload(
        name="actuals.xml",
        content=b'''<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
  <Tasks>
    <Task>
      <UID>1010</UID><ID>10</ID><Name>Abrir equipamento</Name>
      <Summary>0</Summary><PercentComplete>100</PercentComplete>
      <ActualStart>2026-10-06T07:00:00</ActualStart>
      <ActualFinish>2026-10-06T10:00:00</ActualFinish>
    </Task>
    <Task>
      <UID>2020</UID><ID>20</ID><Name>Inspecionar</Name>
      <Summary>0</Summary><PercentComplete>40</PercentComplete>
      <ActualStart>2026-10-06T10:30:00</ActualStart>
    </Task>
  </Tasks>
</Project>''',
    )

    result = reconcile_progress(
        parse_progress_file(
            upload,
            calendar_origin=datetime(2026, 10, 6, 6, 0),
        ),
        _project(),
    )

    assert result.rows[0].actual_start_h == pytest.approx(1.0)
    assert result.rows[0].actual_finish_h == pytest.approx(4.0)
    assert result.rows[1].actual_start_h == pytest.approx(4.5)
    assert result.rows[1].actual_finish_h is None


def test_absolute_actuals_require_project_origin():
    upload = _Upload(
        name="actuals.csv",
        content=(
            "ID,Status,Actual Start,Actual Finish\n"
            "10,completed,2026-10-06 07:00,2026-10-06 09:00\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="origem temporal"):
        parse_progress_file(upload)


def test_actual_finish_before_start_is_rejected():
    upload = _Upload(
        name="actuals_h.csv",
        content=(
            "ID,Status,Actual Start H,Actual Finish H\n"
            "10,completed,5,4\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="Actual Finish deve ser"):
        parse_progress_file(upload)


def test_not_started_activity_cannot_have_actual_start():
    upload = _Upload(
        name="invalid.csv",
        content=(
            "ID,Status,Actual Start H\n"
            "10,not started,2\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="não iniciada"):
        parse_progress_file(upload)


def test_csv_imports_remaining_duration_and_status_reference_in_h_plus():
    upload = _Upload(
        name="remaining.csv",
        content=(
            "ID,Status,Actual Start H,Remaining Duration,Status H+\n"
            "20,in progress,4,3.5,6\n"
        ).encode("utf-8"),
    )

    result = reconcile_progress(
        parse_progress_file(upload),
        _project(),
    )
    row = result.rows[0]

    assert row.actual_start_h == pytest.approx(4.0)
    assert row.remaining_duration_h == pytest.approx(3.5)
    assert row.remaining_as_of_h == pytest.approx(6.0)


def test_project_xml_imports_remaining_duration_and_status_date():
    upload = _Upload(
        name="remaining.xml",
        content=b'''<?xml version="1.0" encoding="UTF-8"?>
<Project xmlns="http://schemas.microsoft.com/project">
  <StatusDate>2026-10-06T10:00:00</StatusDate>
  <Tasks>
    <Task>
      <UID>2020</UID><ID>20</ID><Name>Inspecionar</Name>
      <Summary>0</Summary><PercentComplete>40</PercentComplete>
      <ActualStart>2026-10-06T08:00:00</ActualStart>
      <RemainingDuration>PT3H30M0S</RemainingDuration>
    </Task>
  </Tasks>
</Project>''',
    )

    result = reconcile_progress(
        parse_progress_file(
            upload,
            calendar_origin=datetime(2026, 10, 6, 6, 0),
        ),
        _project(),
    )
    row = result.rows[0]

    assert row.actual_start_h == pytest.approx(2.0)
    assert row.remaining_as_of_h == pytest.approx(4.0)
    assert row.remaining_duration_h == pytest.approx(3.5)


def test_in_progress_remaining_duration_requires_actual_start():
    upload = _Upload(
        name="remaining.csv",
        content=(
            "ID,Status,Remaining Duration\n"
            "20,in progress,3\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="exige Actual Start"):
        parse_progress_file(upload)


def test_completed_activity_rejects_positive_remaining_duration():
    upload = _Upload(
        name="remaining.csv",
        content=(
            "ID,Status,Actual Start H,Actual Finish H,Remaining Duration\n"
            "10,completed,1,4,2\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="igual a 0"):
        parse_progress_file(upload)


def test_remaining_reference_cannot_precede_actual_start():
    upload = _Upload(
        name="remaining.csv",
        content=(
            "ID,Status,Actual Start H,Remaining Duration,Status H+\n"
            "20,in progress,5,3,4\n"
        ).encode("utf-8"),
    )

    with pytest.raises(ValueError, match="não pode anteceder"):
        parse_progress_file(upload)


def test_remaining_forecast_respects_resource_calendar():
    project = TurnaroundProject(
        tasks=[
            TurnaroundTask(
                id="20",
                project_uid="2020",
                name="Inspecionar",
                modes=[
                    ExecutionMode(
                        name="base",
                        duration=8,
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
                origin_hour=6,
            )
        },
    )

    assert forecast_remaining_finish(
        project,
        task_id="20",
        remaining_duration_h=4,
        from_h=2,
        mode_name="base",
    ) == pytest.approx(6.0)

    with pytest.raises(ValueError, match="não cabe continuamente"):
        forecast_remaining_finish(
            project,
            task_id="20",
            remaining_duration_h=4,
            from_h=10,
            mode_name="base",
        )


def test_batch_merge_preserves_tasks_absent_from_partial_file():
    previous = [
        {
            "task_id": "10",
            "project_uid": "1010",
            "task_name": "Abrir equipamento",
            "percent_complete": 100.0,
            "status": "completed",
            "actual_start_h": 1.0,
            "actual_finish_h": 3.0,
            "remaining_duration_h": 0.0,
            "remaining_as_of_h": 3.0,
            "source_reference": "UID 1010",
        },
        {
            "task_id": "20",
            "project_uid": "2020",
            "task_name": "Inspecionar",
            "percent_complete": 40.0,
            "status": "in_progress",
            "actual_start_h": 3.5,
            "actual_finish_h": None,
            "remaining_duration_h": 3.0,
            "remaining_as_of_h": 4.0,
            "source_reference": "UID 2020",
        },
    ]
    incoming = reconcile_progress(
        parse_progress_file(
            _Upload(
                name="parcial.csv",
                content=(
                    "ID,Status,% concluído,Actual Start H,Remaining Duration,Status H+\n"
                    "20,in progress,60,3.5,2,5\n"
                ).encode("utf-8"),
            )
        ),
        _project(),
    )

    merged = merge_progress_snapshot(previous, incoming.rows)

    assert merged.changed_task_ids == ("20",)
    assert merged.preserved_task_ids == ("10",)
    assert len(merged.rows) == 2
    by_id = {row.task_id: row for row in merged.rows}
    assert by_id["10"].status == "completed"
    assert by_id["10"].actual_finish_h == pytest.approx(3.0)
    assert by_id["20"].percent_complete == pytest.approx(60.0)
    assert by_id["20"].remaining_duration_h == pytest.approx(2.0)
    assert by_id["20"].remaining_as_of_h == pytest.approx(5.0)


def test_partial_patch_can_complete_task_using_previous_actual_start():
    previous = [
        {
            "task_id": "20",
            "project_uid": "2020",
            "task_name": "Inspecionar",
            "percent_complete": 60.0,
            "status": "in_progress",
            "actual_start_h": 3.5,
            "actual_finish_h": None,
            "remaining_duration_h": 2.0,
            "remaining_as_of_h": 5.0,
            "source_reference": "UID 2020",
        }
    ]
    upload = _Upload(
        name="fechamento.csv",
        content=(
            "ID,Status,% concluído,Actual Finish H\n"
            "20,completed,100,7\n"
        ).encode("utf-8"),
    )

    incoming = reconcile_progress(
        parse_progress_file(upload, allow_partial=True),
        _project(),
    )
    merged = merge_progress_snapshot(previous, incoming.rows)
    row = merged.rows[0]

    assert row.status == "completed"
    assert row.actual_start_h == pytest.approx(3.5)
    assert row.actual_finish_h == pytest.approx(7.0)
    assert row.remaining_duration_h == pytest.approx(0.0)


def test_partial_patch_can_update_remaining_without_repeating_actual_start():
    previous = [
        {
            "task_id": "20",
            "project_uid": "2020",
            "task_name": "Inspecionar",
            "percent_complete": 40.0,
            "status": "in_progress",
            "actual_start_h": 3.5,
            "actual_finish_h": None,
            "remaining_duration_h": 3.0,
            "remaining_as_of_h": 4.0,
            "source_reference": "UID 2020",
        }
    ]
    incoming = reconcile_progress(
        parse_progress_file(
            _Upload(
                name="remaining_delta.csv",
                content=(
                    "ID,Status,% concluído,Remaining Duration,Status H+\n"
                    "20,in progress,50,2.25,5\n"
                ).encode("utf-8"),
            ),
            allow_partial=True,
        ),
        _project(),
    )

    merged = merge_progress_snapshot(previous, incoming.rows)
    row = merged.rows[0]

    assert row.actual_start_h == pytest.approx(3.5)
    assert row.remaining_duration_h == pytest.approx(2.25)
    assert row.remaining_as_of_h == pytest.approx(5.0)


def test_batch_merge_blocks_status_and_percent_regression():
    previous = [
        {
            "task_id": "20",
            "project_uid": "2020",
            "task_name": "Inspecionar",
            "percent_complete": 60.0,
            "status": "in_progress",
            "actual_start_h": 3.5,
            "actual_finish_h": None,
            "remaining_duration_h": 2.0,
            "remaining_as_of_h": 5.0,
            "source_reference": "UID 2020",
        }
    ]

    status_regression = reconcile_progress(
        parse_progress_file(
            _Upload(
                name="status.csv",
                content=("ID,Status,% concluído\n20,not started,0\n").encode("utf-8"),
            ),
            allow_partial=True,
        ),
        _project(),
    )
    with pytest.raises(ValueError, match="status não pode regredir"):
        merge_progress_snapshot(previous, status_regression.rows)

    percent_regression = reconcile_progress(
        parse_progress_file(
            _Upload(
                name="percent.csv",
                content=("ID,Status,% concluído\n20,in progress,50\n").encode("utf-8"),
            ),
            allow_partial=True,
        ),
        _project(),
    )
    with pytest.raises(ValueError, match="% concluído não pode regredir"):
        merge_progress_snapshot(previous, percent_regression.rows)


def test_batch_merge_blocks_stale_remaining_snapshot():
    previous = [
        {
            "task_id": "20",
            "project_uid": "2020",
            "task_name": "Inspecionar",
            "percent_complete": 60.0,
            "status": "in_progress",
            "actual_start_h": 3.5,
            "actual_finish_h": None,
            "remaining_duration_h": 2.0,
            "remaining_as_of_h": 6.0,
            "source_reference": "UID 2020",
        }
    ]
    incoming = reconcile_progress(
        parse_progress_file(
            _Upload(
                name="stale.csv",
                content=(
                    "ID,Status,% concluído,Remaining Duration,Status H+\n"
                    "20,in progress,65,1.5,5\n"
                ).encode("utf-8"),
            ),
            allow_partial=True,
        ),
        _project(),
    )

    with pytest.raises(ValueError, match="anterior ao último snapshot"):
        merge_progress_snapshot(previous, incoming.rows)
