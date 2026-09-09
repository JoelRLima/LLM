# ADR 0016: fechamento determinístico PRE-V1

- Status: aceito
- Data: 2026-09-09

## Contexto

A Wave 15.5 fecha os critérios C2, C3 e C9 da visão standalone sem reabrir os
contratos de W12–W15. O pacote precisa demonstrar, com procedimentos
determinísticos, uma credencial referenciada, uma fronteira explícita de
workspace e uma projeção de auditoria bounded. Isso ainda não é a aceitação
final com modelo real.

## Decisão

1. A configuração pode persistir somente a metadata fechada de uma referência
   `env`/`bearer`. O valor é resolvido tarde e somente no transporte HTTP do
   provider; não existe um vault geral nem uma camada de secrets além desse
   boundary mínimo.
2. Toda rota task-producing headless recebe `--workspace` explícito. A falta
   dessa seleção falha antes de application, modelo, tools e mutação. O chat
   interativo continua usando seu chooser explícito.
3. O fechamento de um run produz um `run_audit_receipt` read-only, bounded e
   versionado a partir dos fatos canônicos. Ele usa o dispatcher, TraceStore,
   Presentation API e export existentes; não vira autoridade, approval,
   checkpoint ou outcome owner.

## Limites

Esta decisão não adiciona autonomia em background, scheduling temporal, provider,
evaluator ou event bus. O receipt não contém secrets, prompts, completions,
args/results brutos ou conteúdo de artifact. A identidade observada do modelo é
registrada apenas quando observada no response e nunca é inferida da identidade
declarada.

## Consequências e aceitação

Os owners existentes de configuração, workspace, gateway, authority, approval,
mutation evidence, snapshot, dispatcher, trace e export permanecem canônicos.
Os gates de wheel instalado, C1–C10, W15/W15.5, H-series e long-horizon são
determinísticos e não executam Qwen nem modelo vivo. O live-model gate permanece
separado, autorizado explicitamente e necessário antes de qualquer declaração de
Standalone V1.
