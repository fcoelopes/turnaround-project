# Turnaround Scheduler — Streamlit MVP

Aplicação para receber um cronograma exportado do Microsoft Project e aplicar um modelo inicial de **turnaround/shutdown scheduling**.

## O que o MVP faz

1. Importa **Microsoft Project XML**, Excel ou CSV.
2. Normaliza atividades, durações, predecessoras, recursos e WBS/EDT.
3. Calcula uma referência CPM sem limitação de recursos.
4. Gera cronograma factível por recursos com um **RCPSP heurístico** (Serial Schedule Generation Scheme).
5. Testa quatro regras de prioridade e escolhe o menor atraso/makespan.
6. Mostra Gantt, gargalos, utilização dos recursos e penalidade de capacidade.
7. Faz Monte Carlo triangular das durações e calcula P50/P80/P90 e probabilidade de cumprir a janela.
8. Exporta o resultado para Excel.

## Por que RCPSP?

Turnarounds são projetos de manutenção de grande escala com precedências fortes, duração curta e recursos limitados. O problema é naturalmente próximo do Resource-Constrained Project Scheduling Problem; variantes de shutdown maintenance também incorporam equipes multi-skill, modos de execução e avaliação de risco de prazo.

## Rodar

```bash
python -m venv .venv
source .venv/bin/activate        # Linux/WSL
# .venv\\Scripts\\activate       # Windows PowerShell
pip install -r requirements.txt
streamlit run app.py
```

## Entrada recomendada

### Microsoft Project

Use **Salvar como / Exportar → XML**. O parser lê:

- Task ID / UID / Name / Duration / WBS
- PredecessorLink (FS, SS, FF, SF e lag)
- Resources / Assignments / Units

### Excel/CSV

Exemplo:

| ID | Nome | Duracao_h | Predecessoras | Recursos | Demandas |
|---:|---|---:|---|---|---|
| 10 | Abrir equipamento | 8 | 5FS | Mecânica;Guindaste | Mecânica:3;Guindaste:1 |
| 20 | Inspecionar | 4 | 10FS | Inspeção | Inspeção:2 |

O arquivo `sample_data/cronograma_exemplo.csv` pode ser usado imediatamente.

## Limitações intencionais do MVP

- O motor é heurístico, não prova ótimo global.
- Ainda não há calendário por turno, folga de refeição, indisponibilidade individual ou overtime.
- O CPM exibido é uma aproximação de folga para vínculos complexos; o agendador respeita FS/SS/FF/SF e lag na programação.
- Recursos do Excel/CSV usam demanda unitária por padrão; a coluna `Demandas` permite sobrescrever.
- Não lê `.mpp` nativo. XML é preferido porque evita dependência Java/MPXJ.
- A simulação de risco perturba duração; ainda não modela descoberta de escopo emergente como eventos discretos.

## Próxima evolução

O núcleo foi separado da interface para substituir o heurístico por **CP-SAT/MILP** sem refazer a tela. Próximas camadas sugeridas:

- calendários e turnos 24x7;
- multi-skill RCPSP;
- crew sizing / modos de execução;
- custos de overtime e contratação;
- tarefas de inspeção que liberam escopo emergente;
- restrições de área, LOTO, acesso, guindaste e simultaneidade;
- buffers por risco e janela P80;
- reexportação compatível com Microsoft Project.
