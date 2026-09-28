from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import datetime
from typing import Iterable, Literal

import pandas as pd

from .advanced_models import TurnaroundProject

ProgressStatus = Literal["not_started", "in_progress", "completed", "skipped"]


@dataclass(frozen=True)
class ImportedProgressRow:
    source_id: str | None
    source_uid: str | None
    name: str | None
    percent_complete: float | None
    status: ProgressStatus
    actual_start_h: float | None = None
    actual_finish_h: float | None = None


@dataclass(frozen=True)
class ReconciledProgressRow:
    task_id: str
    project_uid: str | None
    task_name: str
    percent_complete: float | None
    status: ProgressStatus
    source_reference: str
    actual_start_h: float | None = None
    actual_finish_h: float | None = None


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
    "actual_start": [
        "actual start",
        "inicio real",
        "início real",
        "data inicio real",
        "data início real",
    ],
    "actual_finish": [
        "actual finish",
        "termino real",
        "término real",
        "fim real",
        "data termino real",
        "data término real",
    ],
    "actual_start_h": [
        "actual start h",
        "actual start (h)",
        "inicio real h",
        "início real h",
        "h+ inicio",
        "h+ início",
    ],
    "actual_finish_h": [
        "actual finish h",
        "actual finish (h)",
        "termino real h",
        "término real h",
        "h+ termino",
        "h+ término",
    ],
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


def _parse_h_plus(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text:
        return None
    number = float(text.replace(",", "."))
    if number < 0:
        raise ValueError("H+ real deve ser >= 0")
    return float(number)


def _parse_actual_datetime(
    value: object,
    calendar_origin: datetime | None,
) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    text = str(value).strip()
    if not text or text.upper() in {"NA", "N/A", "NONE"}:
        return None
    if calendar_origin is None:
        raise ValueError(
            "Actual Start/Finish em data/hora exigem origem temporal do projeto; "
            "use colunas H+ quando a origem não estiver disponível."
        )

    actual = pd.Timestamp(pd.to_datetime(text, errors="raise"))
    origin = pd.Timestamp(calendar_origin)

    if actual.tzinfo is not None and origin.tzinfo is not None:
        actual = actual.tz_convert(origin.tzinfo)
    elif actual.tzinfo is not None and origin.tzinfo is None:
        actual = actual.tz_localize(None)
    elif actual.tzinfo is None and origin.tzinfo is not None:
        origin = origin.tz_localize(None)

    value_h = (actual - origin).total_seconds() / 3600.0
    if value_h < -1e-9:
        raise ValueError(
            f"Actual em {actual} ocorre antes da origem da parada {origin}."
        )
    return max(0.0, float(value_h))


def _resolve_actual_h(
    *,
    direct_h: object,
    datetime_value: object,
    calendar_origin: datetime | None,
    label: str,
) -> float | None:
    direct = _parse_h_plus(direct_h)
    derived = _parse_actual_datetime(datetime_value, calendar_origin)
    if direct is not None and derived is not None and abs(direct - derived) > 1 / 60:
        raise ValueError(
            f"{label}: H+ informado ({direct:g}) diverge da data/hora "
            f"convertida ({derived:g}) em mais de 1 minuto."
        )
    return direct if direct is not None else derived


def _validate_actuals(
    status: ProgressStatus,
    actual_start_h: float | None,
    actual_finish_h: float | None,
) -> None:
    if actual_finish_h is not None and actual_start_h is None:
        raise ValueError("Actual Finish exige Actual Start.")
    if (
        actual_start_h is not None
        and actual_finish_h is not None
        and actual_finish_h < actual_start_h - 1e-9
    ):
        raise ValueError("Actual Finish deve ser >= Actual Start.")
    if status == "not_started" and (
        actual_start_h is not None or actual_finish_h is not None
    ):
        raise ValueError("Atividade não iniciada não pode possuir Actual Start/Finish.")
    if status == "in_progress" and actual_finish_h is not None:
        raise ValueError("Atividade em andamento não pode possuir Actual Finish.")
    if status == "completed" and actual_start_h is None and actual_finish_h is not None:
        raise ValueError("Atividade concluída exige Actual Start antes de Actual Finish.")


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


def _rows_from_dataframe(
    df: pd.DataFrame,
    calendar_origin: datetime | None = None,
) -> list[ImportedProgressRow]:
    id_col = _pick_column(df.columns, "id")
    uid_col = _pick_column(df.columns, "uid")
    name_col = _pick_column(df.columns, "name")
    percent_col = _pick_column(df.columns, "percent")
    status_col = _pick_column(df.columns, "status")
    actual_start_col = _pick_column(df.columns, "actual_start")
    actual_finish_col = _pick_column(df.columns, "actual_finish")
    actual_start_h_col = _pick_column(df.columns, "actual_start_h")
    actual_finish_h_col = _pick_column(df.columns, "actual_finish_h")

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
            actual_start_h = _resolve_actual_h(
                direct_h=(
                    row[actual_start_h_col]
                    if actual_start_h_col is not None
                    else None
                ),
                datetime_value=(
                    row[actual_start_col]
                    if actual_start_col is not None
                    else None
                ),
                calendar_origin=calendar_origin,
                label="Actual Start",
            )
            actual_finish_h = _resolve_actual_h(
                direct_h=(
                    row[actual_finish_h_col]
                    if actual_finish_h_col is not None
                    else None
                ),
                datetime_value=(
                    row[actual_finish_col]
                    if actual_finish_col is not None
                    else None
                ),
                calendar_origin=calendar_origin,
                label="Actual Finish",
            )
            _validate_actuals(
                status,
                actual_start_h,
                actual_finish_h,
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(f"Linha {index + 2}: {exc}") from exc

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
                percent_complete=(
                    None if percent is None else float(percent)
                ),
                status=status,
                actual_start_h=actual_start_h,
                actual_finish_h=actual_finish_h,
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


def _rows_from_project_xml(
    content: bytes,
    calendar_origin: datetime | None = None,
) -> list[ImportedProgressRow]:
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
        status = _status_from_percent(percent)
        actual_start_h = _parse_actual_datetime(
            _child_text(node, "ActualStart"),
            calendar_origin,
        )
        actual_finish_h = _parse_actual_datetime(
            _child_text(node, "ActualFinish"),
            calendar_origin,
        )
        _validate_actuals(
            status,
            actual_start_h,
            actual_finish_h,
        )
        rows.append(
            ImportedProgressRow(
                source_id=source_id,
                source_uid=source_uid,
                name=_child_text(node, "Name"),
                percent_complete=percent,
                status=status,
                actual_start_h=actual_start_h,
                actual_finish_h=actual_finish_h,
            )
        )

    if not rows:
        raise ValueError(
            "Nenhuma atividade executável com progresso foi encontrada no XML."
        )
    return rows


def parse_progress_file(
    uploaded_file,
    *,
    calendar_origin: datetime | None = None,
) -> list[ImportedProgressRow]:
    name = uploaded_file.name.lower()
    content = uploaded_file.getvalue()

    if name.endswith(".xml"):
        return _rows_from_project_xml(
            content,
            calendar_origin=calendar_origin,
        )
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
        return _rows_from_dataframe(
            df,
            calendar_origin=calendar_origin,
        )
    if name.endswith((".xlsx", ".xls")):
        return _rows_from_dataframe(
            pd.read_excel(io.BytesIO(content)),
            calendar_origin=calendar_origin,
        )
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
                percent_complete=(
                    None
                    if row.percent_complete is None
                    else float(row.percent_complete)
                ),
                status=row.status,
                source_reference=source_reference,
                actual_start_h=row.actual_start_h,
                actual_finish_h=row.actual_finish_h,
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
