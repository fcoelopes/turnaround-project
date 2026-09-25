from __future__ import annotations

import json
import os
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from alembic import command
from alembic.config import Config
from sqlalchemy import (
    DateTime,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    create_engine,
    event,
    select,
)
from sqlalchemy.engine import Engine
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column, relationship, sessionmaker

from .advanced_models import DiscoveredTask


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB_PATH = ROOT / "data" / "turnaround.db"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_database_url() -> str:
    configured = os.getenv("TURNAROUND_DATABASE_URL")
    if configured:
        return configured

    path = Path(os.getenv("TURNAROUND_DB_PATH", str(DEFAULT_DB_PATH))).expanduser()
    if not path.is_absolute():
        path = (ROOT / path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    return f"sqlite:///{path}"


class Base(DeclarativeBase):
    pass


class ExecutionSessionRecord(Base):
    __tablename__ = "execution_sessions"

    id: Mapped[str] = mapped_column(String(36), primary_key=True)
    project_key: Mapped[str] = mapped_column(String(64), index=True)
    project_name: Mapped[str] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(32), default="active", index=True)
    current_time: Mapped[float] = mapped_column(Float, default=0.0)
    observed_events_json: Mapped[str] = mapped_column(Text, default="{}")
    human_selections_json: Mapped[str] = mapped_column(Text, default="{}")
    selected_optional_ids_json: Mapped[str] = mapped_column(Text, default="[]")
    scenario_capacities_json: Mapped[str] = mapped_column(Text, default="{}")
    stability_weight: Mapped[float] = mapped_column(Float, default=1.0)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    discovered_tasks: Mapped[list["DiscoveredTaskRecord"]] = relationship(
        back_populates="session",
        cascade="all, delete-orphan",
    )


class DiscoveredTaskRecord(Base):
    __tablename__ = "discovered_tasks"
    __table_args__ = (
        UniqueConstraint("session_id", "task_id", name="uq_discovered_task_session_task"),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("execution_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    task_id: Mapped[str] = mapped_column(String(64))
    name: Mapped[str] = mapped_column(String(255))
    discovered_at: Mapped[float] = mapped_column(Float)
    source_task_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    source_event: Mapped[str | None] = mapped_column(String(255), nullable=True)
    wbs: Mapped[str | None] = mapped_column(String(255), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    payload_json: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)

    session: Mapped[ExecutionSessionRecord] = relationship(back_populates="discovered_tasks")
    resources: Mapped[list["DiscoveredTaskResourceRecord"]] = relationship(
        cascade="all, delete-orphan",
    )
    predecessors: Mapped[list["DiscoveredTaskPredecessorRecord"]] = relationship(
        cascade="all, delete-orphan",
    )
    successors: Mapped[list["DiscoveredTaskSuccessorRecord"]] = relationship(
        cascade="all, delete-orphan",
    )


class DiscoveredTaskResourceRecord(Base):
    __tablename__ = "discovered_task_resources"
    __table_args__ = (
        UniqueConstraint(
            "discovered_task_id",
            "mode_name",
            "resource_name",
            name="uq_discovered_task_mode_resource",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discovered_task_id: Mapped[int] = mapped_column(
        ForeignKey("discovered_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    mode_name: Mapped[str] = mapped_column(String(128))
    resource_name: Mapped[str] = mapped_column(String(255))
    demand: Mapped[float] = mapped_column(Float)


class DiscoveredTaskPredecessorRecord(Base):
    __tablename__ = "discovered_task_predecessors"
    __table_args__ = (
        UniqueConstraint(
            "discovered_task_id",
            "predecessor_id",
            name="uq_discovered_task_predecessor",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discovered_task_id: Mapped[int] = mapped_column(
        ForeignKey("discovered_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    predecessor_id: Mapped[str] = mapped_column(String(64))
    relation: Mapped[str] = mapped_column(String(2), default="FS")
    lag: Mapped[float] = mapped_column(Float, default=0.0)


class DiscoveredTaskSuccessorRecord(Base):
    __tablename__ = "discovered_task_successors"
    __table_args__ = (
        UniqueConstraint(
            "discovered_task_id",
            "successor_id",
            name="uq_discovered_task_successor",
        ),
    )

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    discovered_task_id: Mapped[int] = mapped_column(
        ForeignKey("discovered_tasks.id", ondelete="CASCADE"),
        index=True,
    )
    successor_id: Mapped[str] = mapped_column(String(64))


class ExecutionEventRecord(Base):
    __tablename__ = "execution_events"

    id: Mapped[int] = mapped_column(Integer, primary_key=True, autoincrement=True)
    session_id: Mapped[str] = mapped_column(
        ForeignKey("execution_sessions.id", ondelete="CASCADE"),
        index=True,
    )
    event_type: Mapped[str] = mapped_column(String(64), index=True)
    task_id: Mapped[str | None] = mapped_column(String(64), nullable=True, index=True)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utc_now)
    payload_json: Mapped[str] = mapped_column(Text, default="{}")


@dataclass(frozen=True)
class ExecutionSessionSnapshot:
    id: str
    project_key: str
    project_name: str
    current_time: float
    observed_events: dict[str, list[str]]
    human_selections: dict[str, list[str]]
    selected_optional_ids: list[str]
    scenario_capacities: dict[str, float]
    stability_weight: float


@dataclass(frozen=True)
class ExecutionEvent:
    id: int
    event_type: str
    task_id: str | None
    occurred_at: datetime
    payload: dict[str, Any]


def _json_dump(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _json_load_list(raw: str | None) -> list[Any]:
    if not raw:
        return []
    value = json.loads(raw)
    if not isinstance(value, list):
        raise ValueError("JSON persistido deveria ser lista")
    return value


def _json_load_dict(raw: str | None) -> dict[str, Any]:
    if not raw:
        return {}
    value = json.loads(raw)
    if not isinstance(value, dict):
        raise ValueError("JSON persistido deveria ser objeto")
    return value


def create_sqlite_engine(database_url: str | None = None) -> Engine:
    url = database_url or default_database_url()
    connect_args: dict[str, Any] = {}
    if url.startswith("sqlite:"):
        connect_args["check_same_thread"] = False

    engine = create_engine(
        url,
        future=True,
        connect_args=connect_args,
    )

    if url.startswith("sqlite:"):
        @event.listens_for(engine, "connect")
        def _set_sqlite_pragmas(dbapi_connection, _connection_record):
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.execute("PRAGMA journal_mode=WAL")
            cursor.execute("PRAGMA synchronous=NORMAL")
            cursor.close()

    return engine


def upgrade_database(database_url: str | None = None) -> None:
    url = database_url or default_database_url()
    config = Config(str(ROOT / "alembic.ini"))
    config.set_main_option("script_location", str(ROOT / "alembic"))
    config.set_main_option("sqlalchemy.url", url)
    command.upgrade(config, "head")


class ExecutionStore:
    def __init__(self, database_url: str | None = None):
        self.database_url = database_url or default_database_url()
        self.engine = create_sqlite_engine(self.database_url)
        self.SessionLocal = sessionmaker(
            bind=self.engine,
            expire_on_commit=False,
            class_=Session,
        )

    def get_or_create_active_session(
        self,
        *,
        project_key: str,
        project_name: str,
        initial_current_time: float = 0.0,
    ) -> ExecutionSessionSnapshot:
        with self.SessionLocal.begin() as db:
            record = db.scalar(
                select(ExecutionSessionRecord)
                .where(
                    ExecutionSessionRecord.project_key == project_key,
                    ExecutionSessionRecord.status == "active",
                )
                .order_by(ExecutionSessionRecord.created_at.desc())
            )
            if record is None:
                record = ExecutionSessionRecord(
                    id=str(uuid.uuid4()),
                    project_key=project_key,
                    project_name=project_name,
                    status="active",
                    current_time=float(initial_current_time),
                    observed_events_json="{}",
                    human_selections_json="{}",
                    selected_optional_ids_json="[]",
                    scenario_capacities_json="{}",
                    stability_weight=1.0,
                    created_at=utc_now(),
                    updated_at=utc_now(),
                )
                db.add(record)
                db.flush()
                self._append_event(
                    db,
                    session_id=record.id,
                    event_type="EXECUTION_SESSION_CREATED",
                    payload={
                        "project_key": project_key,
                        "project_name": project_name,
                    },
                )
            return self._snapshot(record)

    def start_new_session(
        self,
        *,
        project_key: str,
        project_name: str,
        initial_current_time: float = 0.0,
    ) -> ExecutionSessionSnapshot:
        with self.SessionLocal.begin() as db:
            active = db.scalars(
                select(ExecutionSessionRecord).where(
                    ExecutionSessionRecord.project_key == project_key,
                    ExecutionSessionRecord.status == "active",
                )
            ).all()
            for record in active:
                record.status = "archived"
                record.updated_at = utc_now()
                self._append_event(
                    db,
                    session_id=record.id,
                    event_type="EXECUTION_SESSION_ARCHIVED",
                )

            record = ExecutionSessionRecord(
                id=str(uuid.uuid4()),
                project_key=project_key,
                project_name=project_name,
                status="active",
                current_time=float(initial_current_time),
                observed_events_json="{}",
                human_selections_json="{}",
                selected_optional_ids_json="[]",
                scenario_capacities_json="{}",
                stability_weight=1.0,
                created_at=utc_now(),
                updated_at=utc_now(),
            )
            db.add(record)
            db.flush()
            self._append_event(
                db,
                session_id=record.id,
                event_type="EXECUTION_SESSION_CREATED",
                payload={
                    "project_key": project_key,
                    "project_name": project_name,
                },
            )
            return self._snapshot(record)

    def load_discovered_tasks(self, session_id: str) -> list[DiscoveredTask]:
        with self.SessionLocal() as db:
            rows = db.scalars(
                select(DiscoveredTaskRecord)
                .where(DiscoveredTaskRecord.session_id == session_id)
                .order_by(DiscoveredTaskRecord.discovered_at, DiscoveredTaskRecord.id)
            ).all()
            return [
                DiscoveredTask.model_validate_json(row.payload_json)
                for row in rows
            ]

    def add_discovered_task(
        self,
        session_id: str,
        task: DiscoveredTask,
    ) -> None:
        with self.SessionLocal.begin() as db:
            self._require_session(db, session_id)
            existing = db.scalar(
                select(DiscoveredTaskRecord).where(
                    DiscoveredTaskRecord.session_id == session_id,
                    DiscoveredTaskRecord.task_id == task.id,
                )
            )
            if existing is not None:
                raise ValueError(
                    f"Atividade descoberta {task.id} já existe nesta sessão"
                )

            record = DiscoveredTaskRecord(
                session_id=session_id,
                task_id=task.id,
                name=task.name,
                discovered_at=float(task.discovered_at),
                source_task_id=task.source_task_id,
                source_event=task.source_event,
                wbs=task.wbs,
                notes=task.notes,
                payload_json=task.model_dump_json(),
                created_at=utc_now(),
            )
            db.add(record)
            db.flush()

            for mode in task.modes:
                for resource_name, demand in mode.resources.items():
                    record.resources.append(
                        DiscoveredTaskResourceRecord(
                            mode_name=mode.name,
                            resource_name=resource_name,
                            demand=float(demand),
                        )
                    )
            for predecessor in task.precedences:
                record.predecessors.append(
                    DiscoveredTaskPredecessorRecord(
                        predecessor_id=predecessor.predecessor_id,
                        relation=predecessor.relation,
                        lag=float(predecessor.lag),
                    )
                )
            for successor_id in task.successor_task_ids:
                record.successors.append(
                    DiscoveredTaskSuccessorRecord(
                        successor_id=successor_id,
                    )
                )

            self._append_event(
                db,
                session_id=session_id,
                event_type="DISCOVERED_TASK_CREATED",
                task_id=task.id,
                payload=task.model_dump(mode="json"),
            )

    def remove_discovered_task(self, session_id: str, task_id: str) -> None:
        with self.SessionLocal.begin() as db:
            record = db.scalar(
                select(DiscoveredTaskRecord).where(
                    DiscoveredTaskRecord.session_id == session_id,
                    DiscoveredTaskRecord.task_id == task_id,
                )
            )
            if record is None:
                raise ValueError(
                    f"Atividade descoberta {task_id} não existe nesta sessão"
                )

            self._append_event(
                db,
                session_id=session_id,
                event_type="DISCOVERED_TASK_REMOVED",
                task_id=task_id,
                payload={"name": record.name},
            )
            db.delete(record)

    def update_execution_state(
        self,
        session_id: str,
        *,
        current_time: float,
        observed_events: dict[str, list[str]],
        human_selections: dict[str, list[str]],
        selected_optional_ids: list[str],
        scenario_capacities: dict[str, float],
        stability_weight: float,
    ) -> ExecutionSessionSnapshot:
        normalized_events = {
            str(task_id): sorted(set(values))
            for task_id, values in observed_events.items()
            if values
        }
        normalized_selections = {
            str(group_id): sorted(set(values))
            for group_id, values in human_selections.items()
            if values
        }
        normalized_optional_ids = sorted(set(selected_optional_ids))
        normalized_capacities = {
            str(resource): float(value)
            for resource, value in scenario_capacities.items()
        }

        with self.SessionLocal.begin() as db:
            record = self._require_session(db, session_id)

            old_time = float(record.current_time)
            old_events = _json_load_dict(record.observed_events_json)
            old_selections = _json_load_dict(record.human_selections_json)
            old_optional_ids = _json_load_list(record.selected_optional_ids_json)
            old_capacities = _json_load_dict(record.scenario_capacities_json)
            old_stability_weight = float(record.stability_weight)

            changed: dict[str, Any] = {}
            if abs(old_time - float(current_time)) > 1e-9:
                record.current_time = float(current_time)
                changed["current_time"] = {
                    "from": old_time,
                    "to": float(current_time),
                }
            if old_events != normalized_events:
                record.observed_events_json = _json_dump(normalized_events)
                changed["observed_events"] = {
                    "from": old_events,
                    "to": normalized_events,
                }
            if old_selections != normalized_selections:
                record.human_selections_json = _json_dump(normalized_selections)
                changed["human_selections"] = {
                    "from": old_selections,
                    "to": normalized_selections,
                }
            if old_optional_ids != normalized_optional_ids:
                record.selected_optional_ids_json = _json_dump(normalized_optional_ids)
                changed["selected_optional_ids"] = {
                    "from": old_optional_ids,
                    "to": normalized_optional_ids,
                }
            if old_capacities != normalized_capacities:
                record.scenario_capacities_json = _json_dump(normalized_capacities)
                changed["scenario_capacities"] = {
                    "from": old_capacities,
                    "to": normalized_capacities,
                }
            if abs(old_stability_weight - float(stability_weight)) > 1e-9:
                record.stability_weight = float(stability_weight)
                changed["stability_weight"] = {
                    "from": old_stability_weight,
                    "to": float(stability_weight),
                }

            if changed:
                record.updated_at = utc_now()
                self._append_event(
                    db,
                    session_id=session_id,
                    event_type="EXECUTION_STATE_UPDATED",
                    payload=changed,
                )

            return self._snapshot(record)

    def list_events(
        self,
        session_id: str,
        *,
        limit: int = 200,
    ) -> list[ExecutionEvent]:
        with self.SessionLocal() as db:
            rows = db.scalars(
                select(ExecutionEventRecord)
                .where(ExecutionEventRecord.session_id == session_id)
                .order_by(ExecutionEventRecord.id.desc())
                .limit(limit)
            ).all()
            return [
                ExecutionEvent(
                    id=row.id,
                    event_type=row.event_type,
                    task_id=row.task_id,
                    occurred_at=row.occurred_at,
                    payload=_json_load_dict(row.payload_json),
                )
                for row in rows
            ]

    @staticmethod
    def _snapshot(record: ExecutionSessionRecord) -> ExecutionSessionSnapshot:
        return ExecutionSessionSnapshot(
            id=record.id,
            project_key=record.project_key,
            project_name=record.project_name,
            current_time=float(record.current_time),
            observed_events={
                str(key): list(value)
                for key, value in _json_load_dict(record.observed_events_json).items()
            },
            human_selections={
                str(key): list(value)
                for key, value in _json_load_dict(record.human_selections_json).items()
            },
            selected_optional_ids=[
                str(value)
                for value in _json_load_list(record.selected_optional_ids_json)
            ],
            scenario_capacities={
                str(key): float(value)
                for key, value in _json_load_dict(record.scenario_capacities_json).items()
            },
            stability_weight=float(record.stability_weight),
        )

    @staticmethod
    def _require_session(
        db: Session,
        session_id: str,
    ) -> ExecutionSessionRecord:
        record = db.get(ExecutionSessionRecord, session_id)
        if record is None:
            raise ValueError(f"Sessão de execução desconhecida: {session_id}")
        return record

    @staticmethod
    def _append_event(
        db: Session,
        *,
        session_id: str,
        event_type: str,
        task_id: str | None = None,
        payload: dict[str, Any] | None = None,
    ) -> None:
        db.add(
            ExecutionEventRecord(
                session_id=session_id,
                event_type=event_type,
                task_id=task_id,
                occurred_at=utc_now(),
                payload_json=_json_dump(payload or {}),
            )
        )
