from __future__ import annotations

from dataclasses import dataclass

import pytest

from turnaround import (
    ExecutionMode,
    TurnaroundProject,
    TurnaroundTask,
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
