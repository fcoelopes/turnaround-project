from turnaround.io import dataframe_to_tasks
from turnaround.rcpsp import optimize_turnaround

import pandas as pd


def test_rcpsp_respects_shared_resource_capacity():
    df = pd.DataFrame([
        {"ID": 1, "Nome": "A", "Duracao_h": 4, "Predecessoras": "", "Recursos": "Mecânica"},
        {"ID": 2, "Nome": "B", "Duracao_h": 4, "Predecessoras": "", "Recursos": "Mecânica"},
        {"ID": 3, "Nome": "C", "Duracao_h": 2, "Predecessoras": "1FS;2FS", "Recursos": "Operação"},
    ])
    tasks = dataframe_to_tasks(df)
    best, _ = optimize_turnaround(tasks, {"Mecânica": 1, "Operação": 1})
    assert best.makespan_h == 10


def test_parallel_when_capacity_allows():
    df = pd.DataFrame([
        {"ID": 1, "Nome": "A", "Duracao_h": 4, "Predecessoras": "", "Recursos": "Mecânica"},
        {"ID": 2, "Nome": "B", "Duracao_h": 4, "Predecessoras": "", "Recursos": "Mecânica"},
    ])
    tasks = dataframe_to_tasks(df)
    best, _ = optimize_turnaround(tasks, {"Mecânica": 2})
    assert best.makespan_h == 4
