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
8. Exporta os dados técnicos para Excel.
9. Gera um **relatório gerencial em PDF** com KPIs, gargalos, risco e cronograma.
10. Em uma página avançada, trata **MRCPSP**, escopo opcional/condicional e rescheduling após inspeções.

## Dois níveis de planejamento

### Planejamento-base — RCPSP

A página principal mantém o MVP original: CPM + RCPSP heurístico + risco de prazo.
É apropriada quando o escopo já é conhecido e cada atividade possui um único modo
de execução.

### Scope discovery — MRCPSP + escopo condicional

A página **Escopo Condicional MRCPSP** acrescenta a dinâmica típica de turnaround:

- modos alternativos de execução por atividade;
- atividades `mandatory`, `optional` e `conditional`;
- eventos gerados por inspeções;
- múltiplos achados simultâneos;
- grupos lógicos AND, OR e XOR;
- rescheduling com atividades concluídas/em andamento congeladas;
- sidecar JSON para regras que não pertencem ao arquivo do Microsoft Project.

O parser de XML é o mesmo do MVP base. Não existe um segundo modelo de importação:
o cronograma é lido uma vez e convertido para o domínio avançado somente quando
essa página é usada.

### Identidade estável das regras de escopo

Para XML do Microsoft Project, o sidecar deve preferir o **UID da tarefa** em vez
do ID visual. O ID pode mudar quando linhas são inseridas, removidas ou
reordenadas; o UID é preservado pelo Project para a mesma tarefa enquanto ela
não for excluída/recriada.

Formas recomendadas:

```json
{
  "task_uid_overrides": {
    "9009": {
      "activation": {
        "kind": "conditional",
        "conditions": [
          {
            "source_task_uid": "9004",
            "events": ["impeller_damage"]
          }
        ]
      }
    }
  },
  "logical_groups": [
    {
      "id": "P101_impeller_disposition",
      "operator": "xor",
      "member_task_uids": ["9009", "9010"],
      "when": {
        "source_task_uid": "9004",
        "events": ["impeller_damage"]
      }
    }
  ]
}
```

As formas antigas baseadas em `task ID` continuam aceitas por compatibilidade.
Na carga do sidecar, UID é resolvido para o ID atual do cronograma.

O modelo rejeita duas ambiguidades estruturais:

- IDs duplicados de `logical_groups`;
- uma mesma tarefa pertencendo a mais de um grupo seletivo `XOR/OR`.

## Interface e saída gerencial

A interface Streamlit separa a análise em visão executiva, cronograma, recursos, risco e exportação. O PDF é a saída gerencial para comunicação da parada; o Excel permanece como saída técnica para exploração e auditoria dos dados. A página de scope discovery possui relatório próprio, registrando baseline, novo escopo, mapa de ativação e cronograma reprogramado.

## Por que RCPSP?

Turnarounds são projetos de manutenção de grande escala com precedências fortes, duração curta e recursos limitados. O problema é naturalmente próximo do Resource-Constrained Project Scheduling Problem; variantes de shutdown maintenance também incorporam equipes multi-skill, modos de execução e avaliação de risco de prazo.

## Rodar

Com uv:

```bash
uv sync
uv run streamlit run app.py
```

Ou, com venv tradicional:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
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
- A simulação de risco da página base perturba duração; o **scope discovery discreto** é tratado separadamente na página MRCPSP.

## Arquivos de exemplo

- `sample_data/turnaround_project_model.xml` — cronograma-base MSPDI;
- `sample_data/turnaround_conditional_model.xml` — cenário de inspeção "Kinder Ovo";
- `sample_data/turnaround_conditional_scope.json` — regras de ativação e modos.

## Próxima evolução

O núcleo foi separado da interface para substituir o heurístico por **CP-SAT/MILP** sem refazer a tela. Próximas camadas sugeridas:

- calendários e turnos 24x7;
- multi-skill RCPSP;
- crew sizing e dimensionamento multi-skill;
- custos de overtime e contratação;
- restrições de área, LOTO, acesso, guindaste e simultaneidade;
- buffers por risco e janela P80;
- reexportação compatível com Microsoft Project.


## Deploy na OCI

O repositório inclui um stack de produção com **Docker Compose + Caddy**:

- `Dockerfile` — aplicação Streamlit;
- `compose.yaml` — app + reverse proxy;
- `deploy/oci/Caddyfile` — HTTPS automático e proxy para o Streamlit;
- `.env.example` — domínio do deploy;
- `deploy/oci/README.md` — passo a passo completo para VM Ubuntu na OCI.

Fluxo resumido:

```bash
cp .env.example .env
# edite DOMAIN no .env

docker compose up -d --build
```

A porta 8501 fica somente na rede Docker; exponha publicamente apenas 80/443.
