from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor

from sqlalchemy import inspect, text

from turnaround.persistence import Base, create_sqlite_engine

from turnaround import (
    DiscoveredTask,
    ExecutionMode,
    ExecutionStore,
    Person,
    Precedence,
    WorkforceProfile,
    upgrade_database,
)


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

    assert revision == "0003_multiskill_workforce"
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

    assert revisions == ["0003_multiskill_workforce"]



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
