# Planning e descoberta de tools

> **STATUS: CURRENT — PRIMARY HOME.** Planning propõe ações; authority e
> execução pertencem, respectivamente, a [security.md](security.md) e
> [orchestration.md](orchestration.md).

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

## Caminhos de planejamento

- linear: `PlanBuilder` solicita um plano sequencial mínimo e valida cada passo
  contra a view. Para mudanças orienta `code_task`, não `file_writer`.
- reativo: fallback quando não há plano; cada decisão unitária atravessa a
  mesma validação/otimização antes da invocação.
- hierárquico: objetivos complexos viram `MacroPlan`; dependências são
  convertidas/validadas como `TaskGraph`, executadas hoje em ordem topológica e
  cada microplano atravessa o `ExecutionGateway`.

`ExecutionGateway` é o gateway de planos: valida, otimiza, revalida e coordena
replan antes do `PlanExecutor`. Ele não é o `ToolInvocationGateway`, que aplica
authority/approval na chamada concreta de uma tool.

## Replan e falhas

Falhas recuperáveis são classificadas deterministicamente; heurísticas seguras
precedem a consulta ao modelo. Uma proposta de replan é validada contra o mesmo
planning context e não pode introduzir tool fora da view. Passo bloqueado não é
removido silenciosamente. Cada chamada de replan recebe um budget limitado; os
contadores atuais não são um budget compartilhado de toda a tarefa. Replan não
cria authority.

## Paralelismo

O `PlanExecutor` pode executar fisicamente batches compatíveis de leitura. A
semântica de decisão permanece sequencial: resultados são finalizados por slot
na ordem lógica do plano, todos os siblings iniciados são assentados e a
primeira disposition decisiva projeta o agregado. `step_id`, `invocation_id` e
slot lógico são capturados antes do dispatch; completion física não decide o
resultado.

O `TaskGraphScheduler` do domínio de código é separado: cria contextos filhos,
aplica dependências e conflitos read/write e limita concorrência. O executor
hierárquico legado continua sequencial por compartilhar sessão e `AgentState`.

## Limites

O modelo pode escolher entre alternativas apresentadas e influenciar a persona
dentro das policies estáticas, mas não registrar tools, alterar descriptor,
criar grants ou promover output em authority. Robustez comparativa de planners
e modelos ainda não foi demonstrada; isso pertence ao Marco 3 Block B.
