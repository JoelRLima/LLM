# Planning e descoberta de tools

> **STATUS: CURRENT — PRIMARY HOME.** Planning propõe ações; authority e
> execução pertencem, respectivamente, a [security.md](security.md) e
> [orchestration.md](orchestration.md). A policy task-scoped pertence ao
> [runtime](runtime.md), e a projeção de progresso ao
> [reporting](reporting.md).

## Contrato

Depois do roteamento de persona, o `Orchestrator` cria no máximo um
`PlanningContextSnapshot` para a tarefa. Ele projeta descriptors do
`ToolRegistry` congelado junto da application/task authority e das restrições
de persona. O snapshot é imutável, carrega `RuntimeSnapshotIdentity` e contém
somente metadata segura para planning, nunca adapter, comando ou manifest.

`PlanningPresentationSnapshot` oferece views correlacionadas por planner:
linear e reativo recebem schemas; o hierárquico recebe visão compacta. Nome
ausente da view, schema incoerente, budget/profundidade excedidos e identidade
divergente falham fechado. Descrições externas são enquadradas como catálogo
não confiável.

Planning context, apresentação, `active_skills`, persona e escolha do modelo
são eligibility/guidance. Nenhum deles cria grant ou substitui o gateway.

## Task Definition e Plan

`TaskContract`/`TaskSpec` são a autoridade normativa admitida antes do
planning; `TaskDefinitionRef` é o binding compacto que permite resolvê-la.
`ContextManager` materializa essa autoridade como contexto confiável,
separado de projeto, memória, histórico, summaries e outputs de tools não
confiáveis.

O `Plan` é o artefato executável: possui steps, tools, bindings e validação
pelo `ExecutionGateway`. Contract e Spec não contêm tool calls, não concedem
capabilities e não substituem o Plan. As fases descritivas da Spec também não
implementam avanço autônomo, retry/replan de longo horizonte ou policy
dinâmica. A decisão task-scoped pertence ao `TaskRuntimePolicy` da tarefa raiz,
e os fatos de progresso pertencem à `TaskProgressProjection` read-only; nenhum
deles transforma a Spec em autoridade de execução. Planning e replan conservam
seus owners e budgets específicos.

## Caminhos de planejamento

- linear: `PlanBuilder` solicita um plano sequencial mínimo e valida cada passo
  contra a view. Para mudanças orienta `code_task`, não `file_writer`.
- reativo: fallback quando não há plano; cada decisão unitária atravessa a
  mesma validação/otimização antes da invocação.
- hierárquico: objetivos complexos viram `MacroPlan`; dependências são
  convertidas/validadas como `TaskGraph`, executadas hoje em ordem topológica e
  cada microplano atravessa o `ExecutionGateway`.

Essas rotas, além do analyzer de segurança, dos nós de `TaskGraph` e dos
caminhos de modelo/sessão aninhados, aplicam a policy da tarefa raiz por
adapters estreitos. Recovery, replan e continuação também preservam essa
verdade quando participam da execução. A equivalência é de fatos e decisões,
não de implementação interna.

`ExecutionGateway` é o gateway de planos: valida, otimiza, revalida e coordena
replan antes do `PlanExecutor`. Ele não é o `ToolInvocationGateway`, que aplica
authority/approval na chamada concreta de uma tool.

## Replan e falhas

Falhas recuperáveis são classificadas deterministicamente; heurísticas seguras
precedem a consulta ao modelo. Uma proposta de replan é validada contra o mesmo
planning context e não pode introduzir tool fora da view. Passo bloqueado não é
removido silenciosamente. Cada chamada de replan recebe um budget limitado; os
contadores específicos de replan não são um budget quantitativo compartilhado
de toda a tarefa. Isso não remove a policy task-scoped: admissões lógicas e
duração ativa são mantidas pela mesma `TaskRuntimePolicy` raiz, e replan não
pode resetá-las. Replan não cria authority.

## Paralelismo

O `PlanExecutor` pode executar fisicamente batches compatíveis de leitura. A
semântica de decisão permanece sequencial: resultados são finalizados por slot
na ordem lógica do plano, todos os siblings iniciados são assentados e a
primeira disposition decisiva projeta o agregado. `step_id`, `invocation_id` e
slot lógico são capturados antes do dispatch; completion física não decide o
resultado. Antes do dispatch, o `TaskRuntimePolicy` admite o número lógico de
unidades ainda disponível. Um batch paralelo é truncado atomicamente pelo
prefixo admitido; slots rejeitados não são submetidos e a admissão não pode ser
resetada por cursor, replan ou limpeza de microplano.

O `TaskGraphScheduler` do domínio de código é separado: cria contextos filhos,
aplica dependências e conflitos read/write e limita concorrência. O executor
hierárquico legado continua sequencial por compartilhar sessão e `AgentState`.
Ambos preservam a policy da tarefa raiz; `TaskGraphState` continua sendo o owner
do estado dos nós, sem um segundo owner de progresso no checkpoint da tarefa.

O lifecycle hierárquico macro é explícito. Uma execução interrompida em
`running` não é pseudo-retomada; o resume falha fechado com
`HIERARCHICAL_RESUME_UNSUPPORTED`. Limpar um microplano individual não libera
novas admissões para a tarefa raiz.

## Limites

O modelo pode escolher entre alternativas apresentadas e influenciar a persona
dentro das policies estáticas, mas não registrar tools, alterar descriptor,
criar grants ou promover output em authority. Robustez comparativa de planners
e modelos ainda não foi demonstrada; isso pertence a trabalho futuro separado
do contrato de planning.
