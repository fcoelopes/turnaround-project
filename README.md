# Turnaround Decision Support

Aplicação para receber um cronograma exportado do Microsoft Project e aplicar um modelo inicial de **turnaround/shutdown scheduling**.

## O que o MVP faz

1. Importa **Microsoft Project XML**, Excel ou CSV.
2. Normaliza atividades, durações, predecessoras, recursos e WBS/EDT.
3. Calcula uma referência CPM sem limitação de recursos.
4. Gera cronograma factível por recursos com um **RCPSP heurístico** (Serial Schedule Generation Scheme).
5. Testa quatro regras de prioridade e escolhe o menor atraso/makespan.
6. Mostra Gantt, gargalos, utilização dos recursos e penalidade de capacidade.
7. Faz Monte Carlo combinado de **incerteza de duração + ampliação probabilística de escopo**, calculando P50/P80/P90, probabilidade de cumprir a janela e impacto incremental do escopo potencial.
8. Exporta os dados técnicos para Excel.
9. Gera um **relatório gerencial em PDF** com KPIs, gargalos, risco e cronograma.
10. Permite **aprovar o cenário RCPSP como baseline de execução** e continuar na página avançada sem novo upload.
11. Em uma página avançada, trata **MRCPSP**, escopo opcional/condicional e rescheduling após inspeções.
12. Exporta o snapshot operacional replanejado em **Microsoft Project XML (MSPDI)**, materializando condicionais ativadas, atividades `DS-*`, precedências, recursos e novos horários.

## Dois níveis de planejamento

### Planejamento-base — RCPSP

A página principal mantém o MVP original: CPM + RCPSP heurístico + risco de prazo.
É apropriada quando o escopo já é conhecido e cada atividade possui um único modo
de execução.

### Risco combinado no Planejamento

A aba **Risco** é o único contexto de configuração e leitura das incertezas no
Planejamento. No mesmo lugar ficam:

- número de simulações Monte Carlo;
- parâmetros otimista, mais provável e pessimista da distribuição triangular;
- atividades de escopo potencial, gatilhos, eventos e probabilidades;
- Média, P50, P80, P90 e probabilidade de cumprir a janela;
- histograma do makespan;
- decomposição **P80 somente duração × P80 duração + escopo**;
- impacto médio/P80 da ampliação de escopo;
- direcionadores de risco ordenados pelo impacto marginal médio.

A guia **Planejamento** distingue o escopo-base das atividades que podem entrar
durante a parada. Atividades de escopo potencial podem ser associadas a:

- atividade gatilho;
- nome do evento;
- probabilidade do evento no planejamento.

Em cada iteração do Monte Carlo, o sistema:

1. sorteia a ocorrência dos eventos de escopo;
2. materializa somente as atividades potenciais cujos eventos ocorreram;
3. sorteia as durações das atividades ativas por distribuição triangular;
4. executa novamente o RCPSP com as mesmas capacidades;
5. registra makespan, cumprimento da janela e impacto incremental do novo escopo.

Eventos iguais (mesmo gatilho + nome) são sorteados uma única vez, mesmo quando
ativam mais de uma atividade, evitando tratar uma mesma ocorrência física como
eventos independentes.

O sistema também executa um cenário pareado de **duração apenas** usando os
mesmos fatores aleatórios. Isso permite separar:

~~~text
P80 combinado
= incerteza de duração
+ risco probabilístico de ampliação do escopo
~~~

e reportar o impacto médio/P80 do escopo, além da frequência simulada de cada
evento.

CSV/Excel podem pré-cadastrar candidatos usando colunas opcionais como
`Tipo_escopo`, `Gatilho_ID`, `Evento_sugerido` e `Probabilidade_%`.
A probabilidade pertence ao planejamento; depois que o baseline é aprovado, a
mesma relação gatilho/evento segue para **Escopo e Replanejamento**, onde a
ativação passa a depender do evento realmente observado, não da probabilidade.

### Status executivo baseado em risco

A leitura gerencial separa três coisas que não devem ser confundidas:

- **Determinístico**: verifica se o cronograma-base cabe na janela;
- **Risco probabilístico**: é considerado controlado somente quando o **P80 cabe
  na janela** e **P(cumprir janela) ≥ 80%**;
- **Status geral**: combina as duas leituras e nunca fica verde apenas porque o
  cronograma determinístico cabe no prazo.

Exemplo:

~~~text
Determinístico: DENTRO
P80: FORA
P(janela): 42%

Status geral: RISCO DE PRAZO
~~~

A classificação usada na interface e no PDF é:

~~~text
sem janela
→ JANELA NÃO DEFINIDA

determinístico fora da janela
→ FORA DA JANELA

determinístico dentro + risco ausente
→ RISCO NÃO AVALIADO

determinístico dentro + (P80 fora OU P(janela) < 80%)
→ RISCO DE PRAZO

determinístico dentro + P80 dentro + P(janela) ≥ 80%
→ DENTRO COM CONFIANÇA P80
~~~

O limiar de 80% é coerente com a leitura gerencial por P80 e funciona como
critério explícito de confiança, não como decisão automática de aprovação.

### Ponte Planejamento → Execução

A página **Planejamento** não é mais um fluxo isolado. Depois de analisar
capacidade, makespan, CPM e risco, o planejador pode usar **Aprovar como baseline
da execução**.

Antes dessa aprovação, as capacidades recebem uma origem explícita:

- `PROJECT`: capacidade importada explicitamente do XML e mantida no cenário;
- `INFORMADA`: capacidade alterada/informada pelo planejador para o cenário;
- `INFERIDA`: fallback derivado das demandas das atividades quando o arquivo não
  trouxe uma capacidade explícita.

Capacidade `INFERIDA` pode ser usada para explorar cenários, mas não é tratada
como disponibilidade operacional confirmada. Se o cenário ainda contiver algum
valor `INFERIDA`, a interface mostra um alerta e exige confirmação explícita
antes de liberar a aprovação do baseline. A origem e os valores aprovados são
persistidos junto com o snapshot e também aparecem no relatório gerencial.

A aprovação da **Baseline 0** é formal. Antes de liberar o plano para execução,
a aplicação exige:

- nome do cenário;
- aprovador;
- motivo / observação da aprovação.

A data/hora é registrada automaticamente. Esses metadados ficam persistidos
junto do snapshot e aparecem no relatório gerencial.

A aprovação grava no SQLite um snapshot imutável do cenário escolhido:

- tarefas e precedências normalizadas;
- capacidades aprovadas e sua origem;
- janela/deadline;
- cronograma RCPSP escolhido, com início e término de cada atividade;
- regra heurística vencedora;
- premissas Monte Carlo (nº de simulações, otimista, mais provável e pessimista);
- probabilidades dos eventos de escopo;
- P80 e probabilidade de cumprir a janela, quando disponíveis;
- nome do cenário, aprovador, motivo e timestamp formal da Baseline 0.

A página **Escopo e Replanejamento** oferece esse baseline aprovado como fonte
preferencial. Quando o modelo avançado continua compatível com o plano-base, a
execução começa exatamente nos horários RCPSP aprovados. Se regras de escopo,
modos alternativos ou multi-skill exigirem reconstrução, o MRCPSP pode gerar a
linha operacional, mas os horários aprovados continuam sendo a referência do
**stability-aware rescheduling**.

Cada cenário aprovado recebe um fingerprint SHA-256 determinístico. Reaprovar o
mesmo cenário atualiza o snapshot em vez de criar duplicatas; alterar tarefas,
capacidades, deadline ou o cronograma produz uma nova identidade de baseline.

### Visão executiva da execução

A página **Escopo e Replanejamento** começa agora com uma aba **Visão** somente
para leitura gerencial. Ela não possui controles técnicos: reutiliza o mesmo
snapshot calculado pela Operação e mostra, em um único lugar:

- Baseline original;
- Baseline vigente;
- Forecast atual;
- Δ vs original e Δ vs vigente;
- situação da janela;
- decisões humanas pendentes;
- novo escopo ativo;
- cadeia controladora do término.

A intenção é responder rapidamente **onde estamos, o que mudou e o que precisa
de atenção**, sem obrigar o gerente a navegar pelos controles de execução,
recursos ou governança. O rebaseline formal continua exclusivo da aba
**Governança**.

### Replanejamento x rebaseline formal

Um replanejamento não cria automaticamente uma nova linha de base.

O sistema mantém três referências distintas:

- **Baseline original** — cenário aprovado na guia Planejamento; permanece imutável;
- **Forecast atual** — resultado mais recente do replanejamento;
- **Baseline vigente** — baseline original ou a última revisão formalmente promovida.

Na guia **Escopo e Replanejamento**, a aba **Governança** concentra a leitura
formal de **Baseline original**, **Baseline vigente**, **Forecast atual**,
**Δ vs original**, **Δ vs vigente**, janela e histórico de revisões. É também o
único lugar onde o usuário pode usar **Promover este replanejamento a nova linha
de base** quando uma mudança de escopo, janela ou compromisso tiver sido
formalmente aprovada. A revisão exige nome, motivo, aprovador, janela aprovada
e confirmação explícita; a observação complementar permanece opcional.

As revisões são imutáveis e numeradas de `Rev.1` a `Rev.10`, acompanhando os
dez slots adicionais de baseline do Microsoft Project. Cada revisão guarda:

- snapshot de origem;
- cronograma materializado;
- makespan e deadline aprovados;
- modos e recursos usados;
- hora corrente;
- motivo, aprovador e observações;
- data/hora da aprovação;
- baseline formal que estava vigente antes da promoção;
- makespan e janela da referência anterior;
- Δ da revisão contra a baseline anterior;
- Δ da revisão contra a Baseline 0.

Novas revisões exigem **aprovador**. A aba **Governança** apresenta a cadeia
`Original → Rev.1 → Rev.2...` e o histórico mostra a referência anterior,
deltas, snapshot e contexto da aprovação. Revisões antigas permanecem legíveis;
quando não possuem os novos campos de auditoria, a interface deriva a cadeia a
partir da ordem histórica.

Depois de uma revisão formal, o stability-aware rescheduling passa a comparar o
trabalho futuro com a **baseline vigente**, enquanto as métricas gerenciais
continuam preservando também a comparação contra a **baseline original**.

Exemplo:

~~~text
Baseline original    88 h
Baseline Rev.1      102 h
Forecast atual      106 h

Δ vs original       +18 h
Δ vs Rev.1           +4 h
Mudança formalizada +14 h
~~~

O histórico completo continua no SQLite; o XML exportado para o Microsoft
Project grava a referência original como `Baseline` (`Number=0`) e as revisões
formais como `Baseline1` … `Baseline10`, quando a atividade fizer parte
daquela revisão.

### Scope discovery — MRCPSP + escopo condicional

A página **Escopo e Replanejamento** acrescenta a dinâmica típica de turnaround:

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

O parser continua sendo uma única fonte de verdade. A página avançada pode
consumir diretamente um baseline aprovado no Planejamento ou, como fallback,
ler um XML do Microsoft Project e convertê-lo para o domínio avançado.

### Aba Escopo\n\nA configuração de lógica de escopo deixou de ficar misturada com preferências\ngenéricas. A aba **Escopo** concentra `conditional`, XOR/OR/AND, gatilhos,\neventos, resolução `human/event`, rotas determinísticas e o editor tabular.\nO JSON continua sendo apenas uma fonte opcional de regras herdadas.\n\n### Regras de escopo em planilha

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

### Onde a decisão associada ao gatilho acontece

A configuração da regra e a resolução da decisão são etapas diferentes.

Na aba **Escopo**, o planejador define a lógica:

- `conditional`: não representa escolha entre alternativas. É uma ativação direta:
  se o gatilho for concluído e o evento ocorrer, a atividade-alvo entra no escopo;
- `xor`: exatamente um ramo deve ser escolhido;
- `or`: uma ou mais alternativas podem ser escolhidas;
- `and`: todos os membros são ativados quando a condição é satisfeita.

Para grupos `xor/or`, a coluna **Resolução** define quem resolve a alternativa:

- `human`: o gatilho + evento apenas abrem a decisão. Na aba **Operação**, depois
  que o achado é registrado, aparece **Decisão de escopo necessária**. A aplicação
  calcula cenários sombra para cada alternativa e mostra factibilidade, makespan,
  atraso, custo, deslocamento do plano e gargalos. A seleção é feita manualmente em
  **Resolver decisão**; o scheduler não escolhe a ação técnica;
- `event`: usado quando o próprio evento já determina objetivamente o ramo. A coluna
  **Rotas event** mapeia evento → atividade(s). Ao registrar o achado na Operação, a
  rota é aplicada automaticamente e aparece em **Regras determinísticas aplicadas**.

Exemplo de decisão humana:

~~~text
Tipo: xor
Gatilho: Inspecionar impelidor
Eventos: impeller_damage
Resolução: human
Membros: Reparar impelidor; Substituir impelidor
~~~

Fluxo:

~~~text
CONFIGURAÇÃO
gatilho + evento + alternativas + modo de resolução
        ↓
OPERAÇÃO
gatilho concluído
        ↓
Registrar achado/evento
        ↓
Resolução = human
        ↓
Decisão de escopo necessária
        ↓
cenários sombra
        ↓
Resolver decisão
        ↓
novo escopo entra no replanejamento
~~~

Exemplo de resolução determinística por evento:

~~~text
Tipo: xor
Gatilho: Ensaiar motor
Eventos: repairable; replacement_required
Resolução: event
Membros: Reparar motor; Substituir motor

Rotas event:
repairable=>Reparar motor |
replacement_required=>Substituir motor
~~~

Nesse segundo caso não existe escolha humana: o evento observado já contém a
regra de roteamento. A aplicação materializa o ramo correspondente e segue para
o replanejamento.

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

### Ajuda contextual na página avançada

A página **Escopo e Replanejamento** não possui mais uma aba exclusiva de manual.
A orientação essencial aparece no próprio contexto:

- **Operação**: fluxo de achados, eventos, decisões humanas e DS-*;
- **Configuração**: `conditional`, XOR/OR/AND, `human` x `event` e rotas;
- **Pessoas**: roster, habilidades e restrição multi-skill;
- **Governança**: Baseline 0, baseline vigente, forecast e rebaseline.

O manual técnico completo continua em
`docs/manual-escopo-condicional.md` para consulta detalhada.

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

- baselines RCPSP aprovados na guia Planejamento;
- revisões formais de baseline (Rev.1 a Rev.10);
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

## Interface e saídas

A interface Streamlit separa a análise em visão executiva, cronograma, recursos, risco e exportação.

Na página base:
- o PDF é a saída gerencial para comunicação do planejamento;
- o Excel permanece como saída técnica para exploração e auditoria.

Na página **Escopo e Replanejamento**:
- o PDF registra baseline, novo escopo, mapa de ativação e cronograma reprogramado;
- o **Microsoft Project XML (MSPDI)** devolve o cronograma operacional ao ambiente de planejamento.

O XML exportado representa o **snapshot materializado atual**. Entram as atividades efetivamente ativas, incluindo condicionais já disparadas e atividades `DS-*` descobertas durante a execução. Também são exportados os horários replanejados, precedências efetivas, recursos, modo escolhido e metadados de rastreabilidade em `Notes`.

Regras condicionais ainda dormentes, grupos XOR/OR/AND e a lógica de decisão não são serializados como regras nativas porque o Microsoft Project não possui esses conceitos. Quando uma regra é resolvida, seu efeito é materializado no cronograma exportado.

## Por que RCPSP?

Turnarounds são projetos de manutenção de grande escala com precedências fortes, duração curta e recursos limitados. O problema é naturalmente próximo do Resource-Constrained Project Scheduling Problem; variantes de shutdown maintenance também incorporam equipes multi-skill, modos de execução e avaliação de risco de prazo.

## Rodar

Com uv:

```bash
uv sync
uv run streamlit run Planejamento.py
```

Ou, com venv tradicional:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
streamlit run Planejamento.py
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
- O parâmetro de horas/dia é apenas um fator de conversão de prazo e indicadores; não representa jornada ou turno no scheduler.
- O CPM exibido é uma aproximação de folga para vínculos complexos; o agendador respeita FS/SS/FF/SF e lag na programação.
- Recursos do Excel/CSV usam demanda unitária por padrão; a coluna `Demandas` permite sobrescrever.
- Não lê `.mpp` nativo. XML é preferido porque evita dependência Java/MPXJ.
- A simulação de risco da página base perturba duração; o **scope discovery discreto** é tratado separadamente na página de Escopo e Replanejamento.

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
- níveis de proficiência e produtividade por habilidade;
- crew sizing e dimensionamento multi-skill;
- custos de overtime e contratação;
- restrições de área, LOTO, acesso, guindaste e simultaneidade;
- buffers por risco e janela P80;
- exportação nativa `.mpp` opcional; o fluxo atual já devolve Microsoft Project XML (MSPDI).


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


## Licença

Este projeto é distribuído sob a **Apache License 2.0**.

Você pode usar, estudar, modificar e redistribuir o código, inclusive em projetos
comerciais, desde que cumpra os termos da licença, preserve os avisos aplicáveis
e indique modificações quando redistribuir arquivos alterados.

Copyright 2026 Edson Lopes.

Consulte o arquivo [LICENSE](LICENSE) para os termos completos.
