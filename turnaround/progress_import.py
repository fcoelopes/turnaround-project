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
    remaining_duration_h: float | None = None
    remaining_as_of_h: float | None = None


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
    remaining_duration_h: float | None = None
    remaining_as_of_h: float | None = None


@dataclass(frozen=True)
class ProgressImportResult:
    rows: tuple[ReconciledProgressRow, ...]
    warnings: tuple[str, ...]
    source_rows: int

    @property
    def matched_rows(self) -> int:
        return len(self.rows)


@dataclass(frozen=True)
class BatchProgressMerge:
    rows: tuple[ReconciledProgressRow, ...]
    changed_task_ids: tuple[str, ...]
    unchanged_task_ids: tuple[str, ...]
    preserved_task_ids: tuple[str, ...]

    @property
    def changed_rows(self) -> int:
        return len(self.changed_task_ids)

    @property
    def unchanged_rows(self) -> int:
        return len(self.unchanged_task_ids)

    @property
    def preserved_rows(self) -> int:
        return len(self.preserved_task_ids)


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
    "remaining_duration": [
        "remainingduration",
        "remaining duration",
        "remaining duration h",
        "remaining duration (h)",
        "duracao restante",
        "duração restante",
        "duracao restante h",
        "duração restante h",
        "horas restantes",
    ],
    "remaining_as_of": [
        "status date",
        "data de status",
        "data status",
        "as of",
        "progress date",
    ],
    "remaining_as_of_h": [
        "status h",
        "status h+",
        "as of h",
        "as of h+",
        "h+ status",
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


def _parse_remaining_duration(value: object) -> float | None:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return None
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        number = float(value)
    else:
        text = str(value).strip().lower().replace(",", ".")
        if not text:
            return None

        iso = re.fullmatch(
            r"pt(?:(\d+(?:\.\d+)?)h)?(?:(\d+(?:\.\d+)?)m)?(?:(\d+(?:\.\d+)?)s)?",
            text,
            re.I,
        )
        if iso:
            number = (
                float(iso.group(1) or 0.0)
                + float(iso.group(2) or 0.0) / 60.0
                + float(iso.group(3) or 0.0) / 3600.0
            )
        else:
            hour = re.fullmatch(
                r"(\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hora|horas)",
                text,
                re.I,
            )
            minute = re.fullmatch(
                r"(\d+(?:\.\d+)?)\s*(?:m|min|mins|minuto|minutos)",
                text,
                re.I,
            )
            if hour:
                number = float(hour.group(1))
            elif minute:
                number = float(minute.group(1)) / 60.0
            else:
                try:
                    number = float(text)
                except ValueError as exc:
                    raise ValueError(
                        f"Remaining Duration não reconhecida: {value!r}. "
                        "Informe horas, minutos ou duração ISO PT... do Project."
                    ) from exc

    if number < 0:
        raise ValueError("Remaining Duration deve ser >= 0 h")
    return float(number)


def _validate_remaining(
    status: ProgressStatus,
    actual_start_h: float | None,
    remaining_duration_h: float | None,
    remaining_as_of_h: float | None,
    *,
    allow_partial: bool = False,
) -> None:
    if remaining_duration_h is None:
        return
    if status == "completed" and remaining_duration_h > 1e-9:
        raise ValueError(
            "Atividade concluída deve possuir Remaining Duration igual a 0 h."
        )
    if status == "skipped" and remaining_duration_h > 1e-9:
        raise ValueError(
            "Atividade omitida/cancelada deve possuir Remaining Duration igual a 0 h."
        )
    if status == "in_progress":
        if actual_start_h is None and not allow_partial:
            raise ValueError(
                "Remaining Duration de atividade em andamento exige Actual Start."
            )
        if remaining_duration_h <= 1e-9:
            raise ValueError(
                "Atividade em andamento exige Remaining Duration maior que 0 h."
            )
        if (
            remaining_as_of_h is not None
            and remaining_as_of_h < actual_start_h - 1e-9
        ):
            raise ValueError(
                "Data/hora de referência do Remaining Duration não pode anteceder "
                "o Actual Start."
            )


def forecast_remaining_finish(
    project: TurnaroundProject,
    *,
    task_id: str,
    remaining_duration_h: float,
    from_h: float,
    mode_name: str | None = None,
) -> float:
    """Projeta o término do trabalho em andamento sem inventar preempção."""
    remaining = float(remaining_duration_h)
    start = float(from_h)
    if remaining <= 0:
        raise ValueError("Remaining Duration deve ser > 0 h para atividade em andamento.")
    if start < 0:
        raise ValueError("Referência H+ do Remaining Duration deve ser >= 0.")

    task = next((item for item in project.tasks if item.id == task_id), None)
    if task is None:
        raise ValueError(f"Atividade {task_id} não existe no projeto.")

    if mode_name is None:
        mode = task.modes[0]
    else:
        mode = next(
            (item for item in task.modes if item.name == mode_name),
            None,
        )
        if mode is None:
            raise ValueError(
                f"Atividade {task_id}: modo {mode_name!r} não existe."
            )

    for resource, demand in mode.resources.items():
        if float(demand) <= 0:
            continue
        calendar = project.resource_calendars.get(resource)
        if (
            calendar is not None
            and not calendar.is_working_interval(start, remaining)
        ):
            raise ValueError(
                f"Atividade {task_id}: Remaining Duration de {remaining:g} h "
                f"a partir de H+{start:g} não cabe continuamente no calendário "
                f"do recurso {resource}. Ajuste calendário/overtime ou o dado real."
            )
    return start + remaining


def _validate_actuals(
    status: ProgressStatus,
    actual_start_h: float | None,
    actual_finish_h: float | None,
    *,
    allow_partial: bool = False,
) -> None:
    if (
        actual_finish_h is not None
        and actual_start_h is None
        and not allow_partial
    ):
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
    *,
    allow_partial: bool = False,
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
    remaining_col = _pick_column(df.columns, "remaining_duration")
    remaining_as_of_col = _pick_column(df.columns, "remaining_as_of")
    remaining_as_of_h_col = _pick_column(df.columns, "remaining_as_of_h")

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
                allow_partial=allow_partial,
            )
            remaining_duration_h = _parse_remaining_duration(
                row[remaining_col] if remaining_col is not None else None
            )
            remaining_as_of_h = _resolve_actual_h(
                direct_h=(
                    row[remaining_as_of_h_col]
                    if remaining_as_of_h_col is not None
                    else None
                ),
                datetime_value=(
                    row[remaining_as_of_col]
                    if remaining_as_of_col is not None
                    else None
                ),
                calendar_origin=calendar_origin,
                label="Referência do Remaining Duration",
            )
            _validate_remaining(
                status,
                actual_start_h,
                remaining_duration_h,
                remaining_as_of_h,
                allow_partial=allow_partial,
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
                remaining_duration_h=remaining_duration_h,
                remaining_as_of_h=remaining_as_of_h,
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
    *,
    allow_partial: bool = False,
) -> list[ImportedProgressRow]:
    root = ET.fromstring(content)
    status_date_h = (
        _parse_actual_datetime(
            _child_text(root, "StatusDate"),
            calendar_origin,
        )
        if calendar_origin is not None
        else None
    )
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
            allow_partial=allow_partial,
        )
        remaining_duration_h = _parse_remaining_duration(
            _child_text(node, "RemainingDuration")
        )
        _validate_remaining(
            status,
            actual_start_h,
            remaining_duration_h,
            status_date_h,
            allow_partial=allow_partial,
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
                remaining_duration_h=remaining_duration_h,
                remaining_as_of_h=status_date_h,
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
    allow_partial: bool = False,
) -> list[ImportedProgressRow]:
    name = uploaded_file.name.lower()
    content = uploaded_file.getvalue()

    if name.endswith(".xml"):
        return _rows_from_project_xml(
            content,
            calendar_origin=calendar_origin,
            allow_partial=allow_partial,
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
            allow_partial=allow_partial,
        )
    if name.endswith((".xlsx", ".xls")):
        return _rows_from_dataframe(
            pd.read_excel(io.BytesIO(content)),
            calendar_origin=calendar_origin,
            allow_partial=allow_partial,
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
                remaining_duration_h=row.remaining_duration_h,
                remaining_as_of_h=row.remaining_as_of_h,
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


def _stored_progress_row(row: dict) -> ReconciledProgressRow:
    return ReconciledProgressRow(
        task_id=str(row["task_id"]),
        project_uid=(
            None
            if row.get("project_uid") is None
            else str(row.get("project_uid"))
        ),
        task_name=str(row.get("task_name") or row["task_id"]),
        percent_complete=(
            None
            if row.get("percent_complete") is None
            else float(row.get("percent_complete"))
        ),
        status=str(row.get("status") or "not_started"),
        source_reference=str(row.get("source_reference") or "histórico"),
        actual_start_h=(
            None
            if row.get("actual_start_h") is None
            else float(row.get("actual_start_h"))
        ),
        actual_finish_h=(
            None
            if row.get("actual_finish_h") is None
            else float(row.get("actual_finish_h"))
        ),
        remaining_duration_h=(
            None
            if row.get("remaining_duration_h") is None
            else float(row.get("remaining_duration_h"))
        ),
        remaining_as_of_h=(
            None
            if row.get("remaining_as_of_h") is None
            else float(row.get("remaining_as_of_h"))
        ),
    )


def _merge_progress_row(
    previous: ReconciledProgressRow,
    incoming: ReconciledProgressRow,
) -> ReconciledProgressRow:
    terminal = {"completed", "skipped"}
    if previous.status in terminal and incoming.status != previous.status:
        raise ValueError(
            f"Atividade {incoming.task_id}: status não pode regredir de "
            f"{previous.status} para {incoming.status} em atualização em lote."
        )
    if previous.status == "in_progress" and incoming.status == "not_started":
        raise ValueError(
            f"Atividade {incoming.task_id}: status não pode regredir de "
            "in_progress para not_started."
        )

    if (
        previous.percent_complete is not None
        and incoming.percent_complete is not None
        and incoming.percent_complete < previous.percent_complete - 1e-9
    ):
        raise ValueError(
            f"Atividade {incoming.task_id}: % concluído não pode regredir de "
            f"{previous.percent_complete:g}% para "
            f"{incoming.percent_complete:g}%."
        )

    if (
        previous.remaining_as_of_h is not None
        and incoming.remaining_as_of_h is not None
        and incoming.remaining_as_of_h < previous.remaining_as_of_h - 1e-9
    ):
        raise ValueError(
            f"Atividade {incoming.task_id}: referência do Remaining Duration "
            f"H+{incoming.remaining_as_of_h:g} é anterior ao último snapshot "
            f"H+{previous.remaining_as_of_h:g}."
        )

    status_changed = incoming.status != previous.status
    percent = incoming.percent_complete
    if percent is None and not status_changed:
        percent = previous.percent_complete

    actual_start = (
        incoming.actual_start_h
        if incoming.actual_start_h is not None
        else previous.actual_start_h
    )
    actual_finish = (
        incoming.actual_finish_h
        if incoming.actual_finish_h is not None
        else previous.actual_finish_h
    )

    if incoming.status in terminal:
        remaining_duration = 0.0
        remaining_as_of = (
            incoming.remaining_as_of_h
            if incoming.remaining_as_of_h is not None
            else previous.remaining_as_of_h
        )
    else:
        remaining_duration = (
            incoming.remaining_duration_h
            if incoming.remaining_duration_h is not None
            else previous.remaining_duration_h
        )
        remaining_as_of = (
            incoming.remaining_as_of_h
            if incoming.remaining_as_of_h is not None
            else previous.remaining_as_of_h
        )

    _validate_actuals(
        incoming.status,
        actual_start,
        actual_finish,
    )
    _validate_remaining(
        incoming.status,
        actual_start,
        remaining_duration,
        remaining_as_of,
    )

    return ReconciledProgressRow(
        task_id=incoming.task_id,
        project_uid=incoming.project_uid or previous.project_uid,
        task_name=incoming.task_name or previous.task_name,
        percent_complete=percent,
        status=incoming.status,
        source_reference=incoming.source_reference,
        actual_start_h=actual_start,
        actual_finish_h=actual_finish,
        remaining_duration_h=remaining_duration,
        remaining_as_of_h=remaining_as_of,
    )


def merge_progress_snapshot(
    previous_rows: list[dict] | tuple[dict, ...],
    incoming_rows: list[ReconciledProgressRow] | tuple[ReconciledProgressRow, ...],
) -> BatchProgressMerge:
    """Aplica um lote parcial sobre a fotografia operacional anterior.

    Atividades ausentes do novo arquivo são preservadas. Campos temporais
    ausentes na linha nova também preservam o último valor conhecido, desde que
    a transição de status continue coerente.
    """
    previous = [_stored_progress_row(row) for row in previous_rows]
    previous_by_id = {row.task_id: row for row in previous}
    incoming_by_id = {row.task_id: row for row in incoming_rows}
    if len(incoming_by_id) != len(incoming_rows):
        raise ValueError("Lote contém atividade duplicada após conciliação.")

    merged_by_id = dict(previous_by_id)
    changed: list[str] = []
    unchanged: list[str] = []

    for task_id, incoming in incoming_by_id.items():
        old = previous_by_id.get(task_id)
        if old is None:
            _validate_actuals(
                incoming.status,
                incoming.actual_start_h,
                incoming.actual_finish_h,
            )
            _validate_remaining(
                incoming.status,
                incoming.actual_start_h,
                incoming.remaining_duration_h,
                incoming.remaining_as_of_h,
            )
            merged = incoming
            changed.append(task_id)
        else:
            merged = _merge_progress_row(old, incoming)
            if merged == old:
                unchanged.append(task_id)
            else:
                changed.append(task_id)
        merged_by_id[task_id] = merged

    preserved = [
        row.task_id
        for row in previous
        if row.task_id not in incoming_by_id
    ]

    ordered_ids = [row.task_id for row in previous]
    ordered_ids.extend(
        row.task_id
        for row in incoming_rows
        if row.task_id not in previous_by_id
    )

    return BatchProgressMerge(
        rows=tuple(merged_by_id[task_id] for task_id in ordered_ids),
        changed_task_ids=tuple(changed),
        unchanged_task_ids=tuple(unchanged),
        preserved_task_ids=tuple(preserved),
    )
