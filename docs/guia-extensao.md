# Guia de extensão

> **STATUS: CURRENT — EXTENSION/CHANGE GUIDE.** Contratos de capability,
> authority e processo pertencem, respectivamente, a
> [tools](agent/tools.md), [security](agent/security.md) e
> [runtime](agent/runtime.md).

Use este mapa para colocar uma mudança na camada correta. A regra central é:
skills e CLI adaptam entradas; domínio implementa comportamento; runtime
fornece serviços transversais; adapters isolam tecnologias externas.

## Onde alterar

| Necessidade | Fonte principal | Regra |
| :--- | :--- | :--- |
| novo provider | [`agent/llm/providers/`](../agent/llm/providers/) e [`factory.py`](../agent/llm/providers/factory.py) | implemente `ModelGateway`; não altere workflows |
| contrato de modelo | [`contracts.py`](../agent/llm/contracts.py) | mantenha request/response independentes de protocolo |
| saída estruturada | [`structured_output.py`](../agent/llm/structured_output.py) | fallback deve continuar validando schema em runtime |
| compressão de contexto | [`context_manager.py`](../agent/llm/context_manager.py) | respeite o perfil de hardware e use o gateway para tokens |
| perfil de hardware | [`hardware.py`](../agent/runtime/hardware.py) e [`config.py`](../agent/runtime/config.py) | para 8 GB, mantenha concorrência de modelo em 1 |
| composição da aplicação | [`application.py`](../agent/application.py) | interfaces reutilizam esta raiz; não montam runtime paralelo |
| consentimento para efeitos | [`approval.py`](../agent/approval.py) | injete `ApprovalPort`; approval não cria authority e o domínio nunca consulta stdin |
| workspace | [`workspace_context.py`](../agent/runtime/workspace_context.py) | receba a raiz explicitamente e injete-a em consumidores |
| nova linguagem | [`agent/code/languages/`](../agent/code/languages/) | implemente o adapter e declare limitações reais |
| descoberta do projeto | [`discovery.py`](../agent/code/discovery.py) | não execute scripts de manifests durante descoberta |
| análise/índice | [`intelligence.py`](../agent/code/intelligence.py) | retorne diagnósticos, não exceções globais por arquivo inválido |
| seleção de contexto | [`context_selection.py`](../agent/code/context_selection.py) | use sinais determinísticos, limites e hashes; não peça ao modelo para escolher arquivos |
| aplicação de mudanças | [`changes.py`](../agent/code/changes.py) | preserve path seguro, hash, diff e rollback |
| risco/confirmação | [`policy.py`](../agent/code/policy.py) | score explicável antes do commit; confirmação não substitui validação |
| classificação de falha | [`diagnostics.py`](../agent/code/diagnostics.py) | heurística determinística antes de qualquer retry por modelo |
| validator | [`validation.py`](../agent/code/validation.py) | `shell=False`, timeout, cancelamento; não instale pacotes |
| nova extension stdio | [`examples/extensions/demo_extension/`](../examples/extensions/demo_extension/) e [`agent/tools/stdio_adapter.py`](../agent/tools/stdio_adapter.py) | protocolo `1.0`; toda resposta deve ecoar o `invocation_id` recebido |
| workflow de código | [`workflows.py`](../agent/code/workflows.py) | componha serviços e retorne `TaskResult` |
| entrada CLI/skill | [`application.py`](../agent/code/application.py) | mantenha uma entrada única independente de UI e planner |
| comando explícito | [`commands.py`](../agent/code/commands.py) | parser puro; não execute efeitos nem importe CLI |
| template de grafo | [`task_templates.py`](../agent/code/task_templates.py) | IDs, dependências, capabilities e recursos determinísticos |
| nova skill | [`catalog.py`](../agent/skills/catalog.py) e um módulo de skill | um `SkillSpec`; sem mapa paralelo |
| política de persona | [`policy.py`](../agent/skills/policy.py) | conceda capacidades, não nomes de tools |
| schema de plano legado | [`agent/contracts.py`](../agent/contracts.py) | preserve formato JSON público |
| execução unitária | [`step_executor.py`](../agent/planning/step_executor.py) | não devolva coordenação global ao passo |
| validação de planos | [`execution_gateway.py`](../agent/planning/execution_gateway.py) | mantenha o gateway nos fluxos linear, reativo e hierárquico |
| dependências/multitarefa | [`task_graph.py`](../agent/planning/task_graph.py) e [`task_scheduler.py`](../agent/planning/task_scheduler.py) | preserve DAG, isolamento, recursos e determinismo |
| retry/replan legado | [`replan.py`](../agent/planning/replan.py) | heurística segura antes de modelo |
| segurança estática | [`security_patterns.py`](../agent/security/security_patterns.py) | mantenha o registro canônico de padrões |
| caminhos gerados | [`paths.py`](../agent/runtime/paths.py) | escolha escopo global ou de workspace; não use literals de `runtime/` |
| configuração | [`config_repository.py`](../agent/runtime/config_repository.py), [`config_schema.py`](../agent/runtime/config_schema.py) e [`config_effective.py`](../agent/runtime/config_effective.py) | versione, valide, materialize o perfil e mantenha paths internos fora do schema |

## Adicionar um provider

1. Implemente `provider_name`, `capabilities`, `complete`, `stream` e
   `count_tokens`.
2. Traduza payload, autenticação, reasoning e eventos somente no adapter.
3. Normalize uso e erros para os contratos do core.
4. Registre um nome explícito na factory.
5. Cubra payload, response, streaming, timeout e capacidade ausente.
6. Documente configurações e diferenças reais do backend.

Não selecione comportamento pelo nome do modelo.

## Implementar uma extension stdio

### Registro e ativação CURRENT

O workflow canônico é programático: `ExtensionCatalogService` adiciona/observa
o manifest global; `WorkspaceExtensionService` habilita a extension e mantém
grants explícitos do workspace; `ApplicationExtensionBootstrap` resolve e
materializa descriptors/adapters para a application. A camada chamadora ainda
precisa fornecer `TaskAuthoritySnapshot` para a invocation model-actionable.

`llm-agent tools` le e escreve apenas `extensions/registry.json` por meio de
`ExtensionRegistry`. Essa CLI e uma superficie legada de compatibilidade e nao
configura o catalogo/workspace/bootstrap CURRENT. Para administracao canonica,
use `llm-agent extensions register|enable|grant|inspect`; `tools add/enable` nao
torna uma extension visivel ao planner.

O manifest deve declarar `id`, `version`, `protocol_version: "1.0"`,
`transport: "stdio"`, um `entrypoint` como lista de strings, `tools` como lista
não vazia com nomes únicos e `timeout_seconds` inteiro entre 1 e 3600. Tipos
inválidos não são convertidos silenciosamente.
Campos não documentados no objeto raiz ou nas declarações de tools são
rejeitados. Campos internos de `schema` seguem JSON Schema e não são tratados
como campos do manifest.
Cada tool com `transport: "stdio"` deve declarar a capability `process`;
o parser, catalogo, materializacao e adapter rejeitam o manifest antes de spawn
quando essa capability falta.

Para cada invocação, leia o único request JSONL de stdin e escreva exatamente
uma linha JSON não vazia em stdout; linhas vazias adicionais são ignoradas. A
resposta deve conter o mesmo `invocation_id` não vazio recebido no request.
`status` é opcional: sua ausência equivale a `succeeded`; quando presente,
somente `succeeded` ou `failed` são aceitos. JSON inválido, stdout extra, ID
ausente/divergente e status desconhecido são erros de protocolo. Envie logs e
diagnósticos para stderr.

Exit code diferente de zero é falha de processo e não pode ser mascarado por
uma resposta JSON aparentemente válida.

O adapter drena stdout e stderr concorrentemente. Cada stream tem limite de
1 MiB durante a produção; `MAX_STDERR_BYTES` é independente, e conteúdo
volumoso ou artifacts não devem ser enviados por stderr. stderr é apenas
diagnóstico e não faz parte do resultado. Timeout ou excesso de saída solicita
o encerramento da árvore do processo; falhas de terminação, Job Object e
drenagem são observáveis como `CLEANUP_ERROR`. Falha isolada na remoção do
status privado é apenas diagnóstico bounded e não substitui o resultado.
No Windows, um launcher interno bloqueado é associado ao Job Object antes de
receber autorização para iniciar a extension. Falha de criação ou associação
impede a execução da extension; não há fallback para execução direta. A
extension continua recebendo exatamente o protocolo público `1.0`, sem
qualquer mudança no manifest ou no código das extensions. POSIX continua
iniciando a extension diretamente. O launcher é um detalhe interno, não é
registrado como tool e não é uma sandbox de sistema operacional. Respostas
tardias são descartadas. O subprocesso recebe apenas o ambiente operacional
mínimo.

Antes do binding, a materialização revalida o manifest, seu fingerprint e a
união de capabilities exigidas; placeholders suportados no entrypoint são
`${extension_dir}` e `${python}`. Entradas `ready` são as únicas materializadas;
manifests inválidos, alterados ou com capabilities incompatíveis ficam
`blocked`/`disabled`/`orphaned` com diagnóstico tipado.

## Adicionar uma linguagem

1. Implemente `LanguageAdapter` sem conhecer o workflow.
2. Retorne `CodeAnalysis` normalizado, com nível e confiança.
3. Trate erro de sintaxe como diagnóstico.
4. Registre extensões no `LanguageRegistry`.
5. Se precisar executar ferramentas, crie um `ValidationProvider` separado.
6. Teste arquivo válido, inválido, grande e sem dependências opcionais.

O fallback textual continua obrigatório para extensões não suportadas.

## Adicionar uma skill

1. Implemente a interface mínima de `BaseSkill`.
2. Delegue a lógica a um caso de uso testável.
3. Adicione o `SkillSpec` ao catálogo.
4. Declare capacidades e efeitos de forma conservadora.
5. Receba workspace/scratch explicitamente; nunca aceite que argumentos escapem
   dessa raiz, mesmo quando um subprocesso usa `cwd` seguro.
6. Para qualquer efeito, receba `ApprovalPort`; não use `input()` no domínio.
7. Use injeção de dependência para gateway, contexto ou configuração.
8. Teste `SkillRegistry`, autorização da persona, confinamento e contrato de
   resultado.
9. Atualize [`skills.md`](skills.md).

Não há `SKILL_CONFIG`; `tool_metadata.py` é derivado do catálogo.

## Adicionar um workflow de código

Um workflow deve:

- receber `TaskExecutionContext`;
- selecionar o menor contexto de arquivos necessário;
- usar `ModelGateway` somente quando a operação exigir geração;
- parsear saída antes de construir `ChangeSet`;
- validar paths e hashes antes de escrever;
- preferir `edit` localizado com `base_hash` e `expected_text` a `modify`
  integral;
- submeter a prévia à política de confiança antes do commit;
- atravessar a fronteira `ToolInvocationGateway` com a authority da tarefa e a
  aprovação aplicável antes de expor o workflow model-actionable; a transação
  de código em si usa `ChangeApprovalPolicy`/`ApprovalPort` e não recebe uma
  autoridade implícita;
- classificar falhas antes de oferecer contexto a uma tentativa de reparo;
- devolver `succeeded`, `failed`, `cancelled`, `blocked` ou `unverified` com
  semântica exata;
- emitir artifacts e diagnósticos úteis à revisão.

Não adicione novos fluxos ao `AutoCoder`; ele é uma fachada de compatibilidade.
Se o caso de uso também for exposto na CLI e na skill, adicione-o primeiro a
`CodingApplicationService`, mantendo essas duas bordas finas.

## Adicionar uma tarefa concorrente

Cada `TaskNode` precisa declarar:

- ID estável e objetivo;
- dependências existentes;
- prioridade;
- recursos lógicos/paths em modo `read` ou `write`;
- capacidades/permissões;
- política de falha e metadata do executor.

Evite um recurso global sem necessidade, pois ele serializa todo o grafo. Para
operações de modelo no perfil de 8 GB, o gate compartilhado já impõe limite 1;
declarar também um recurso lógico `model:write` torna essa intenção visível no
grafo.

## Alterar configuração

Ao adicionar uma chave:

1. defina um fallback seguro em `agent/resources/default_config.json`;
2. inclua a chave na validação estrita de `config_schema.py`;
3. exponha a chave em `config.example.json`;
4. teste ausência, chave desconhecida, tipo inválido, limite e valor válido;
5. documente comportamento e migração em [`arquivos-raiz.md`](arquivos-raiz.md);
6. passe o valor pelo contexto/contrato, sem leitura global espalhada.

Paths de checkpoint, relatório, memória, cache e outros detalhes internos
pertencem a `AppPaths`/`WorkspacePaths`, não ao arquivo de preferência.

## Administracao canonica CURRENT

F1 disponibiliza a superficie de produto `llm-agent extensions`. Ela registra
o manifest no catalogo moderno e usa `WorkspaceExtensionService` para
`enable`, `disable`, `grant`, `revoke` e `inspect`. O comando legado
`llm-agent tools` continua separado e continua usando apenas
`extensions/registry.json`.

Para uma tarefa headless, a authority deve ser fornecida explicitamente fora
do modelo, por exemplo:

```powershell
llm-agent run --workspace C:\projeto --task-authority read --task-authority process --yes "Use a extension"
```

`--task-authority` cria um snapshot capability-wide, limitado a esta execucao
e ligado ao runtime atual; nao altera grants persistentes nem a persona. `--yes`
somente aprova efeitos; nao cria authority. Sem a flag, extensions permanecem
inelegiveis e o gateway nao inicia o processo.

## Gates antes de concluir

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest -q
.venv\Scripts\python.exe scripts\verify_installed_package.py
git diff --check
```

Para uma capacidade nova, adicione também cenário hermético em
`tests/fixtures/capabilities/` com oráculo de efeito real.
