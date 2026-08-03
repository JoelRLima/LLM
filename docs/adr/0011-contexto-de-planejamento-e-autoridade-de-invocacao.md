# ADR 0011 — Contexto de planejamento e autoridade de invocação

- Status: contratos concluídos, validados e publicados
- Escopo: Gate 2.6a e Gate 2.7a
- Gates 2.6 e 2.7: não concluídos

## Contexto

O `ToolRegistry` congelado já é a fonte runtime das tools executáveis, mas o
planejamento histórico mantinha listas e metadados paralelos. A autorização
também combinava persona, `active_skills`, capabilities e grants sem um
snapshot único por aplicação/tarefa.

## Decisão

- `ApplicationAuthoritySnapshot` é capturado durante o bootstrap a partir do
  mesmo snapshot de workspace/resolução usado para compor o registry.
- `RuntimeSnapshotIdentity` contém o `workspace_id` canônico e um ID opaco de
  bootstrap; a mesma instância é publicada no `ToolRegistry` e na authority.
- O planning builder exige essa identidade conjunta e um registry congelado;
  não existe fallback por parâmetro opcional de workspace.
- Grants permanecem persistidos por `extension_id`; não são duplicados por
  tool, nem relidos por tarefa ou invocação.
- Grants são armazenados internamente em composição imutável; presença de uma
  extension continua distinta de presença com grant vazio.
- `TaskAuthoritySnapshot` é sempre fornecido explicitamente por uma camada
  chamadora confiável. Ausência (`None`) não equivale a snapshot vazio:
  ausência torna extensions inelegíveis, enquanto vazio explícito permite
  somente extensions sem capabilities requeridas e corretamente configuradas.
- Persona é apenas uma restrição da autoridade explícita; nunca adiciona
  capabilities. `active_skills` não é autoridade.
- `PlanningContextSnapshot` é uma projeção profunda e imutável dos descriptors
  do `ToolRegistry`. As tools elegíveis são um subconjunto do registry e
  `eligible_names` corresponde exatamente às tools projetadas.
- A origem é tipada como `builtin` ou `extension`. Tools de extension carregam
  `extension_id` canônico; planning models não carregam adapter, comando, path
  ou manifest.
- `ToolDescriptor` mantém as posições posicionais históricas; `origin_kind` e
  `extension_id` são keyword-only. Capabilities são copiadas para
  `frozenset` durante a construção.
- `ToolInvocationRequest` estabelece o boundary para que `invocation_id` nasça
  antes do gateway, com argumentos defensivamente copiados. A integração
  funcional com gateway, eventos, approval e timeout pertence aos subgates
  seguintes.

## Consequências

Builtins preservam o comportamento histórico e não passam a depender de
grants de extensions. Revogações persistidas exigem novo bootstrap: uma
aplicação existente mantém seu snapshot. Falhas estruturais de origem,
grants ou descriptors não devem ser convertidas silenciosamente em autoridade;
a projeção deve falhar fechado ou produzir diagnóstico tipado.

Descrições e schemas externos são armazenados apenas como dados copiados. O
framing textual, provenance visível, budgets de contexto e proteção contra
prompt injection serão tratados no Gate 2.6b.

## Fora deste ADR

Status dos subgates: Gate 2.6a e Gate 2.7a foram concluídos, validados e
publicados; Gates 2.6 e 2.7 continuam não concluídos. Gate 2.8 e Gate 2.9 não
foram iniciados.

Não foram alterados planners, `PlanValidator`, `PlanOptimizer`, replanner,
gateway em produção, lifecycle de eventos, approval, redaction, timeout,
CLI, hot reload, protocolo stdio 1.0 ou Gate 2.8/2.9.
