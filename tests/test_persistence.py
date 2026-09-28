from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import inspect, text

from turnaround.persistence import Base, create_sqlite_engine

from turnaround import (
    BaselineRevision,
    DiscoveredTask,
    ExecutionMode,
    ExecutionStore,
    Person,
    Precedence,
    WorkforceProfile,
    build_baseline_revision,
    upgrade_database,
)
from turnaround.mrcpsp import AdvancedScheduledTask


def _database_url(tmp_path) -> str:
    return f"sqlite:///{tmp_path / 'turnaround-test.db'}"


def test_alembic_creates_execution_schema_and_sqlite_pragmas(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)

    tables = set(inspect(store.engine).get_table_names())
    assert {
        "alembic_version",
        "execution_sessions",
        "discovered_tasks",
        "discovered_task_resources",
        "discovered_task_predecessors",
        "discovered_task_successors",
        "execution_events",
        "workforce_profiles",
        "planning_baselines",
        "baseline_revisions",
    }.issubset(tables)

    with store.engine.connect() as connection:
        foreign_keys = connection.execute(text("PRAGMA foreign_keys")).scalar_one()
        journal_mode = connection.execute(text("PRAGMA journal_mode")).scalar_one()

    assert foreign_keys == 1
    assert str(journal_mode).lower() == "wal"


def test_execution_session_and_dynamic_scope_survive_store_restart(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)

    first_store = ExecutionStore(url)
    session = first_store.get_or_create_active_session(
        project_key="project-key-1",
        project_name="Parada teste",
        initial_current_time=2.0,
    )

    task = DiscoveredTask(
        id="DS-001",
        name="Reparar trinca",
        discovered_at=7.0,
        source_task_id="I",
        source_event="crack_detected",
        wbs="DS.1",
        modes=[
            ExecutionMode(
                name="campo",
                duration=4.0,
                resources={"Soldador": 1.0, "Mecânica": 2.0},
                cost=1500.0,
            )
        ],
        precedences=[Precedence(predecessor_id="I")],
        successor_task_ids=["C"],
        notes="achado em inspeção",
    )
    first_store.add_discovered_task(session.id, task)
    first_store.update_execution_state(
        session.id,
        current_time=7.0,
        observed_events={"I": ["crack_detected"]},
        human_selections={"decision-1": ["A"]},
        selected_optional_ids=["OPT-1"],
        scenario_capacities={"Mecânica": 4.0, "Soldador": 1.0},
        stability_weight=0.8,
    )

    # Simula reinicialização do processo/aplicação.
    second_store = ExecutionStore(url)
    restored = second_store.get_or_create_active_session(
        project_key="project-key-1",
        project_name="Parada teste",
    )
    restored_tasks = second_store.load_discovered_tasks(restored.id)
    events = second_store.list_events(restored.id)

    assert restored.id == session.id
    assert restored.current_time == 7.0
    assert restored.observed_events == {"I": ["crack_detected"]}
    assert restored.human_selections == {"decision-1": ["A"]}
    assert restored.selected_optional_ids == ["OPT-1"]
    assert restored.scenario_capacities == {
        "Mecânica": 4.0,
        "Soldador": 1.0,
    }
    assert restored.stability_weight == 0.8
    assert len(restored_tasks) == 1
    assert restored_tasks[0] == task

    event_types = [item.event_type for item in events]
    assert "EXECUTION_SESSION_CREATED" in event_types
    assert "DISCOVERED_TASK_CREATED" in event_types
    assert "EXECUTION_STATE_UPDATED" in event_types


def test_discovered_task_is_normalized_for_audit_and_removal_is_logged(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)
    session = store.get_or_create_active_session(
        project_key="project-key-2",
        project_name="Parada auditável",
    )

    task = DiscoveredTask(
        id="DS-001",
        name="Reparo emergencial",
        discovered_at=3.0,
        modes=[
            ExecutionMode(
                name="campo",
                duration=2.0,
                resources={"Soldador": 1.0},
            )
        ],
        precedences=[Precedence(predecessor_id="A")],
        successor_task_ids=["B"],
    )
    store.add_discovered_task(session.id, task)

    with store.engine.connect() as connection:
        resource = connection.execute(
            text(
                """
                SELECT r.resource_name, r.demand
                FROM discovered_task_resources r
                JOIN discovered_tasks t ON t.id = r.discovered_task_id
                WHERE t.session_id = :session_id AND t.task_id = 'DS-001'
                """
            ),
            {"session_id": session.id},
        ).one()
        predecessor = connection.execute(
            text(
                """
                SELECT p.predecessor_id
                FROM discovered_task_predecessors p
                JOIN discovered_tasks t ON t.id = p.discovered_task_id
                WHERE t.session_id = :session_id AND t.task_id = 'DS-001'
                """
            ),
            {"session_id": session.id},
        ).scalar_one()
        successor = connection.execute(
            text(
                """
                SELECT s.successor_id
                FROM discovered_task_successors s
                JOIN discovered_tasks t ON t.id = s.discovered_task_id
                WHERE t.session_id = :session_id AND t.task_id = 'DS-001'
                """
            ),
            {"session_id": session.id},
        ).scalar_one()

    assert resource.resource_name == "Soldador"
    assert resource.demand == 1.0
    assert predecessor == "A"
    assert successor == "B"

    store.remove_discovered_task(session.id, "DS-001")
    assert store.load_discovered_tasks(session.id) == []

    events = store.list_events(session.id)
    assert any(
        event.event_type == "DISCOVERED_TASK_REMOVED"
        and event.task_id == "DS-001"
        for event in events
    )


def test_start_new_session_archives_previous_execution(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)

    first = store.get_or_create_active_session(
        project_key="same-baseline",
        project_name="Parada A",
    )
    second = store.start_new_session(
        project_key="same-baseline",
        project_name="Parada A",
        initial_current_time=1.0,
    )
    active = store.get_or_create_active_session(
        project_key="same-baseline",
        project_name="Parada A",
    )

    assert first.id != second.id
    assert active.id == second.id
    assert active.current_time == 1.0



def test_upgrade_repairs_unversioned_initial_schema_without_deleting_data(tmp_path):
    url = _database_url(tmp_path)
    engine = create_sqlite_engine(url)

    # Reproduz o estado visto em produção: a primeira tabela foi materializada,
    # mas a migration ainda não conseguiu registrar seu revision marker.
    Base.metadata.tables["execution_sessions"].create(engine)
    with engine.begin() as connection:
        connection.execute(
            text(
                """
                INSERT INTO execution_sessions (
                    id, project_key, project_name, status, current_time,
                    observed_events_json, human_selections_json,
                    selected_optional_ids_json, scenario_capacities_json,
                    stability_weight, created_at, updated_at
                ) VALUES (
                    'recover-me', 'key', 'Parada interrompida', 'active', 3,
                    '{}', '{}', '[]', '{}', 1.0,
                    CURRENT_TIMESTAMP, CURRENT_TIMESTAMP
                )
                """
            )
        )
    engine.dispose()

    upgrade_database(url)

    repaired = create_sqlite_engine(url)
    inspector = inspect(repaired)
    assert {
        "execution_sessions",
        "discovered_tasks",
        "discovered_task_resources",
        "discovered_task_predecessors",
        "discovered_task_successors",
        "execution_events",
        "alembic_version",
    }.issubset(set(inspector.get_table_names()))

    with repaired.connect() as connection:
        revision = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalar_one()
        preserved = connection.execute(
            text("SELECT project_name FROM execution_sessions WHERE id='recover-me'")
        ).scalar_one()

    assert revision == "0005_baseline_revisions"
    assert preserved == "Parada interrompida"


def test_concurrent_upgrade_database_calls_are_serialized(tmp_path):
    url = _database_url(tmp_path)

    with ThreadPoolExecutor(max_workers=4) as pool:
        futures = [
            pool.submit(upgrade_database, url)
            for _ in range(4)
        ]
        for future in futures:
            future.result()

    engine = create_sqlite_engine(url)
    with engine.connect() as connection:
        revisions = connection.execute(
            text("SELECT version_num FROM alembic_version")
        ).scalars().all()

    assert revisions == ["0005_baseline_revisions"]



def test_workforce_profile_survives_store_restart(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)

    profile = WorkforceProfile(
        enabled=True,
        skills=["Mecânica", "Soldagem", "Elétrica"],
        people=[
            Person(
                id="P-001",
                name="Ana",
                skills=["Mecânica", "Soldagem"],
            ),
            Person(
                id="P-002",
                name="Bruno",
                skills=["Mecânica"],
            ),
        ],
    )

    first = ExecutionStore(url)
    first.save_workforce_profile(profile)

    second = ExecutionStore(url)
    restored = second.load_workforce_profile()

    assert restored == profile


def test_formal_baseline_revision_survives_store_restart(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)

    first = ExecutionStore(url)
    session = first.get_or_create_active_session(
        project_key="baseline-revision-project",
        project_name="Parada com rebaseline",
    )

    revision = build_baseline_revision(
        session_id=session.id,
        project_key=session.project_key,
        revision_number=1,
        name="Rev.1 · pós-inspeção",
        reason="Ampliação formal do escopo após inspeção",
        approved_by="Coordenação da parada",
        notes="Janela revisada",
        snapshot_id="snapshot-001",
        previous_baseline_label="Original",
        previous_makespan_h=36.0,
        previous_deadline_h=40.0,
        original_makespan_h=36.0,
        current_time_h=12.0,
        makespan_h=42.0,
        deadline_h=48.0,
        total_cost=1200.0,
        schedule_items=[
            AdvancedScheduledTask(
                task_id="A",
                task_name="Atividade A",
                mode_name="base",
                start=0.0,
                finish=8.0,
                duration=8.0,
                resources={"Mecânica": 2.0},
                cost=100.0,
            ),
            AdvancedScheduledTask(
                task_id="DS-001",
                task_name="Reparo descoberto",
                mode_name="campo",
                start=12.0,
                finish=18.0,
                duration=6.0,
                resources={"Soldagem": 1.0},
                cost=1100.0,
            ),
        ],
        wbs_by_id={"A": "1.1", "DS-001": "DS.1"},
    )

    first.save_baseline_revision(revision)

    second = ExecutionStore(url)
    restored = second.list_baseline_revisions(session.id)
    latest = second.latest_baseline_revision(session.id)
    events = second.list_events(session.id)

    assert restored == [revision]
    assert latest == revision
    assert latest.reference_start_times() == {
        "A": 0.0,
        "DS-001": 12.0,
    }
    assert latest.previous_baseline_label == "Original"
    assert latest.previous_makespan_h == 36.0
    assert latest.previous_deadline_h == 40.0
    assert latest.original_makespan_h == 36.0
    assert latest.delta_vs_previous_h == 6.0
    assert latest.delta_vs_original_h == 6.0
    assert second.next_baseline_revision_number(session.id) == 2
    assert any(
        event.event_type == "BASELINE_REVISION_APPROVED"
        and event.payload["revision_number"] == 1
        and event.payload["previous_baseline_label"] == "Original"
        and event.payload["delta_vs_previous_h"] == 6.0
        for event in events
    )


def test_formal_baseline_revision_is_immutable_per_number(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)
    session = store.get_or_create_active_session(
        project_key="immutable-baseline",
        project_name="Parada",
    )

    revision = build_baseline_revision(
        session_id=session.id,
        project_key=session.project_key,
        revision_number=1,
        name="Rev.1",
        reason="Mudança aprovada",
        approved_by="Gerência",
        snapshot_id="snap",
        current_time_h=5.0,
        makespan_h=20.0,
        deadline_h=24.0,
        total_cost=0.0,
        schedule_items=[
            AdvancedScheduledTask(
                task_id="A",
                task_name="A",
                mode_name="base",
                start=0.0,
                finish=20.0,
                duration=20.0,
                resources={},
                cost=0.0,
            )
        ],
    )
    store.save_baseline_revision(revision)

    import pytest

    with pytest.raises(ValueError, match="já existe"):
        store.save_baseline_revision(revision)



def test_new_revision_requires_approver_for_governance(tmp_path):
    import pytest

    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)
    session = store.get_or_create_active_session(
        project_key="approval-required",
        project_name="Parada",
    )

    with pytest.raises(ValueError, match="aprovador"):
        build_baseline_revision(
            session_id=session.id,
            project_key=session.project_key,
            revision_number=1,
            name="Rev.1",
            reason="Mudança formal",
            snapshot_id="snap-approval",
            current_time_h=4.0,
            makespan_h=12.0,
            deadline_h=16.0,
            total_cost=0.0,
            schedule_items=[
                AdvancedScheduledTask(
                    task_id="A",
                    task_name="A",
                    mode_name="base",
                    start=0.0,
                    finish=12.0,
                    duration=12.0,
                    resources={},
                    cost=0.0,
                )
            ],
        )



def test_legacy_revision_without_audit_context_remains_readable(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)
    session = store.get_or_create_active_session(
        project_key="legacy-revision",
        project_name="Parada legado",
    )

    revision = build_baseline_revision(
        session_id=session.id,
        project_key=session.project_key,
        revision_number=1,
        name="Rev.1",
        reason="Mudança aprovada",
        approved_by="Coordenação",
        snapshot_id="legacy-snap",
        current_time_h=2.0,
        makespan_h=10.0,
        deadline_h=12.0,
        total_cost=0.0,
        schedule_items=[
            AdvancedScheduledTask(
                task_id="A",
                task_name="A",
                mode_name="base",
                start=0.0,
                finish=10.0,
                duration=10.0,
                resources={},
                cost=0.0,
            )
        ],
    )
    payload = revision.model_dump(mode="json")
    for field in (
        "previous_baseline_label",
        "previous_makespan_h",
        "previous_deadline_h",
        "original_makespan_h",
        "delta_vs_previous_h",
        "delta_vs_original_h",
    ):
        payload.pop(field, None)

    restored = BaselineRevision.model_validate(payload)

    assert restored.previous_baseline_label is None
    assert restored.previous_makespan_h is None
    assert restored.delta_vs_previous_h is None
    assert restored.delta_vs_original_h is None


def test_progress_import_survives_store_restart_without_new_schema(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)

    first = ExecutionStore(url)
    session = first.get_or_create_active_session(
        project_key="progress-project",
        project_name="Parada progresso",
    )
    event = first.record_progress_import(
        session.id,
        source_name="progresso.xml",
        rows=[
            {
                "task_id": "10",
                "project_uid": "1010",
                "task_name": "Abrir equipamento",
                "percent_complete": 100.0,
                "status": "completed",
                "actual_start_h": 1.0,
                "actual_finish_h": 3.0,
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
                "remaining_duration_h": 2.5,
                "remaining_as_of_h": 4.0,
                "source_reference": "UID 2020",
            },
        ],
        warnings=["UID 999: atividade ignorada."],
    )

    second = ExecutionStore(url)
    restored = second.latest_progress_import(session.id)

    assert event.event_type == "PROGRESS_IMPORTED"
    assert restored is not None
    assert restored.payload["source_name"] == "progresso.xml"
    assert restored.payload["matched_rows"] == 2
    assert restored.payload["rows"][0]["task_id"] == "10"
    assert restored.payload["rows"][1]["percent_complete"] == 40.0
    assert restored.payload["rows"][0]["actual_start_h"] == 1.0
    assert restored.payload["rows"][0]["actual_finish_h"] == 3.0
    assert restored.payload["rows"][1]["actual_start_h"] == 3.5
    assert restored.payload["rows"][1]["remaining_duration_h"] == 2.5
    assert restored.payload["rows"][1]["remaining_as_of_h"] == 4.0
    assert restored.payload["warnings"] == ["UID 999: atividade ignorada."]

    events = second.list_events(session.id)
    assert any(item.event_type == "PROGRESS_IMPORTED" for item in events)


def test_progress_batch_uses_optimistic_concurrency_and_audit_metadata(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)
    session = store.get_or_create_active_session(
        project_key="batch-progress",
        project_name="Parada lote",
    )

    first = store.record_progress_import(
        session.id,
        source_name="primeiro.csv",
        rows=[
            {
                "task_id": "10",
                "project_uid": "1010",
                "task_name": "Abrir",
                "percent_complete": 20.0,
                "status": "in_progress",
                "actual_start_h": 1.0,
                "actual_finish_h": None,
                "remaining_duration_h": 4.0,
                "remaining_as_of_h": 2.0,
                "source_reference": "UID 1010",
            }
        ],
        expected_previous_event_id=None,
        changed_task_ids=["10"],
        incoming_rows=1,
    )

    second = store.record_progress_import(
        session.id,
        source_name="segundo.csv",
        rows=[
            {
                "task_id": "10",
                "project_uid": "1010",
                "task_name": "Abrir",
                "percent_complete": 40.0,
                "status": "in_progress",
                "actual_start_h": 1.0,
                "actual_finish_h": None,
                "remaining_duration_h": 2.0,
                "remaining_as_of_h": 3.0,
                "source_reference": "UID 1010",
            },
            {
                "task_id": "20",
                "project_uid": "2020",
                "task_name": "Inspecionar",
                "percent_complete": 0.0,
                "status": "not_started",
                "actual_start_h": None,
                "actual_finish_h": None,
                "remaining_duration_h": None,
                "remaining_as_of_h": None,
                "source_reference": "UID 2020",
            },
        ],
        expected_previous_event_id=first.id,
        changed_task_ids=["10"],
        preserved_task_ids=["20"],
        incoming_rows=1,
    )

    assert second.payload["incoming_rows"] == 1
    assert second.payload["changed_task_ids"] == ["10"]
    assert second.payload["preserved_task_ids"] == ["20"]
    assert second.payload["matched_rows"] == 2

    with pytest.raises(ValueError, match="mudou desde a prévia"):
        store.record_progress_import(
            session.id,
            source_name="usuario-desatualizado.csv",
            rows=second.payload["rows"],
            expected_previous_event_id=first.id,
            changed_task_ids=["10"],
            incoming_rows=1,
        )


def test_progress_batch_rejects_missing_expected_previous_event(tmp_path):
    url = _database_url(tmp_path)
    upgrade_database(url)
    store = ExecutionStore(url)
    session = store.get_or_create_active_session(
        project_key="batch-race",
        project_name="Parada lote concorrente",
    )

    store.record_progress_import(
        session.id,
        source_name="estado.csv",
        rows=[
            {
                "task_id": "10",
                "project_uid": None,
                "task_name": "Atividade",
                "percent_complete": 10.0,
                "status": "in_progress",
                "actual_start_h": 0.0,
                "actual_finish_h": None,
                "remaining_duration_h": 4.0,
                "remaining_as_of_h": 1.0,
                "source_reference": "ID 10",
            }
        ],
        expected_previous_event_id=None,
    )

    with pytest.raises(ValueError, match="mudou desde a prévia"):
        store.record_progress_import(
            session.id,
            source_name="sem-refresh.csv",
            rows=[
                {
                    "task_id": "10",
                    "project_uid": None,
                    "task_name": "Atividade",
                    "percent_complete": 20.0,
                    "status": "in_progress",
                    "actual_start_h": 0.0,
                    "actual_finish_h": None,
                    "remaining_duration_h": 3.0,
                    "remaining_as_of_h": 2.0,
                    "source_reference": "ID 10",
                }
            ],
            expected_previous_event_id=None,
        )
