# ADR 0012 — Descoberta segura de tools no planejamento

- Status: implementado localmente, pendente de auditoria
- Escopo: Gate 2.6b e Gate 2.6c
- Data: 2026-08-02
- Gates 2.6 e 2.7: não concluídos

## Contexto

O Gate 2.6a publicou `PlanningContextSnapshot` como projeção canônica e
imutável do registry e da autoridade. Os planners ainda precisavam receber
essa informação sem voltar a consultar adapters, manifests ou listas legadas.
Descrições de extensions também são dados não confiáveis: o catálogo não pode
virar uma instrução do modelo, exceder o orçamento de contexto ou fazer uma
tool elegível parecer executável quando ela não foi apresentada.

## Decisão

1. `Orchestrator` captura no máximo um contexto por tarefa, depois de definir
   persona e capabilities. A produção passa `TaskAuthoritySnapshot=None`;
   portanto extensions permanecem invisíveis até uma camada confiável fornecer
   autoridade explícita. Builtins continuam elegíveis pelo registry congelado.
2. `PlanningContextSnapshot.present(planner_kind, visible_names)` cria uma
   projeção somente-leitura. `visible_names` só pode ser subconjunto de
   `eligible_names`; a identidade do contexto é preservada e a ordenação é
   determinística.
3. `PlanningPresentationSnapshot` é a única representação textual entregue
   aos planners linear, reativo e hierárquico. O payload contém somente nome,
   descrição, schema (quando necessário), categoria, custo, capabilities,
   origem e `extension_id` canônico. Não contém adapter, comando, path,
   manifest ou objeto executável.
4. A apresentação é enquadrada por texto confiável e pelas tags
   `<untrusted_tool_catalog>`. JSON é ordenado, os caracteres `&`, `<` e `>`
   são escapados antes da medição do corpo final e qualquer excesso de
   quantidade, campo, tool ou catálogo falha fechado; nenhuma tool é omitida
   silenciosamente. Schemas possuem limite determinístico de profundidade e
   ciclos falham com erro tipado. O budget deriva do limite de contexto da
   sessão e permanece limitado a 16 KiB.
5. O planner hierárquico recebe uma visão macro compacta; linear e reativo
   recebem schemas para construir argumentos. A lista apresentada é também a
   fronteira de validação: nomes ausentes, schemas inválidos e capabilities
   não autorizadas são bloqueados. Quando existe contexto/view canônico,
   `self.skills` não participa da validação.
6. `PlanValidator`, `PlanOptimizer` e `replan` usam o mesmo contexto e a mesma
   view correlacionada por `planning_context_id` e `RuntimeSnapshotIdentity`.
   O optimizer não consulta `TOOL_METADATA` estático quando há contexto;
   custos, categoria e cacheabilidade vêm dos descriptors projetados e uma
   tool desconhecida falha fechado. Replanejamento não pode introduzir tool
   fora da projeção.
7. `ExecutionGateway` transporta explicitamente o contexto para validação,
   otimização e replanejamento. Esta alteração não faz o gateway invocar
   `ToolInvocationGateway` nem cria `ToolInvocationRequest` em produção. Um
   contexto explícito divergente do orchestrator exige uma view correlacionada;
   não há reconstrução silenciosa a partir da view de outro contexto.

## Invariantes e limites

- `active_skills` é apenas visibilidade de builtins, nunca autoridade.
- Nenhum planner executa uma extension e nenhum catálogo cria aprovação,
  eventos de lifecycle, timeout, deadline, retry ou cancelamento.
- O contexto é uma fotografia: alterações posteriores no registry ou na
  autoridade não mudam a tarefa corrente.
- O contexto e as views preservam `RuntimeSnapshotIdentity`, incluindo
  `workspace_id`, sem cópias independentes de identidade.
- O caminho legado baseado em `self.skills` é permitido apenas sem contexto
  canônico; compatibilidade de assinatura é decidida antes da chamada e não
  captura `TypeError` interno do renderer.
- Respostas, artifacts e checkpoints não foram alterados para usar esse
  envelope; ampliar essa integração exigirá decisão arquitetural própria.
- A distinção entre invocação lógica e tentativa de retry continua pendente
  conforme os contratos anteriores.

## Consequências

O fluxo de planejamento deixa de depender de dicionários paralelos para
tools elegíveis e metadados. A falha de budget e de profundidade é explícita
e observável. O framing delimita conteúdo externo como dados, impede quebra
estrutural da moldura nos casos testados e reduz a superfície de prompt
injection, mas o conteúdo ainda pode influenciar semanticamente o modelo.
Em contrapartida, extensions não
aparecem em produção enquanto o sistema não possuir uma origem confiável de
autoridade de tarefa. O Gate 2.6b/2.6c permanece aberto até auditoria dos
call sites e dos testes no CI.

A duplicação do catálogo linear/reativo entre o system prompt e o prompt do
planner permanece como dívida de eficiência e context budget, não como blocker
desta alteração.

Nota: a duplicacao do catalogo tambem ocorre no fluxo hierarquico; esta e uma divida de eficiencia e context budget, sem mudanca comportamental neste Gate.

## Fora deste ADR

Não foram alterados `ToolInvocationGateway`, transporte stdio, manifest,
approval, eventos, timeout, deadline, retry, cancelamento, CLI, Gate 2.7b/c,
Gate 2.8 ou Gate 2.9.
