# Manual de Uso — Escopo e Replanejamento

Este manual descreve o uso da página **Escopo e Replanejamento** do Turnaround Decision Support.

> A interface não mantém mais uma aba **Manual**. A ajuda curta aparece dentro de
> **Operação**, **Escopo**, **Recursos** e **Governança**; este documento
> permanece como referência técnica completa.

A funcionalidade foi criada para tratar um problema típico de paradas de manutenção: o planejamento começa com um escopo conhecido, mas novas atividades podem surgir somente depois da abertura e inspeção do equipamento.

---

## 1. Objetivo

A página permite:

- continuar a execução a partir de um baseline aprovado na guia Planejamento;
- carregar diretamente um cronograma-base do Microsoft Project quando não houver baseline aprovado;
- acrescentar regras de escopo por meio de um JSON;
- tratar atividades obrigatórias, opcionais e condicionais;
- registrar eventos e achados de inspeção;
- trabalhar com decisões lógicas AND, OR e XOR;
- alterar capacidades de recursos;
- avaliar modos alternativos de execução;
- congelar atividades já concluídas ou em andamento;
- reprogramar apenas o trabalho futuro;
- comparar baseline, novo escopo, prazo e custo;
- exportar um relatório gerencial em PDF.

O fluxo geral é:

```text
Planejamento-base
      ↓
Aprovação do baseline RCPSP
      ↓
Execução da parada
      ↓
Inspeção
      ↓
Achado
      ↓
Novo escopo ativado
      ↓
MRCPSP
      ↓
Reprogramação
      ↓
Avaliação de prazo, recursos e custo
```

---

## 2. Replanejamento e revisão formal da linha de base

O cronograma pode ser replanejado quantas vezes forem necessárias sem alterar a
linha de base formal.

A página distingue:

- **Baseline original**: plano aprovado antes da execução;
- **Baseline vigente**: original ou última `Rev.n` formalmente aprovada;
- **Forecast atual**: cronograma produzido pelo estado corrente da parada.

Na aba **Governança**, compare primeiro a **Baseline original**, a
**Baseline vigente** e o **Forecast atual**. Use **Promover este replanejamento a
nova linha de base** somente quando a mudança de compromisso tiver sido
formalmente aprovada. A promoção registra uma revisão imutável, de `Rev.1` a
`Rev.10`, com motivo, janela aprovada, snapshot, cronograma, makespan e
metadados de aprovação. O controle de promoção não fica mais na área operacional.

Depois da promoção, o replanejamento passa a usar os horários da revisão como
referência de estabilidade. A baseline original não é apagada: o relatório
continua mostrando simultaneamente **Δ vs original** e **Δ vs vigente**.

Cada nova `Rev.n` exige um aprovador e registra também a referência formal que
estava vigente antes da promoção, o makespan/janela anteriores, o snapshot
promovido e os deltas **vs anterior** e **vs Original**. A aba **Governança**
mostra a cadeia `Original → Rev.1 → Rev.2...` para permitir auditoria do
histórico de compromisso. Revisões antigas continuam compatíveis mesmo quando
não possuem esses campos adicionais.

No Microsoft Project XML exportado:

- `Baseline` / `Number=0` representa o plano original;
- `Baseline1` … `Baseline10` representam as revisões formais;
- `Start` e `Finish` representam o forecast operacional mais recente;
- `ActualStart` e `ActualFinish` representam o que já foi executado.

---

## 3. Conceito do cenário “Kinder Ovo”

O cenário demonstrativo representa uma bomba P-101.

Inicialmente, sabe-se que será necessário:

```text
Parada operacional
      ↓
LOTO e liberação
      ↓
Abrir P-101
      ↓
Inspecionar P-101
```

Entretanto, apenas após a inspeção será possível saber se será necessário:

- trocar rolamentos;
- trocar o selo mecânico;
- reparar o eixo;
- executar END após reparo do eixo;
- recuperar o impelidor;
- substituir o impelidor.

O nome **Kinder Ovo** representa exatamente essa dinâmica: parte do escopo só é descoberta depois que o equipamento é aberto.

---

## 4. Tipos de atividade

O sistema trabalha com três tipos principais.

| Tipo | Significado |
|---|---|
| `mandatory` | atividade obrigatória desde o início |
| `optional` | atividade incluída apenas se o usuário selecionar |
| `conditional` | atividade ativada somente quando uma condição/evento ocorrer |

### Exemplo

```text
mandatory
LOTO e liberação

conditional
Trocar rolamentos somente se houver bearing_damage

optional
Executar uma atividade oportunística selecionada pelo usuário
```

---

## 5. Entrada do baseline e regras

O caminho preferencial é aprovar o cenário na guia **Planejamento**. Esse
snapshot é persistido no SQLite com tarefas, precedências, capacidades,
deadline e horários RCPSP. Ao abrir **Escopo e Replanejamento**, selecione
**Baseline aprovado** e continue a execução sem reenviar o arquivo.

Quando a execução não passou pela guia Planejamento, o XML continua disponível
como entrada direta.

### 5.1 Microsoft Project XML

O XML contém a estrutura principal do cronograma:

- ID e UID;
- nome da atividade;
- duração;
- WBS/EDT;
- predecessoras;
- tipo de vínculo;
- lag;
- recursos;
- unidades/demanda de recursos.

Na interface, escolha **Importar XML** como origem do baseline e envie o
arquivo. O formato recomendado é XML exportado pelo Microsoft Project.

Quando a origem for **Baseline aprovado**, o mesmo modelo normalizado já foi
persistido pela guia Planejamento e o upload não é necessário.

### 5.2 JSON de regras de escopo

O JSON adiciona informações que não pertencem naturalmente ao cronograma tradicional:

- deadline;
- atividades condicionais;
- atividades opcionais;
- eventos gatilho;
- modos alternativos de execução;
- custo dos modos;
- grupos lógicos AND, OR e XOR.

Na interface:

**Regras de escopo / modos (JSON) → Upload**

Conceitualmente:

```text
XML
├── estrutura do cronograma
├── duração
├── precedências
└── recursos

JSON
├── quando executar
├── se executar
├── modos possíveis
├── custo
└── regras de decisão
```

---

## 5. Usando o cenário demonstrativo

Para aprender a ferramenta, marque:

**Usar cenário demonstrativo 'Kinder Ovo'**

O sistema carrega automaticamente:

```text
sample_data/turnaround_conditional_model.xml
sample_data/turnaround_conditional_scope.json
```

Assim, não é necessário fazer upload de arquivos para testar a lógica.

---

## 6. Etapa 1 — Planejamento-base e regras de escopo

Depois de carregar os arquivos, o sistema monta o projeto e determina quais atividades estão ativas no baseline.

As atividades condicionais não entram automaticamente como trabalho obrigatório.

Por exemplo, antes dos achados da inspeção, atividades como:

```text
Trocar rolamentos P-101
Trocar selo mecânico P-101
Reparar eixo P-101
END pós-reparo do eixo
Recuperar impelidor
Substituir impelidor
```

podem permanecer inativas ou pendentes.

Isso diferencia o modelo de um RCPSP convencional em que todo o escopo já é conhecido.

---

## 7. Etapa 2 — Capacidade de recursos

Na aba **Recursos**, a interface apresenta sliders para os recursos disponíveis. No mesmo domínio ficam o roster, as habilidades humanas, o multi-skill e os indicadores de pico/utilização do cenário.

No cenário demonstrativo aparecem recursos como:

- Guindaste;
- Inspeção;
- Mecânica;
- Operação.

Ao mover um slider, o Streamlit recalcula a página automaticamente.

Não existe botão **Calcular**.

O fluxo é:

```text
Alterar capacidade
      ↓
Streamlit reroda
      ↓
Modos factíveis são reavaliados
      ↓
MRCPSP é executado novamente
      ↓
Cronograma é atualizado
```

### Importante

Aumentar a quantidade de recursos não reduz necessariamente o prazo.

Se duas atividades possuem dependência direta:

```text
A → B
```

B não poderá iniciar antes de A apenas porque existem mais recursos disponíveis.

O ganho aparece quando:

- atividades podem ocorrer em paralelo;
- existe disputa por um mesmo recurso;
- um modo mais rápido exige maior capacidade.

---

## 8. Modos de execução

Uma atividade pode possuir mais de um modo de execução.

### Exemplo — troca de rolamentos

| Modo | Duração | Mecânica | Custo |
|---|---:|---:|---:|
| normal | 5 h | 3 | 0 |
| reforco | 3 h | 5 | 1200 |

Se a capacidade de Mecânica for 3, o modo `reforco` é inviável.

Se a capacidade for 5 ou superior, ambos os modos tornam-se candidatos.

### Exemplo — reparo do eixo

| Modo | Duração | Mecânica | Custo |
|---|---:|---:|---:|
| normal | 7 h | 3 | 0 |
| ataque | 4 h | 5 | 2500 |

---

## 9. Critério atual de escolha dos modos

Hoje o solver compara os resultados nesta ordem:

```text
1. menor atraso
2. menor makespan
3. menor custo
```

Internamente, a pontuação é:

```text
(tardiness, makespan, cost)
```

Portanto, um modo mais caro pode ser escolhido se produzir menor atraso ou menor makespan.

O modelo atual está orientado prioritariamente para o cumprimento da janela da parada.

---

## 10. Indicadores do baseline

A tela apresenta quatro indicadores principais.

### Makespan base

Tempo total previsto para o escopo atualmente conhecido.

### Deadline

Janela máxima definida no JSON.

### Tarefas ativas na base

Quantidade de atividades que pertencem ao escopo atual do baseline.

### Escopo potencial

Quantidade de atividades existentes no projeto que ainda podem entrar no escopo.

**Escopo potencial não significa que todas essas atividades serão executadas.**

---

## 11. Etapa 3 — Hora corrente

Na seção **Estado da parada e achados**, informe:

**Hora corrente desde o início da parada**

Exemplo:

```text
7.0 h
```

O sistema compara a hora atual com o cronograma-base e identifica automaticamente atividades:

- concluídas;
- em andamento;
- não iniciadas.

---

## 12. Congelamento do trabalho já executado

Atividades concluídas ou em andamento não são reprogramadas como se a parada estivesse começando novamente.

Elas são congeladas.

```text
PASSADO             AGORA                 FUTURO

███████████████████ │ ─────────────────────────
      congelado      │      reprogramável
```

O replanejamento atua apenas sobre o trabalho futuro.

---

## 13. Registrando achados da inspeção

Quando uma atividade gatilho estiver concluída, a interface habilita seus eventos possíveis.

No cenário Kinder Ovo, após a conclusão de:

```text
Inspecionar P-101
```

podem ser registrados eventos como:

```text
bearing_damage
seal_damage
shaft_damage
impeller_damage
```

A seleção desses eventos altera o escopo.

---

## 14. Exemplo — dano no rolamento

Ao selecionar:

```text
bearing_damage
```

a atividade:

```text
Trocar rolamentos P-101
```

é ativada.

O solver então recalcula:

- cronograma;
- modo de execução;
- recursos;
- custo;
- makespan;
- atraso.

---

## 15. Exemplo — dano no eixo

Ao selecionar:

```text
shaft_damage
```

o sistema ativa:

```text
Reparar eixo P-101
        ↓
END pós-reparo do eixo
```

Esse é um exemplo de escopo condicional encadeado.

---

## 16. Exemplo — dano no impelidor

Ao selecionar:

```text
impeller_damage
```

o sistema habilita uma decisão lógica do tipo XOR:

```text
            ┌── Recuperar impelidor
Dano ───────┤
            └── Substituir impelidor
```

O usuário deve escolher exatamente uma alternativa.

Na interface aparece o grupo:

```text
Grupo XOR · impeller_disposition
```

---

## 17. Grupos lógicos AND, OR e XOR

### AND

Todas as atividades do grupo são ativadas.

```text
Achado
  ↓
┌─────┬─────┬─────┐
A     B     C
```

### OR

Uma ou mais atividades podem ser selecionadas.

Exemplos válidos:

```text
A
A+B
B+C
A+B+C
```

### XOR

Exatamente uma atividade deve ser selecionada.

```text
A OU B
```

---

## 18. Estados do mapa de ativação

Na aba **Mapa de ativação**, cada atividade possui um estado.

### `active`

A atividade faz parte do escopo atual.

Pode ocorrer porque:

- é obrigatória;
- foi selecionada;
- uma condição foi satisfeita;
- um grupo lógico a ativou;
- já está em execução;
- já foi concluída.

### `inactive`

A atividade não faz parte do escopo atual.

Exemplo:

```text
não houve seal_damage
↓
Trocar selo mecânico permanece inativa
```

### `pending`

Ainda não existe informação suficiente para decidir.

Exemplo:

```text
Inspeção ainda não concluída
↓
Atividades dependentes do achado permanecem pendentes
```

---

## 19. Campo “Motivo”

O mapa de ativação também apresenta a justificativa do estado.

Exemplos:

```text
atividade obrigatória
atividade opcional selecionada
atividade opcional não selecionada
condição de ativação satisfeita
aguardando atividade/evento gatilho
aguardando seleção XOR do grupo
selecionada pelo grupo XOR
```

Esse campo ajuda na rastreabilidade da decisão.

---

## 20. Atividades opcionais

Atividades `optional` não dependem obrigatoriamente de eventos.

A interface apresenta:

**Atividades opcionais selecionadas**

O usuário pode decidir incluí-las no cenário.

Exemplo conceitual:

```text
Aproveitar a parada para executar uma manutenção oportunística
```

---

## 21. Etapa 4 — Replanejamento

Depois de registrar:

- hora atual;
- achados;
- atividades opcionais;
- decisões OR/XOR;
- recursos disponíveis;

o sistema executa o replanejamento.

O resultado combina:

```text
atividades congeladas
+
novo escopo
+
trabalho futuro já previsto
+
restrições de recursos
+
modos de execução
```

### Preservação do plano

Em **Operação → Preferências do replanejamento**, escolha quanto o
replanejamento deve preservar os horários já comunicados para o trabalho futuro:

- **Baixa**: aceita mais rearranjo em busca de prazo;
- **Balanceada**: compromisso padrão entre prazo e estabilidade;
- **Alta**: penaliza mais mudanças nos horários já planejados.

O valor matemático λ fica escondido por padrão. Marque **Mostrar configuração
avançada de estabilidade** somente para estudos ou calibração.

---

## 22. Visão executiva

A aba **Visão executiva** apresenta os principais indicadores.

### Novo makespan

Duração do cronograma após o novo escopo.

Também é exibida a diferença para o baseline.

### Atraso

Quanto o cronograma ultrapassa o deadline.

```text
Deadline = 24 h
Makespan = 27 h

Atraso = 3 h
```

### Novas tarefas ativas

Quantidade de atividades que entraram no escopo em relação ao baseline.

### Custo dos modos

Soma do custo associado aos modos escolhidos pelo solver.

---

## 23. Mensagens gerenciais

Quando o novo cronograma permanece dentro da janela:

**Scope discovery absorvido.**

Quando ultrapassa:

**Intervenção gerencial necessária.**

O sistema mostra o impacto, mas não toma a decisão gerencial pelo usuário.

Possíveis respostas operacionais incluem:

- aumentar recursos;
- utilizar modos mais rápidos;
- rever sequenciamento;
- retirar trabalho oportunístico;
- rever a janela da parada.

---

## 24. Gantt reprogramado

O gráfico apresenta o cronograma considerando o estado atual.

A linha:

```text
agora
```

representa a hora corrente.

Quando existe deadline, também aparece:

```text
deadline
```

O Gantt permite visualizar:

- atividades já executadas;
- atividades em andamento;
- novo escopo;
- reprogramação futura;
- eventual ultrapassagem da janela.

---

## 24.1 Ordem visual das atividades

O Discovery preserva a ordem original das atividades importadas do Microsoft Project para a leitura da tabela, do Gantt e do PDF. Essa ordem é apenas de apresentação.

O solver continua livre para calcular a sequência real de execução com base em precedências, recursos, modos e horário corrente. Portanto:

```text
ordem visual = Project / WBS / posição original
ordem de execução = resultado do MRCPSP
```

Isso evita que uma atividade “mude de linha” no Gantt apenas porque começou mais cedo ou mais tarde após o replanejamento.

## 25. Aba Cronograma

A tabela detalhada apresenta:

| Campo | Significado |
|---|---|
| ID | identificação da atividade |
| Atividade | nome da atividade |
| Modo | modo escolhido |
| Início | hora prevista de início |
| Fim | hora prevista de término |
| Duração | duração do modo |
| Congelada | indica atividade já iniciada/concluída |
| Recursos | demandas de recursos |

---


## 25.1 Caminho crítico efetivo do discovery

Após o scope discovery, a página também identifica a **cadeia controladora atual** do cronograma reprogramado.

Ela não deve ser confundida com o caminho crítico CPM do planejamento-base. A análise considera o cronograma efetivamente encontrado pelo MRCPSP e procura os vínculos que estão controlando o término naquele instante:

- precedências ativas;
- gates de precedência introduzidos por novo escopo descoberto após atividades já congeladas;
- liberações de recursos que impedem uma atividade de começar antes;
- atividade(s) que definem o makespan atual.

As atividades dessa cadeia aparecem destacadas no Gantt e recebem, na tabela do cronograma, as colunas:

| Campo | Significado |
|---|---|
| `Crítica atual` | indica se a atividade pertence à cadeia efetiva que controla o término |
| `Controla por` | informa se o driver é precedência, gate de escopo, recurso ou término do cronograma |

Exemplo conceitual:

```text
Reparar eixo
   ↓  liberação de Mecânica
Substituir impelidor
   ↓  gate do novo escopo
Teste funcional e partida
   ↓
makespan atual
```

O objetivo é responder não apenas **quanto o novo escopo alterou a parada**, mas também **o que agora controla o seu término e onde uma intervenção pode recuperar prazo**.


## 26. Estratégia do solver

Para problemas menores, o solver utiliza:

```text
enumeration+ssgs
```

Ele avalia as combinações de modos factíveis e diferentes regras de prioridade.

Quando o número de combinações ultrapassa o limite configurado, utiliza:

```text
multistart-local+ssgs
```

Nesse caso, passa a utilizar busca heurística.

O mecanismo atual é uma ferramenta de apoio à decisão e não constitui prova de ótimo global.

---

## 27. Exportação em PDF

Na aba **Exportação** existe:

**Baixar relatório gerencial em PDF**

O relatório registra uma fotografia do momento do replanejamento, incluindo:

- baseline;
- hora atual;
- novo escopo;
- makespan atualizado;
- deadline;
- custo dos modos;
- mapa de ativação;
- cronograma reprogramado.

---

## 28. Roteiro recomendado para testar o Kinder Ovo

1. Ative o cenário **Kinder Ovo**.
2. Observe o baseline.
3. Avance a **Hora corrente** até a atividade `Inspecionar P-101` estar concluída.
4. Selecione apenas `bearing_damage`.
5. Observe a entrada de `Trocar rolamentos P-101`.
6. Altere a capacidade de Mecânica.
7. Observe se novos modos passam a ser factíveis.
8. Adicione `shaft_damage`.
9. Observe a entrada de `Reparar eixo` e `END pós-reparo`.
10. Adicione `impeller_damage`.
11. Escolha `Recuperar impelidor` ou `Substituir impelidor` no grupo XOR.
12. Compare makespan, atraso, custo e Gantt.
13. Gere o relatório PDF.

---

## 29. Regra de ouro

Não confunda:

```text
ATIVIDADE CONDICIONAL
“Só executo se determinada condição acontecer.”

ATIVIDADE OPCIONAL
“Posso decidir aproveitar a parada para executar.”

MODO DE EXECUÇÃO
“A atividade vai acontecer; preciso decidir COMO executá-la.”
```

Exemplo:

```text
shaft_damage
      ↓
Reparar eixo
      ↓
┌───────────────────────────┐
│ normal: 7 h / 3 mecânicos │
│ ataque: 4 h / 5 mecânicos │
└───────────────────────────┘
```

---

## 30. Limitações atuais

A funcionalidade ainda não pretende representar todos os detalhes de uma parada industrial.

Entre as limitações atuais:

- calendários por turno;
- pausas de refeição;
- indisponibilidade individual;
- overtime;
- dimensionamento multi-skill completo;
- otimização global exata;
- restrições avançadas de área e simultaneidade.

A página deve ser entendida atualmente como:

> **Ferramenta de apoio ao replanejamento e à análise de cenários de scope discovery durante turnarounds.**

Ela não substitui, neste estágio, Microsoft Project, Primavera ou sistemas completos de controle da execução.
