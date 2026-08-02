# Módulo `agent/` — tools

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

## Visão geral

O pacote `agent/tools/` define o microkernel de ferramentas do agente, com
registradores, adaptadores e um gateway centralizado de invocação.

Ele separa três responsabilidades:

1. Contratos de ferramentas (`agent/tools/contracts.py`).
2. Registro e descoberta de adaptadores (`ToolRegistry`).
3. Controle de autorização, validação e timeout (`ToolInvocationGateway`).

## ToolRegistry

- Implementado em `agent/tools/tool_registry.py`.
- Agrega `ToolAdapter`s que expõem descritores de ferramentas.
- Mantém um cache de `ToolDescriptor` por nome.
- Resolve ferramentas por nome e invoca o adapter correto.
- Retorna `ToolResult` com `UNAVAILABLE` quando a ferramenta não existe.
- Também converte descritores em `ToolMetadata` para uso pelo planner.

### Comportamento

- `register_adapter(adapter)` adiciona adaptadores dinamicamente e atualiza
  o cache.
- `descriptor(name)` levanta `KeyError` se a ferramenta não foi registrada.
- `invoke(invocation)` delega ao adapter correspondente.

## ToolInvocationGateway

- Implementado em `agent/tools/invocation_gateway.py`.
- É o único ponto de controle para execução de ferramenta quando presente.
- Encapsula:
  - Existência da ferramenta no registro.
  - Autorização por `active_skills`.
  - Autorização por `allowed_capabilities`.
  - Validação de schema de argumentos.
  - Timeout e tratamento de exceções.
  - Emissão de eventos de telemetria.
  - Registro de resultados no estado.

### Validação e autorização

- Se a ferramenta não estiver registrada, retorna `ToolResult.status == UNAVAILABLE`.
- Se o nome da ferramenta não estiver em `active_skills`, retorna `PERMISSION_DENIED`.
- Se os requisitos de capacidades da ferramenta não forem subconjunto de
  `allowed_capabilities`, retorna `PERMISSION_DENIED`.
- `descriptor.schema` é usado para validar os tipos básicos dos argumentos antes
  da invocação.

### Timeouts

- `timeout_seconds` pode ser fornecido na chamada.
- Caso contrário, usa `descriptor.timeout_seconds`.
- Timeout resulta em `ToolStatus.TIMED_OUT`.

### Eventos e persistência

- Emite `tool_start` e `tool_end` via `event_emitter` opcional.
- Registra resultados no estado via `state_recorder` opcional.
- Falhas de emissão ou registro não interrompem a execução do gateway.

## Protocolo stdio e correlação de invocações

O transport stdio usa a versão de protocolo atual `1.0`. Nesta fase de
desenvolvimento não existe compatibilidade legada nem uma versão `1.1` em
paralelo.

Cada request contém um `invocation_id` não vazio. A extension deve devolver
exatamente uma linha JSON não vazia; linhas vazias adicionais são ignoradas.
A resposta terminal deve conter o mesmo ID. O
adapter rejeita como `PROTOCOL_ERROR` respostas sem ID
(`MISSING_INVOCATION_ID`), com ID divergente ou desconhecido
(`INVOCATION_MISMATCH`), JSON inválido e mais de uma linha de resposta.

O framing é estrito: stdout e stderr são drenados concorrentemente enquanto o
processo produz dados, sem `communicate()` acumular streams ilimitados. O limite
de stdout é `MAX_OUTPUT_BYTES` (1 MiB) e o limite independente de stderr é
`MAX_STDERR_BYTES` (1 MiB); exceder qualquer um encerra a execução com erro de
protocolo (`OUTPUT_LIMIT` ou `STDERR_OUTPUT_LIMIT`). stderr é mantido apenas
como amostra diagnóstica limitada e nunca entra no resultado de sucesso. A
resposta deve ser UTF-8 e o manifest aceita
somente tipos explícitos, `transport: "stdio"`, protocolo `1.0`, entrypoint
não vazio, tools com nomes únicos e `timeout_seconds` obrigatório, inteiro
entre 1 e 3600 segundos. `MAX_STDERR_BYTES` permanece em 1 MiB; conteúdo
volumoso e artifacts não devem ser enviados por stderr.
Campos não documentados no objeto raiz do manifest ou nas declarações de tools
são rejeitados. Campos internos de `schema` seguem JSON Schema e não são
tratados como campos do manifest.

Exit code diferente de zero sempre produz `FAILED`/`PROCESS_FAILED`, mesmo que
uma resposta JSON tenha sido escrita. Com exit code zero, `status` ausente
mantém o default de sucesso já existente; valores desconhecidos são erro de
protocolo.

Ao atingir timeout, limite ou erro de processo, o adapter solicita o
encerramento da árvore da extension (SIGTERM e depois SIGKILL no grupo POSIX;
Job Object no Windows), fecha os pipes e aguarda as threads de drenagem. Falhas
de terminação, Job Object e drenagem são retornadas como `CLEANUP_ERROR`, nunca
ocultadas por `daemon=True`; falha isolada ao remover o status privado fica
registrada em diagnóstico bounded sem substituir o resultado principal.
O caminho anterior à criação do contexto usa o helper interno
`agent.tools.stdio_cleanup` para agregar essas falhas sem perder o erro original.
No Windows, o adapter inicia primeiro o launcher interno
`agent.tools.stdio_launcher`, associa o launcher ao Job Object e só então envia
o envelope privado. A extension real não é iniciada antes da associação; falha
de criação ou associação retorna falha de infraestrutura e não degrada para
execução direta. O launcher herda stdout/stderr para que os limites e a
drenagem concorrente continuem no adapter. O protocolo público permanece
`1.0`; esse launcher é um detalhe interno, não uma sandbox e não exige mudança
nas extensions. Respostas tardias são descartadas e não são associadas a outra
invocação.

Timeout encerra o subprocesso da invocação; uma resposta tardia não é
reassociada a outra chamada. Eventos `tool_start`/`tool_end`, o resultado
canônico e o registro de estado carregam o mesmo ID. Ainda não há um
`attempt_id` separado para distinguir tentativas de retry de uma invocação
lógica; essa evolução permanece pendente.

## Integridade do fluxo

- Na composição standalone, o gateway é a fronteira canônica para invocações
  de tools. A fachada `LegacyToolInvoker` existe apenas para consumidores
  legados que não montam `AgentApplication`.
- O gateway bloqueia tools não registradas, capabilities não concedidas,
  efeitos sem aprovação e argumentos incompatíveis antes do adapter. Isso não
  constitui sandbox de sistema operacional.
