# Arquitetura de execução e retomada

## Fluxo canônico

Todo plano segue a mesma cadeia:

```text
linear / reativo / hierárquico
            |
            v
    ExecutionGateway
 valida -> otimiza -> revalida
            |
            v
       PlanExecutor
 coordena dependências, lote paralelo,
 limites, cancelamento e replan
            |
            v
       StepExecutor
 valida e executa uma ferramenta,
 pós-processa e encerra o passo
            |
            v
         AgentState
 IDs, estados, histórico e checkpoint
```

`PlanExecutor` não implementa mais o ciclo interno de uma ferramenta. O
`StepExecutor`, injetável pelo construtor, é o núcleo único dessa operação.

## Estados do passo

Um passo começa em `pending` e recebe um `_step_id` estável. Ao iniciar uma
tentativa passa para `running`. A finalização usa um dos estados terminais:

- `completed`: execução e pós-processamento concluídos;
- `failed`: passo encerrado com erro;
- `skipped`: passo deliberadamente ignorado, por exemplo por dependência falha.

O contador `attempts` e o último erro ficam em `StepExecutionRecord`, separado
do conteúdo declarativo do plano.

## Checkpoint e retomada

O schema v2 persiste plano, IDs e registros de execução. Ao restaurar:

1. passos `completed` permanecem terminais; `failed` e `skipped` também
   permanecem por padrão, mas podem voltar a `pending` pelas flags de retry;
2. passos que estavam `running` voltam a `pending`, pois não há confirmação de
   conclusão atômica;
3. o executor seleciona `next_pending_index()` e não repete passos concluídos;
4. o plano restaurado ainda atravessa o `ExecutionGateway` antes da execução.

Eventos `step_completed`, `step_failed` e `step_skipped` disparam persistência.
O `CancellationToken` é consultado em limites seguros e `cancel_task()` salva o
checkpoint imediatamente.

## Responsabilidades

- `ExecutionGateway`: política de entrada, validação e otimização.
- `PlanExecutor`: coordenação entre passos e mutações de plano por replan.
- `StepExecutor`: ciclo de vida de exatamente um passo.
- `AgentState`: fonte de verdade das transições e serialização.
- `CheckpointManager`: I/O atômico e versionamento do checkpoint.

## Limitações remanescentes

- resultados de ferramentas e eventos ainda são dicionários livres;
- o lote paralelo compartilha telemetria e memória mutável, embora a transição
  terminal seja serializada depois da coleta dos futures;
- checkpoints de schema v1 são rejeitados, sem migração automática;
- cancelamento é cooperativo e não encerra uma ferramenta já bloqueada.
