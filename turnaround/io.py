from __future__ import annotations

import io
import re
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

import pandas as pd

from .models import Link, Task


ALIASES = {
    "id": ["id", "task id", "identificação", "identificacao", "uid"],
    "name": ["nome", "name", "task name", "nome da tarefa", "tarefa"],
    "duration": ["duracao_h", "duração_h", "duracao", "duração", "duration", "duration_h"],
    "pred": ["predecessoras", "predecessores", "predecessors", "predecessor"],
    "resources": ["recursos", "resource names", "nomes dos recursos", "resources", "recurso"],
    "demands": ["demandas", "resource demand", "resource demands", "demanda_recursos"],
    "start": ["inicio", "início", "start", "baseline start"],
    "finish": ["termino", "término", "conclusão", "finish", "baseline finish"],
    "wbs": ["wbs", "edt"],
}


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", str(s).strip().lower())


def _pick_column(columns: Iterable[str], key: str) -> str | None:
    cmap = {_norm(c): c for c in columns}
    for alias in ALIASES[key]:
        if _norm(alias) in cmap:
            return cmap[_norm(alias)]
    return None


def parse_duration_to_hours(value, hours_per_day: int = 8) -> int:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return 0
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return max(0, int(round(float(value))))

    s = str(value).strip().lower().replace(",", ".")
    if not s:
        return 0

    # ISO 8601 used by MS Project XML, e.g. PT16H0M0S
    m = re.fullmatch(r"pt(?:(\d+)h)?(?:(\d+)m)?(?:(\d+)s)?", s, re.I)
    if m:
        h = int(m.group(1) or 0)
        minutes = int(m.group(2) or 0)
        seconds = int(m.group(3) or 0)
        return max(0, int(round(h + minutes / 60 + seconds / 3600)))

    total = 0.0
    found = False
    patterns = [
        (r"([+-]?\d+(?:\.\d+)?)\s*(?:d|dia|dias|day|days)\b", hours_per_day),
        (r"([+-]?\d+(?:\.\d+)?)\s*(?:h|hr|hrs|hora|horas|hour|hours)\b", 1),
        (r"([+-]?\d+(?:\.\d+)?)\s*(?:m|min|mins|minuto|minutos|minute|minutes)\b", 1 / 60),
    ]
    for pattern, factor in patterns:
        for match in re.finditer(pattern, s):
            total += float(match.group(1)) * factor
            found = True
    if found:
        return max(0, int(round(total)))

    try:
        return max(0, int(round(float(s))))
    except ValueError as exc:
        raise ValueError(f"Duração não reconhecida: {value!r}") from exc


def parse_predecessors(value, hours_per_day: int = 8) -> List[Link]:
    if value is None or (isinstance(value, float) and pd.isna(value)):
        return []
    text = str(value).strip()
    if not text:
        return []

    links: List[Link] = []
    for raw in re.split(r"[;,]", text):
        token = raw.strip().replace(" ", "")
        if not token:
            continue
        m = re.match(r"^(?P<id>[^A-Za-z+\-]+|\d+)(?P<rel>FS|SS|FF|SF)?(?P<lag>[+-].+)?$", token, re.I)
        if not m:
            # Most common Project export shape still starts with task id.
            m2 = re.match(r"^(\d+)", token)
            if not m2:
                continue
            links.append(Link(predecessor_id=m2.group(1), relation="FS", lag_h=0))
            continue
        pred_id = m.group("id").strip()
        rel = (m.group("rel") or "FS").upper()
        lag_text = m.group("lag")
        lag_h = 0
        if lag_text:
            sign = -1 if lag_text.startswith("-") else 1
            lag_h = sign * parse_duration_to_hours(lag_text[1:], hours_per_day)
        links.append(Link(predecessor_id=pred_id, relation=rel, lag_h=lag_h))
    return links


def parse_resources(names_value, demands_value=None) -> Dict[str, int]:
    if names_value is None or (isinstance(names_value, float) and pd.isna(names_value)):
        return {}
    names = [x.strip() for x in re.split(r"[;,]", str(names_value)) if x.strip()]
    if not names:
        return {}

    explicit: Dict[str, int] = {}
    if demands_value is not None and not (isinstance(demands_value, float) and pd.isna(demands_value)):
        for part in re.split(r"[;,]", str(demands_value)):
            if ":" in part:
                name, qty = part.split(":", 1)
                try:
                    explicit[name.strip()] = max(0, int(float(qty.strip())))
                except ValueError:
                    pass
    return {name: explicit.get(name, 1) for name in names}


def dataframe_to_tasks(df: pd.DataFrame, hours_per_day: int = 8) -> List[Task]:
    id_col = _pick_column(df.columns, "id")
    name_col = _pick_column(df.columns, "name")
    duration_col = _pick_column(df.columns, "duration")
    if not id_col or not name_col or not duration_col:
        raise ValueError(
            "Colunas mínimas não encontradas. Use ID, Nome/Name e Duracao_h/Duration. "
            "Predecessoras e Recursos são opcionais, mas recomendados."
        )

    pred_col = _pick_column(df.columns, "pred")
    res_col = _pick_column(df.columns, "resources")
    demand_col = _pick_column(df.columns, "demands")
    start_col = _pick_column(df.columns, "start")
    finish_col = _pick_column(df.columns, "finish")
    wbs_col = _pick_column(df.columns, "wbs")

    tasks: List[Task] = []
    for _, row in df.iterrows():
        if pd.isna(row[id_col]) or str(row[id_col]).strip() == "":
            continue
        tid = str(row[id_col]).strip()
        if tid.endswith(".0"):
            tid = tid[:-2]
        name = str(row[name_col]).strip()
        duration_h = parse_duration_to_hours(row[duration_col], hours_per_day)
        if duration_h <= 0:
            # Ignore summary rows / blank milestones in this MVP.
            continue
        tasks.append(
            Task(
                id=tid,
                name=name,
                duration_h=duration_h,
                predecessors=parse_predecessors(row[pred_col], hours_per_day) if pred_col else [],
                resources=parse_resources(row[res_col], row[demand_col] if demand_col else None) if res_col else {},
                baseline_start=None if not start_col or pd.isna(row[start_col]) else str(row[start_col]),
                baseline_finish=None if not finish_col or pd.isna(row[finish_col]) else str(row[finish_col]),
                wbs=None if not wbs_col or pd.isna(row[wbs_col]) else str(row[wbs_col]),
            )
        )
    if not tasks:
        raise ValueError("Nenhuma atividade válida foi encontrada no arquivo.")
    return tasks


def _local(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _child_text(node: ET.Element, name: str, default=None):
    for child in node:
        if _local(child.tag) == name:
            return child.text if child.text is not None else default
    return default


def project_xml_to_tasks(content: bytes, hours_per_day: int = 8) -> Tuple[List[Task], Dict[str, int]]:
    root = ET.fromstring(content)

    uid_to_resource: Dict[str, str] = {}
    resource_caps: Dict[str, int] = {}
    for node in root.iter():
        if _local(node.tag) == "Resource":
            uid = _child_text(node, "UID")
            name = _child_text(node, "Name") or f"Resource {uid}"
            max_units = _child_text(node, "MaxUnits")
            if uid:
                uid_to_resource[str(uid)] = name
            try:
                # MaxUnits=1.0 means one full-time equivalent.
                resource_caps[name] = max(1, int(round(float(max_units or 1))))
            except ValueError:
                resource_caps[name] = 1

    assignment_by_task: Dict[str, Dict[str, int]] = {}
    for node in root.iter():
        if _local(node.tag) == "Assignment":
            task_uid = _child_text(node, "TaskUID")
            resource_uid = _child_text(node, "ResourceUID")
            units = _child_text(node, "Units")
            if not task_uid or not resource_uid or resource_uid == "-65535":
                continue
            name = uid_to_resource.get(str(resource_uid), f"Resource {resource_uid}")
            try:
                qty = max(1, int(round(float(units or 1))))
            except ValueError:
                qty = 1
            assignment_by_task.setdefault(str(task_uid), {})[name] = qty

    task_nodes = [n for n in root.iter() if _local(n.tag) == "Task"]
    uid_to_id: Dict[str, str] = {}
    for node in task_nodes:
        uid = _child_text(node, "UID")
        tid = _child_text(node, "ID") or uid
        if uid and tid:
            uid_to_id[str(uid)] = str(tid)

    relation_map = {0: "FF", 1: "FS", 2: "SF", 3: "SS"}
    tasks: List[Task] = []
    for node in task_nodes:
        uid = _child_text(node, "UID")
        tid = _child_text(node, "ID") or uid
        name = _child_text(node, "Name") or f"Task {tid}"
        duration_text = _child_text(node, "Duration")
        summary = _child_text(node, "Summary", "0")
        if not tid or not uid or summary == "1":
            continue
        duration_h = parse_duration_to_hours(duration_text, hours_per_day)
        if duration_h <= 0:
            continue

        links: List[Link] = []
        for child in node:
            if _local(child.tag) != "PredecessorLink":
                continue
            pred_uid = _child_text(child, "PredecessorUID")
            if pred_uid is None:
                continue
            pred_id = uid_to_id.get(str(pred_uid), str(pred_uid))
            try:
                relation = relation_map.get(int(_child_text(child, "Type", "1")), "FS")
            except ValueError:
                relation = "FS"
            try:
                # Project XML LinkLag is tenths of a minute.
                lag_h = int(round(int(_child_text(child, "LinkLag", "0")) / 600))
            except ValueError:
                lag_h = 0
            links.append(Link(pred_id, relation, lag_h))

        tasks.append(
            Task(
                id=str(tid),
                name=name,
                duration_h=duration_h,
                predecessors=links,
                resources=assignment_by_task.get(str(uid), {}),
                baseline_start=_child_text(node, "Start"),
                baseline_finish=_child_text(node, "Finish"),
                wbs=_child_text(node, "WBS"),
            )
        )
    if not tasks:
        raise ValueError("Nenhuma atividade executável foi encontrada no XML do Microsoft Project.")
    return tasks, resource_caps


def load_schedule(uploaded_file, hours_per_day: int = 8) -> Tuple[List[Task], Dict[str, int]]:
    name = uploaded_file.name.lower()
    content = uploaded_file.getvalue()
    if name.endswith(".xml"):
        return project_xml_to_tasks(content, hours_per_day)
    if name.endswith(".csv"):
        try:
            df = pd.read_csv(io.BytesIO(content), sep=None, engine="python")
        except UnicodeDecodeError:
            df = pd.read_csv(io.BytesIO(content), sep=None, engine="python", encoding="latin1")
        return dataframe_to_tasks(df, hours_per_day), {}
    if name.endswith((".xlsx", ".xls")):
        df = pd.read_excel(io.BytesIO(content))
        return dataframe_to_tasks(df, hours_per_day), {}
    raise ValueError("Formato não suportado. Envie XML, XLSX/XLS ou CSV exportado do Microsoft Project.")
