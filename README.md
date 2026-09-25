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
- atividades `DS-*` realmente criadas durante a execução;
- eventos gerados por inspeções;
- múltiplos achados simultâneos;
- grupos lógicos AND, OR e XOR;
- rescheduling com atividades concluídas/em andamento congeladas;
- stability-aware rescheduling, penalizando mudanças excessivas nos horários de início;
- sidecar JSON para regras que não pertencem ao arquivo do Microsoft Project;
- editor de regras de escopo em formato de planilha, persistido em SQLite.

O parser de XML é o mesmo do MVP base. Não existe um segundo modelo de importação:
o cronograma é lido uma vez e convertido para o domínio avançado somente quando
essa página é usada.

### Regras de escopo em planilha

Além do sidecar JSON, a página avançada possui um editor tabular persistido no
SQLite. O botão **Adicionar regra** cria uma nova linha e o planejador pode
preencher regras sem editar JSON manualmente.

Tipos suportados:

- `conditional`: transforma uma atividade existente em condicional;
- `xor`: exatamente um ramo é escolhido quando o grupo é disparado;
- `or`: um ou mais ramos podem ser escolhidos;
- `and`: todos os membros são ativados quando o grupo é disparado.

As colunas permitem definir atividade-alvo, gatilho, eventos, lógica
`any/all`, membros do grupo, resolução `human/event`, rotas determinísticas
e observações.

As referências são persistidas preferencialmente como `uid:<UID>` do
Microsoft Project. Para grupos, os membros são informados separados por `;`:

```text
uid:9009; uid:9010
```

Para uma regra determinística `event`, as rotas usam:

```text
repairable=>uid:9009 | replacement_required=>uid:9010
```

Grupos criados pela planilha passam a controlar seus membros diretamente; os
membros são tratados como `optional` na ativação-base para que XOR/OR/AND não
sejam anulados por uma atividade originalmente `mandatory`.

Antes de salvar, o sistema valida o projeto completo. IDs duplicados,
referências inexistentes, conflito com grupos do sidecar, duas regras
`conditional` para o mesmo alvo e combinações ambíguas são rejeitados.

As regras são armazenadas em `scope_rule_rows`, vinculadas ao fingerprint do
baseline + sidecar. O JSON existente continua compatível; a planilha funciona
como uma camada adicional.

### Multi-skill workforce (MS-RCPSP)

A página avançada possui a aba **Pessoas** para cadastrar o roster da parada e
classificar quais recursos do cronograma representam habilidades humanas.

O modelo segue a formulação-base do MS-RCPSP:

- uma pessoa pode possuir uma ou mais habilidades;
- cada atividade/modo pode exigir uma quantidade inteira de pessoas por habilidade;
- o scheduler escolhe quais pessoas atendem cada vaga;
- uma mesma pessoa não pode ocupar duas vagas simultâneas, mesmo sendo multi-skill;
- recursos não humanos (guindaste, munck, ferramenta especial etc.) continuam
  usando capacidade agregada normal;
- atividades em andamento congelam também as pessoas já alocadas.

Exemplo: se Ana possui `Mecânica; Soldagem`, ela conta como qualificada para
ambas, mas **não** permite executar simultaneamente uma atividade que exige
1 Mecânica e outra que exige 1 Soldagem sem uma segunda pessoa disponível.

Nesta primeira versão a habilidade é binária (possui/não possui). Níveis de
proficiência, produtividade, aprendizagem e calendários individuais são
extensões futuras e não são inferidos pelo scheduler.

### Dynamic scope discovery

O framework diferencia dois tipos de mudança de escopo durante a parada:

- **escopo condicional pré-modelado**: atividades potenciais já existiam no
  sidecar e são ativadas quando um gatilho ocorre;
- **escopo descoberto dinamicamente**: a atividade não existia no XML nem no
  sidecar e é criada durante a execução a partir de um achado de campo.

Uma atividade dinâmica recebe um ID estável da sessão (`DS-001`, `DS-002`,
...), hora do achado, origem opcional, duração, recursos, custo, predecessoras e
atividades futuras que ela deve bloquear.

Exemplo conceitual:

```text
Inspecionar carcaça
        ↓
achado não previsto: trinca
        ↓
DS-001 · Reparar trinca
  Soldador:1
  Mecânica:1
  4 h
        ↓
Fechar equipamento
```

A materialização não modifica o cronograma-base. O projeto efetivo é construído
em memória com a nova atividade como `mandatory` e, quando necessário, são
injetadas precedências FS nas sucessoras indicadas. A hora do achado vira
`release_time`, portanto a atividade nunca pode ser programada antes de ter
sido descoberta. O rescheduling então trata a atividade nova junto com o
restante do escopo ativo.

Se a descoberta introduzir um recurso que não existia na Resource Sheet, por
exemplo `Soldador`, o catálogo dinâmico de recursos passa a exibi-lo
automaticamente com capacidade-base não informada. A tarefa permanece inviável
até que o planejador informe capacidade no cenário.

Atividades criadas por dynamic scope discovery não recebem penalidade de
estabilidade, porque não possuíam horário de início no plano anterior. As
atividades futuras já planejadas continuam sujeitas ao stability-aware
rescheduling.

As descobertas e o estado operacional da execução são persistidos em SQLite.
O baseline importado permanece imutável; o projeto efetivo continua sendo
reconstruído a partir do baseline + regras + eventos + atividades `DS-*`.

### Persistência da execução — SQLite + SQLAlchemy + Alembic

Por padrão, a aplicação usa:

```text
data/turnaround.db
```

O SQLite roda com:

```text
PRAGMA journal_mode=WAL
PRAGMA foreign_keys=ON
PRAGMA synchronous=NORMAL
```

A camada SQLAlchemy persiste:

- sessões de execução por baseline;
- hora corrente da parada;
- eventos/achados observados;
- decisões humanas já resolvidas;
- regras de escopo criadas pelo editor tabular;
- atividades `DS-*`;
- recursos, predecessoras e sucessoras das atividades descobertas;
- event log append-only para criação/remoção de `DS-*` e mudanças de estado.

O schema é versionado por Alembic. Localmente:

```bash
uv run alembic upgrade head
```

No deploy da VPS, `scripts/deploy_vps.sh` executa a migração antes de reiniciar
o serviço Streamlit.

Para usar outro arquivo SQLite ou migrar futuramente para outro banco compatível
com SQLAlchemy, defina:

```bash
export TURNAROUND_DATABASE_URL="sqlite:////caminho/turnaround.db"
```

O banco não é versionado pelo Git; arquivos `data/*.db*` são ignorados.

### Stability-aware rescheduling

Quando o plano precisa ser refeito durante a execução, o solver pode considerar
não apenas o makespan, mas também quanto o novo cronograma se afasta dos inícios
previamente planejados.

Para cada atividade futura já existente no plano anterior:

```text
desvio_j = |novo_inicio_j - inicio_planejado_j|
```

O objetivo stability-aware usado na seleção de cronogramas é:

```text
atraso à deadline
    ↓
makespan + λ × soma(desvio_j)
    ↓
maior desvio individual
    ↓
custo
```

`λ=0` reproduz o comportamento anterior. Com `λ>0`, duas soluções de
desempenho temporal semelhante passam a favorecer a que exige menos mudanças no
plano comunicado às equipes.

Atividades descobertas durante a parada não recebem penalidade de estabilidade,
pois não possuíam horário de início no plano anterior. Atividades concluídas ou
em andamento continuam congeladas pelo rescheduling.

A página avançada mostra a soma dos deslocamentos, o maior deslocamento,
quantas atividades foram comparadas e o peso `λ`; essas métricas também entram
no relatório gerencial PDF e nos cenários salvos da sessão.

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

### Decisões de escopo sob demanda

Grupos lógicos não geram controles permanentes na interface. Eles ficam
dormentes até que a condição `when` seja satisfeita. Nesse momento, o motor de
decisão avalia cenários sombra e aplica uma das três políticas:

- `human` (padrão): calcula o impacto de cada alternativa e mostra somente a
  decisão ativa para julgamento humano. Makespan, atraso, custo e recursos são
  informações de apoio; a ferramenta não seleciona a ação técnica;
- `event`: reservado a regras determinísticas previamente definidas, nas
  quais o próprio evento já determina qual tarefa entra no escopo por
  `event_routes`.

Exemplo de decisão humana:

```json
{
  "id": "P101_impeller_disposition",
  "operator": "xor",
  "resolution_mode": "human",
  "member_task_uids": ["9009", "9010"],
  "when": {
    "source_task_uid": "9004",
    "events": ["impeller_damage"]
  }
}
```

Exemplo event-driven com referências estáveis:

```json
{
  "id": "P102_motor_disposition",
  "operator": "xor",
  "resolution_mode": "event",
  "member_task_uids": ["9101", "9102"],
  "when": {
    "source_task_uid": "9100",
    "events": ["repairable", "replacement_required"]
  },
  "event_route_uids": {
    "repairable": ["9101"],
    "replacement_required": ["9102"]
  }
}
```

A avaliação é lazy/rolling-horizon: grupos cujo gatilho ainda não ocorreu não
são simulados. Para grupos `OR` grandes, a análise usa um conjunto limitado de
candidatos em vez de enumerar todas as `2^n - 1` combinações.

O scheduler nunca escolhe entre alternativas técnicas como reparar, substituir
ou recuperar com base em prazo/custo. Se uma alternativa exigir um recurso sem
capacidade disponível, o cenário sombra é marcado como inviável e a interface
expõe explicitamente o déficit de recurso.

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
- A persistência operacional usa SQLite, adequada ao deploy atual em uma única VPS; concorrência multiusuário intensa exigirá evolução para Postgres.
- Ainda não há calendário por turno, folga de refeição, indisponibilidade individual ou overtime.
- O CPM exibido é uma aproximação de folga para vínculos complexos; o agendador respeita FS/SS/FF/SF e lag na programação.
- Recursos do Excel/CSV usam demanda unitária por padrão; a coluna `Demandas` permite sobrescrever.
- Não lê `.mpp` nativo. XML é preferido porque evita dependência Java/MPXJ.
- A simulação de risco da página base perturba duração; o **scope discovery discreto** é tratado separadamente na página MRCPSP.

## Arquivos de exemplo

- `sample_data/turnaround_project_model.xml` — cronograma-base MSPDI;
- `sample_data/turnaround_conditional_model.xml` — cenário de inspeção "Kinder Ovo";
- `sample_data/turnaround_conditional_scope.json` — regras de ativação e modos.

No cenário **Kinder Ovo**, há agora duas fontes independentes de scope discovery:

- inspeção mecânica da P-101: rolamento, selo, eixo e impelidor;
- ensaio do motor elétrico da P-101: usa 2 eletricistas por 2 h e pode gerar
  `motor_replacement_required`.

### Catálogo dinâmico de recursos

A página avançada não depende de uma lista fixa de profissões/equipamentos. O
catálogo usado para gerar os sliders é a união de:

- capacidades declaradas no Microsoft Project / sidecar;
- recursos demandados pelas atividades;
- recursos demandados por qualquer modo MRCPSP.

Assim, um modo que use `Caldeiraria`, `Soldagem`, `Munck` ou outro recurso
novo faz esse recurso aparecer automaticamente na interface.

Se o recurso for demandado mas não tiver capacidade-base declarada, o sistema
**não infere capacidade a partir da demanda**. Ele mostra:

- capacidade-base: não informada;
- maior demanda observada;
- capacidade do cenário iniciando em 0.

O planejador precisa informar explicitamente a capacidade do cenário para que
tarefas/modos dependentes daquele recurso possam se tornar factíveis.

Se a substituição do motor for ativada, entram 6 h de trabalho com
`Elétrica:2`, `Mecânica:2` e `Guindaste:1`. O fechamento/alinhamento da
bomba só é liberado depois dessa atividade quando ela pertence ao escopo ativo.

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
