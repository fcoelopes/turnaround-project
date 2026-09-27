# Plano de Evolução — Turnaround Decision Support

## 1. Objetivo

Evoluir o Turnaround Decision Support de uma aplicação analítica tecnicamente forte para uma ferramenta com melhor aplicabilidade prática em grandes paradas de manutenção, priorizando:

- UX operacional;
- governança de baseline e rebaseline;
- clareza gerencial;
- fidelidade do calendário;
- validação explícita de capacidades;
- risco integrado de duração + ampliação de escopo;
- rastreabilidade entre planejamento, execução e Microsoft Project.

A orientação deste plano é manter a ferramenta como **camada de apoio à decisão complementar ao Microsoft Project**, e não como substituta do sistema de planejamento.

## 2. Princípios de produto

### 2.1 Planejamento e execução são mundos conectados, mas diferentes

```text
PLANEJAMENTO
    ↓
factibilidade + risco
    ↓
baseline aprovado
    ↓
EXECUÇÃO
    ↓
achados + novo escopo
    ↓
replanejamento
    ↓
rebaseline formal
```

No Planejamento, a ferramenta trabalha com **probabilidades e cenários**.

Na Execução, a ferramenta trabalha com **evidências observadas e estado real**.

### 2.2 A interface deve responder perguntas de decisão

As telas principais devem priorizar cinco perguntas:

1. Estamos dentro da janela?
2. Qual é a confiança de cumprir a janela?
3. O que está controlando o término?
4. O que mudou em relação ao plano aprovado?
5. Que decisão precisa ser tomada agora?

### 2.3 Evitar promessas matemáticas maiores do que o motor entrega

O RCPSP atual é heurístico. Trocar **Gerar plano otimizado** por **Gerar cenário factível** ou **Gerar melhor cenário heurístico**.

A aplicação não deve sugerir prova de ótimo global enquanto usar o scheduler heurístico atual.

---

# 3. Fase P0 — Correções de UX e semântica

## P0.1 Renomear “Horas por dia de parada”

Problema: o controle pode ser interpretado como calendário real de trabalho, embora hoje seja usado principalmente para conversão de horas em dias e deadline.

Trocar para:

```text
Horas consideradas por dia para conversão de prazo
```

Adicionar help:

```text
Este parâmetro converte horas em dias para indicadores e janela.
Ainda não representa calendário real de turnos.
```

### Critérios de aceite

- [x] Nenhuma mensagem da interface sugere que já existe calendário por turno.
- [x] Relatórios deixam claro que a conversão é apenas temporal.
- [x] README e manual usam a mesma terminologia.


**Implementado:** TDS-2.

## P0.2 Renomear o botão de otimização

Trocar **Gerar plano otimizado** por **Gerar cenário factível** ou **Gerar melhor cenário heurístico**.

### Critérios de aceite

- [x] UI não usa “ótimo/otimizado” como resultado garantido.
- [x] Relatório metodológico continua informando uso de RCPSP heurístico.


**Implementado:** TDS-3.

## P0.3 Reduzir a sidebar do Planejamento

A sidebar hoje mistura premissas, prazo, Monte Carlo, capacidade e risco.

### Permanecer na sidebar

- nome/contexto do cenário;
- janela-alvo;
- conversão horas/dia.

### Sair da sidebar

- capacidade de recursos;
- número de simulações;
- otimista/pessimista;
- risco de ampliação de escopo.

---

# 4. Planejamento — Recursos

## P1.1 Aba Recursos como centro das capacidades

Mover os controles de capacidade para a aba **Recursos**.

A aba deve mostrar:

| Recurso | Capacidade-base | Origem | Capacidade cenário | Pico | Utilização |
|---|---:|---|---:|---:|---:|

**TDS-5 implementado — aguardando revisão funcional:** edição de capacidade, leitura de capacidade-base/origem/cenário, pico e utilização foram centralizadas em **Recursos**. Alterar uma premissa invalida o resultado anterior e exige novo cálculo.

**TDS-6 permanece responsável pela governança:** confirmação explícita, alerta de capacidade inferida e bloqueio da aprovação de baseline enquanto houver capacidade não validada.

Cada recurso deve receber uma origem explícita:

```text
PROJECT
INFORMADA
INFERIDA
```

Capacidade inferida não deve parecer validada.

Antes de aprovar baseline:

- [x] todas as capacidades usadas no cenário devem estar confirmadas;
- [x] capacidades inferidas devem aparecer com alerta;
- [x] baseline não deve ser aprovado silenciosamente com capacidade não validada.

**TDS-6 implementado — aguardando revisão funcional:** a origem efetiva é
classificada como `PROJECT`, `INFORMADA` ou `INFERIDA`; capacidades inferidas
exigem confirmação explícita antes da aprovação; origem e validação são
persistidas no baseline e a origem aparece no relatório gerencial.

## P1.2 Sensibilidade de recursos

Adicionar análise gerencial:

```text
Se eu aumentar este recurso, quanto ganho no makespan?
```

Exemplo:

| Recurso | Base | Teste | Ganho makespan |
|---|---:|---:|---:|
| Mecânica | 4 | 5 | -6 h |
| Guindaste | 1 | 2 | -14 h |
| Inspeção | 2 | 3 | 0 h |

### Critérios de aceite

- [ ] Usuário consegue identificar recursos em que aumentar capacidade reduz prazo.
- [ ] Recursos com aumento sem efeito aparecem claramente.
- [ ] Análise não altera o baseline até confirmação do usuário.

---

# 5. Planejamento — Risco

## P1.3 Centralizar todos os controles de risco

A aba **Risco** deve concentrar:

### Incerteza de duração

- número de simulações;
- cenário otimista;
- cenário mais provável;
- cenário pessimista.

### Incerteza de escopo

- atividade potencial;
- atividade gatilho;
- evento;
- probabilidade.

### Resultado

Cards:

```text
Média
P50
P80
P90
P(cumprir janela)
```

Histograma e resumo de ampliação de escopo.

**TDS-7 implementado — aguardando revisão funcional:** configuração e resultado
foram consolidados na mesma aba **Risco**, incluindo o parâmetro mais provável
da distribuição triangular.

## P1.4 Mostrar decomposição do risco

Mostrar explicitamente:

```text
P80 somente duração
vs
P80 duração + escopo
```

E:

```text
Impacto médio de escopo
P80 do impacto de escopo
Frequência de ocorrência de escopo adicional
```

## P1.5 Ranking dos direcionadores de risco

Criar tabela:

| Evento | Probabilidade | Freq. simulada | Impacto marginal |
|---|---:|---:|---:|

Objetivo: responder **qual evento está mais puxando o P80**.

**TDS-7 implementado — aguardando revisão funcional:** a aba Risco exibe P80
somente duração, P80 combinado, impacto médio/P80 do escopo e uma tabela de
direcionadores ordenada pelo impacto marginal médio observado.

## P1.6 Documentar independência de eventos

Na versão atual, eventos diferentes são tratados como independentes.

A interface e relatório devem informar essa hipótese.

Evolução posterior:

- correlação;
- eventos mutuamente exclusivos;
- árvores de cenários;
- causas comuns.

---

# 6. Planejamento — Decisão

## P1.7 Manter a aba quase sem controles

A aba Decisão deve ser uma tela de leitura.

Exibir:

```text
Makespan base
Penalidade de recursos
P80
P(cumprir janela)
Status executivo
Gantt resumido
Aprovar baseline
```

Evitar configuração técnica nesta aba.

## P1.8 Status executivo baseado em risco

Problema: um cronograma determinístico pode estar dentro da janela enquanto o P80 está fora.

Exemplo:

```text
Determinístico  18 d
Janela          20 d
P80             22 d
P(janela)       42%
```

O sistema não deve aparecer simplesmente como “verde”.

Nova lógica:

```text
Status determinístico
Status de risco
Status geral
```

Exemplo:

```text
Determinístico: DENTRO
P80: FORA
P(janela): 42%

STATUS GERAL: RISCO DE PRAZO
```

### Critérios de aceite

- [ ] O status geral considera P80 e/ou probabilidade da janela.
- [ ] O gerente não recebe mensagem “dentro da janela” quando o risco é elevado.
- [ ] Critério de cor/status é documentado.

---

# 7. Governança da Baseline 0

## P1.9 Formalizar aprovação da baseline original

A Baseline 0 deve ter governança equivalente às revisões Rev.1…Rev.10.

Ao clicar em **Aprovar baseline**, solicitar:

```text
Nome do cenário
Aprovado por
Motivo / observação
Data/hora
```

Persistir também:

```text
Janela
Makespan base
P80
P(cumprir janela)
Capacidades aprovadas
Premissas de risco
```

### Critérios de aceite

- [ ] Baseline 0 possui aprovador.
- [ ] Baseline 0 possui timestamp.
- [ ] Baseline 0 possui motivo/observação.
- [ ] PDF consegue identificar formalmente a Baseline 0.

---

# 8. Escopo e Replanejamento — Arquitetura de navegação

## P1.10 Substituir navegação atual

Hoje:

```text
Operação
Configuração
Pessoas
Manual
```

Proposta:

```text
Visão
Operação
Escopo
Recursos
Governança
Relatório
```

A navegação deve seguir o domínio da parada, não a estrutura interna do software.

---

# 9. Aba Visão

## P1.11 Criar uma tela executiva da execução

Exibir:

```text
Baseline original
Baseline vigente
Forecast atual
Δ vs original
Δ vs vigente
Janela
Decisões pendentes
Novo escopo relevante
Cadeia controladora
```

Sem controles técnicos.

---

# 10. Aba Operação

## P1.12 Concentrar estado real

Manter na Operação:

- hora corrente;
- status das atividades;
- realizado;
- atividades em andamento;
- eventos;
- achados;
- dynamic scope DS-*;
- decisões disparadas;
- registro do que mudou.

Essa aba deve responder:

```text
O que aconteceu desde o último snapshot?
```

---

# 11. Aba Escopo

## P1.13 Mover regras para um domínio próprio

Retirar regras de uma aba genérica “Configuração”.

Criar aba **Escopo** com:

```text
conditional
XOR
OR
AND
gatilhos
eventos
rotas
observações
```

### Critérios de aceite

- [ ] Usuário entende que está configurando lógica de escopo.
- [ ] JSON continua como importação opcional.
- [ ] Editor tabular permanece como mecanismo principal.

---

# 12. Aba Recursos

## P1.14 Unificar recursos agregados e pessoas

A aba Recursos deve responder:

```text
Com quem e com o que consigo executar o plano?
```

Organizar em subseções:

### Capacidades

- Mecânica;
- Elétrica;
- Guindaste;
- Ferramentas;
- Inspeção;
- recursos não humanos.

### Pessoas

- roster;
- habilidades;
- multi-skill;
- disponibilidade.

### Resultado

- déficit;
- pico;
- utilização;
- atividades inviáveis;
- conflito de skill.

---

# 13. Peso de estabilidade

## P1.15 Traduzir λ para linguagem operacional

Não expor λ como principal controle gerencial.

Exibir:

```text
Preservação do plano

Baixa
Balanceada
Alta
```

Exemplo de mapeamento interno:

```text
Baixa       λ = 0,3
Balanceada  λ = 1,0
Alta        λ = 1,7
```

Manter λ exato em **Configuração avançada**.

---

# 14. Governança do replanejamento

## P1.16 Mover Rebaseline para “Governança”

A ação **Promover este replanejamento a nova linha de base** deve ficar em **Governança**.

Mostrar antes da promoção:

```text
Baseline vigente
Forecast atual
Δ vs vigente
Δ vs original
Mudança formalizada
```

Solicitar:

```text
Nome
Motivo
Aprovador
Nova janela
Observação
Confirmação
```

A lógica Rev.1…Rev.10 existente deve ser mantida.

---

# 15. Manual e ajuda

## P1.17 Remover Manual da navegação principal

Substituir aba Manual por **? Ajuda** no header ou expander contextual.

Objetivo: evitar consumir uma aba operacional com documentação.

---

# 16. Relatório Gerencial — Planejamento

## P1.18 Reestruturar a primeira página

A primeira página deve responder rapidamente:

```text
Janela
Makespan base
P80
P(cumprir janela)
Reserva até P80
Status geral
```

Exemplo:

```text
Janela                 21,0 d
Makespan base          18,7 d
P80                    21,4 d
P(janela)              74%
Reserva até P80        +2,7 d

STATUS: ATENÇÃO
```

## P1.19 Mostrar principais direcionadores

Primeira página ou logo após o resumo:

```text
1. nozzle_crack        +11 h
2. bundle_damage        +9 h
3. Guindaste            gargalo
```

A ferramenta deve apresentar **informação para decisão**, não decidir por conta própria.

## P1.20 Criar seção “Premissas do cenário”

Obrigatória para rastreabilidade.

Incluir:

```text
Arquivo de origem
Janela
Horas/dia usadas para conversão
Capacidades
Origem das capacidades
Nº de simulações
Distribuição de duração
Probabilidades de eventos
Heurística escolhida
Data/hora do cálculo
```

---

# 17. Relatório Gerencial — Execução

## P1.21 Manter indicadores atuais

Manter:

```text
Baseline original
Baseline vigente
Forecast atual
Δ vs original
Δ vs vigente
Janela
Histórico Rev.n
```

Essa estrutura é adequada para gestão de parada.

## P1.22 Trazer decisões pendentes para a leitura executiva

Adicionar na primeira página quando houver:

```text
DECISÕES PENDENTES

V-101
Reparar x substituir bocal
Impacto prazo
Impacto custo
Recurso crítico
```

Não selecionar a decisão automaticamente.

## P1.23 Mostrar novo escopo com impacto

Adicionar seção resumida:

```text
DS-003
Reparo de trinca
Descoberto em H+32
Impacto no término: +4 h
Bloqueia: fechamento V-101
```

Priorizar somente novos escopos com impacto relevante.

---

# 18. Calendários e turnos — maior lacuna operacional

## P2.1 Implementar calendário real

Hoje o scheduler trabalha em horas contínuas.

Para uso real em turnaround, implementar:

- turnos;
- calendários por recurso;
- calendários por equipe;
- indisponibilidades;
- jornadas;
- pausas;
- overtime.

Exemplo:

```text
Equipe A      07:00–19:00
Equipe B      19:00–07:00
Guindaste     06:00–18:00
Inspeção      08:00–17:00
```

### Critérios de aceite

- [ ] Atividade não é programada fora do calendário do recurso.
- [ ] Recursos distintos podem ter calendários diferentes.
- [ ] Gantt reflete datas reais.
- [ ] Monte Carlo respeita os calendários.
- [ ] Exportação MSPDI preserva o resultado.

---

# 19. Avanço real da execução

## P2.2 Reduzir entrada manual

Evoluções possíveis:

- importar atualização do Microsoft Project;
- aceitar % complete;
- aceitar Actual Start / Actual Finish;
- aceitar Remaining Duration;
- atualizar múltiplas atividades em lote;
- comparar estado importado x estado persistido.

### Critérios de aceite

- [ ] Não é necessário editar atividade por atividade em uma parada grande.
- [ ] O sistema detecta divergências entre baseline e realizado.

---

# 20. Risco avançado

## P3.1 Dependência entre eventos

Evoluir Monte Carlo para suportar:

- correlação;
- causas comuns;
- eventos mutuamente exclusivos;
- árvores de eventos;
- probabilidade condicional.

Exemplo:

```text
corrosão severa
    ↓
P(nozzle_crack | corrosão)
P(internal_damage | corrosão)
```

---

# 21. Melhorias matemáticas futuras

Não priorizar antes de UX, calendário e governança.

Depois disso avaliar:

- CP-SAT;
- MILP;
- múltiplos objetivos;
- overtime;
- custo de contratação;
- buffers;
- crew sizing;
- proficiência;
- produtividade;
- aprendizado;
- restrições de área;
- LOTO;
- simultaneidade;
- acesso;
- guindaste;
- ferramentas especiais.

---

# 22. Ordem recomendada de implementação

## Sprint 1 — UX e linguagem

- [ ] Renomear horas/dia.
- [x] Renomear “Gerar plano otimizado”.
- [ ] Reduzir sidebar.
- [x] Mover Monte Carlo para Risco.
- [ ] Mover capacidades para Recursos.
- [ ] Adicionar origem da capacidade.
- [ ] Implementar status geral baseado em risco.

## Sprint 2 — Governança

- [ ] Formalizar Baseline 0.
- [ ] Criar aba Governança.
- [ ] Mover rebaseline para Governança.
- [ ] Melhorar rastreabilidade de aprovação.
- [ ] Remover Manual da navegação principal.

## Sprint 3 — Reorganização da execução

- [ ] Criar aba Visão.
- [ ] Criar aba Escopo.
- [ ] Consolidar Recursos + Pessoas.
- [ ] Traduzir λ em níveis de preservação.
- [ ] Manter configuração avançada opcional.

## Sprint 4 — Relatórios gerenciais

- [ ] Nova primeira página do Planejamento.
- [ ] Premissas do cenário.
- [ ] Ranking de direcionadores de risco.
- [ ] Decisões pendentes na execução.
- [ ] Novo escopo com impacto.
- [ ] Status gerencial coerente com P80/P(janela).

## Sprint 5 — Calendários

- [ ] Turnos.
- [ ] Calendário de recursos.
- [ ] Indisponibilidade.
- [ ] Overtime.
- [ ] Integração com RCPSP/MRCPSP.
- [ ] Integração com Monte Carlo.
- [ ] Exportação Project coerente.

## Sprint 6 — Atualização operacional

- [ ] Importar progresso.
- [ ] Actual Start / Actual Finish.
- [ ] Remaining Duration.
- [ ] Atualização em lote.
- [ ] Comparação plano x realizado.

## Sprint 7 — Risco avançado

- [ ] Correlação.
- [ ] Causas comuns.
- [ ] Eventos dependentes.
- [ ] Árvores de cenários.

---

# 23. Definition of Done para uso prático

A ferramenta pode ser considerada pronta para um piloto operacional de grande parada quando:

- [ ] capacidades forem explicitamente validadas;
- [ ] calendário/turnos forem respeitados pelo scheduler;
- [ ] status executivo considerar risco;
- [ ] baseline original possuir governança formal;
- [ ] rebaseline possuir governança formal;
- [ ] avanço real puder ser atualizado sem trabalho excessivamente manual;
- [ ] relatório de Planejamento registrar todas as premissas;
- [ ] relatório de Execução mostrar decisões pendentes e novo escopo relevante;
- [ ] Project XML mantiver baseline, revisões, forecast e realizado;
- [ ] Monte Carlo respeitar escopo potencial e calendários;
- [ ] testes de regressão cobrirem planejamento → baseline → execução → replanejamento → exportação.

---

# 24. Métricas para avaliar a evolução do produto

## UX

- tempo para montar um cenário;
- número de controles visíveis por tela;
- número de ações para aprovar baseline;
- número de ações para registrar um achado;
- número de ações para gerar replanejamento.

## Planejamento

- diferença CPM x RCPSP;
- penalidade de recursos;
- P80;
- P(cumprir janela);
- impacto de escopo probabilístico.

## Execução

- Δ vs baseline original;
- Δ vs baseline vigente;
- número de decisões pendentes;
- número de DS-*;
- impacto acumulado de novo escopo;
- estabilidade do plano;
- quantidade de rebaselines.

## Operação

- tempo para atualizar avanço;
- tempo para registrar achado;
- tempo para produzir forecast novo;
- tempo para exportar cronograma atualizado ao Project.

---

# 25. Direção de produto

A cadeia de valor a preservar é:

```text
Microsoft Project
        ↓
factibilidade por recursos
        ↓
risco de duração + escopo
        ↓
baseline aprovado
        ↓
execução
        ↓
achado
        ↓
novo escopo
        ↓
replanejamento
        ↓
rebaseline formal
        ↓
Microsoft Project atualizado
```

A prioridade imediata deve ser:

```text
UX
↓
governança
↓
calendário
↓
avanço operacional
↓
novos métodos matemáticos
```

Neste estágio, adicionar mais algoritmos antes de resolver essas quatro camadas tende a aumentar a complexidade sem aumentar proporcionalmente a aplicabilidade prática.
