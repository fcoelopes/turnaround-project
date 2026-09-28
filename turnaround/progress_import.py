from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from typing import Iterable, Literal

import pandas as pd

from .advanced_models import TurnaroundProject

ProgressStatus = Literal["not_started", "in_progress", "completed", "skipped"]


@dataclass(frozen=True)
class ImportedProgressRow:
    source_id: str | None
    source_uid: str | None
    name: str | None
    percent_complete: float
    status: ProgressStatus


@dataclass(frozen=True)
class ReconciledProgressRow:
    task_id: str
    project_uid: str | None
    task_name: str
    percent_complete: float
    status: ProgressStatus
    source_reference: str


@dataclass(frozen=True)
class ProgressImportResult:
    rows: tuple[ReconciledProgressRow, ...]
    warnings: tuple[str, ...]
    source_rows: int

    @property
    def matched_rows(self) -> int:
        return len(self.rows)


_PROGRESS_ALIASES = {
    "id": ["id", "task id", "atividade id", "identificacao", "identificação"],
    "uid": ["uid", "project uid", "uid project", "microsoft project uid"],
    "name": ["name", "nome", "task name", "atividade", "tarefa"],
    "percent": [
        "percentcomplete",
        "percent complete",
        "% complete",
        "% concluido",
        "% concluído",
        "percentual concluido",
        "percentual concluído",
        "progresso %",
        "progresso",
    ],
    "status": ["status", "estado", "situacao", "situação"],
}


def _norm(value: object) -> str:
    text = str(value).replace("\ufeff", "").strip().lower()
    text = (
        text.replace("á", "a")
        .replace("à", "a")
        .replace("ã", "a")
        .replace("â", "a")
        .replace("é", "e")
        .replace("ê", "e")
        .replace("í", "i")
        .replace("ó", "o")
        .replace("ô", "o")
        .replace("õ", "o")
        .replace("ú", "u")
        .replace("ç", "c")
    )
    return re.sub(r"\s+", " ", text)


def _pick_column(columns: Iterable[object], key: str) -> object | None:
    normalized = {_norm(column): column for column in columns}
    for alias in _PROGRESS_ALIASES[key]:
        candidate = normalized.get(_norm(alias))
        if candidate is not None:
            return candidate
    return None


def _clean_reference(value: object) -> str | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith(".0"):
        text = text[:-2]
    return text


def _percent(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, str):
        raw = value.strip().replace("%", "").replace(",", ".")
        if not raw:
            return None
        number = float(raw)
    else:
        number = float(value)
    if 0.0 <= number <= 1.0 and not isinstance(value, str):
        number *= 100.0
    if not 0.0 <= number <= 100.0:
        raise ValueError(f"Percentual de progresso fora de 0–100: {number:g}")
    return float(number)


def _status_from_percent(percent: float) -> ProgressStatus:
    if percent >= 100.0 - 1e-9:
        return "completed"
    if percent > 0.0:
        return "in_progress"
    return "not_started"


def _normalize_status(value: object, percent: float | None) -> ProgressStatus:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return _status_from_percent(float(percent or 0.0))

    raw = _norm(value).replace("_", " ")
    mapping: dict[str, ProgressStatus] = {
        "not started": "not_started",
        "nao iniciada": "not_started",
        "nao iniciado": "not_started",
        "planned": "not_started",
        "in progress": "in_progress",
        "em andamento": "in_progress",
        "iniciada": "in_progress",
        "iniciado": "in_progress",
        "completed": "completed",
        "complete": "completed",
        "concluida": "completed",
        "concluido": "completed",
        "finalizada": "completed",
        "finalizado": "completed",
        "skipped": "skipped",
        "omitida": "skipped",
        "omitido": "skipped",
        "cancelada": "skipped",
        "cancelado": "skipped",
    }
    status = mapping.get(raw)
    if status is None:
        raise ValueError(f"Status de progresso não reconhecido: {value!r}")

    if percent is not None:
        inferred = _status_from_percent(percent)
        if status == "completed" and percent < 100.0 - 1e-9:
            raise ValueError(
                f"Status concluído exige 100%, mas recebeu {percent:g}%"
            )
        if status == "not_started" and percent > 1e-9:
            raise ValueError(
                f"Status não iniciado exige 0%, mas recebeu {percent:g}%"
            )
        if status == "in_progress" and inferred != "in_progress":
            raise ValueError(
                f"Status em andamento exige progresso entre 0 e 100%, mas recebeu {percent:g}%"
            )
    return status


def _rows_from_dataframe(df: pd.DataFrame) -> list[ImportedProgressRow]:
    id_col = _pick_column(df.columns, "id")
    uid_col = _pick_column(df.columns, "uid")
    name_col = _pick_column(df.columns, "name")
    percent_col = _pick_column(df.columns, "percent")
    status_col = _pick_column(df.columns, "status")

    if id_col is None and uid_col is None:
        raise ValueError(
            "Arquivo de progresso exige coluna ID ou UID Project."
        )
    if percent_col is None and status_col is None:
        raise ValueError(
            "Arquivo de progresso exige % concluído/PercentComplete ou Status."
        )

    rows: list[ImportedProgressRow] = []
    for index, row in df.iterrows():
        source_id = _clean_reference(row[id_col]) if id_col is not None else None
        source_uid = _clean_reference(row[uid_col]) if uid_col is not None else None
        if source_id is None and source_uid is None:
            continue
        try:
            percent = _percent(row[percent_col]) if percent_col is not None else None
            status = _normalize_status(
                row[status_col] if status_col is not None else None,
                percent,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Linha {index + 2}: {exc}") from exc

        if percent is None:
            percent = {
                "not_started": 0.0,
                "in_progress": 50.0,
                "completed": 100.0,
                "skipped": 0.0,
            }[status]

        rows.append(
            ImportedProgressRow(
                source_id=source_id,
                source_uid=source_uid,
                name=(
                    None
                    if name_col is None
                    or pd.isna(row[name_col])
                    else str(row[name_col]).strip() or None
                ),
                percent_complete=float(percent),
                status=status,
            )
        )
    if not rows:
        raise ValueError("Nenhuma linha de progresso utilizável foi encontrada.")
    return rows


def _child_text(node: ET.Element, name: str, default: str | None = None) -> str | None:
    for child in node:
        if child.tag.split("}", 1)[-1] == name:
            return child.text if child.text is not None else default
    return default


def _rows_from_project_xml(content: bytes) -> list[ImportedProgressRow]:
    root = ET.fromstring(content)
    rows: list[ImportedProgressRow] = []
    for node in root.iter():
        if node.tag.split("}", 1)[-1] != "Task":
            continue
        if _child_text(node, "Summary", "0") == "1":
            continue

        source_uid = _clean_reference(_child_text(node, "UID"))
        source_id = _clean_reference(_child_text(node, "ID"))
        if source_uid is None and source_id is None:
            continue

        percent = _percent(_child_text(node, "PercentComplete", "0")) or 0.0
        rows.append(
            ImportedProgressRow(
                source_id=source_id,
                source_uid=source_uid,
                name=_child_text(node, "Name"),
                percent_complete=percent,
                status=_status_from_percent(percent),
            )
        )

    if not rows:
        raise ValueError(
            "Nenhuma atividade executável com progresso foi encontrada no XML."
        )
    return rows


def parse_progress_file(uploaded_file) -> list[ImportedProgressRow]:
    name = uploaded_file.name.lower()
    content = uploaded_file.getvalue()

    if name.endswith(".xml"):
        return _rows_from_project_xml(content)
    if name.endswith(".csv"):
        try:
            df = pd.read_csv(
                io.BytesIO(content),
                sep=None,
                engine="python",
                encoding="utf-8-sig",
            )
        except UnicodeDecodeError:
            df = pd.read_csv(
                io.BytesIO(content),
                sep=None,
                engine="python",
                encoding="latin1",
            )
        return _rows_from_dataframe(df)
    if name.endswith((".xlsx", ".xls")):
        return _rows_from_dataframe(pd.read_excel(io.BytesIO(content)))
    raise ValueError(
        "Formato de progresso não suportado. Envie XML, XLSX/XLS ou CSV."
    )


def reconcile_progress(
    rows: list[ImportedProgressRow],
    project: TurnaroundProject,
) -> ProgressImportResult:
    by_id = {task.id: task for task in project.tasks}
    by_uid = {
        str(task.project_uid): task
        for task in project.tasks
        if task.project_uid is not None
    }

    reconciled: list[ReconciledProgressRow] = []
    warnings: list[str] = []
    seen_task_ids: set[str] = set()

    for row in rows:
        task = None
        source_reference = ""

        if row.source_uid is not None and row.source_uid in by_uid:
            task = by_uid[row.source_uid]
            source_reference = f"UID {row.source_uid}"
        elif row.source_id is not None and row.source_id in by_id:
            task = by_id[row.source_id]
            source_reference = f"ID {row.source_id}"

        if task is None:
            reference = (
                f"UID {row.source_uid}"
                if row.source_uid is not None
                else f"ID {row.source_id}"
            )
            warnings.append(
                f"{reference}: atividade não existe no snapshot executável e foi ignorada."
            )
            continue

        if task.id in seen_task_ids:
            raise ValueError(
                f"Atividade {task.id} aparece mais de uma vez no arquivo de progresso."
            )
        seen_task_ids.add(task.id)

        if row.name and _norm(row.name) != _norm(task.name):
            warnings.append(
                f"{source_reference}: nome no arquivo ({row.name}) difere do snapshot "
                f"({task.name}); a conciliação usou a referência estável."
            )

        reconciled.append(
            ReconciledProgressRow(
                task_id=task.id,
                project_uid=task.project_uid,
                task_name=task.name,
                percent_complete=float(row.percent_complete),
                status=row.status,
                source_reference=source_reference,
            )
        )

    if not reconciled:
        raise ValueError(
            "Nenhuma linha do arquivo de progresso corresponde às atividades do projeto."
        )

    return ProgressImportResult(
        rows=tuple(reconciled),
        warnings=tuple(dict.fromkeys(warnings)),
        source_rows=len(rows),
    )
