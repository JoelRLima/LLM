# Guia de extensão

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
| nova linguagem | [`agent/code/languages/`](../agent/code/languages/) | implemente o adapter e declare limitações reais |
| descoberta do projeto | [`discovery.py`](../agent/code/discovery.py) | não execute scripts de manifests durante descoberta |
| análise/índice | [`intelligence.py`](../agent/code/intelligence.py) | retorne diagnósticos, não exceções globais por arquivo inválido |
| seleção de contexto | [`context_selection.py`](../agent/code/context_selection.py) | use sinais determinísticos, limites e hashes; não peça ao modelo para escolher arquivos |
| aplicação de mudanças | [`changes.py`](../agent/code/changes.py) | preserve path seguro, hash, diff e rollback |
| risco/confirmação | [`policy.py`](../agent/code/policy.py) | score explicável antes do commit; confirmação não substitui validação |
| classificação de falha | [`diagnostics.py`](../agent/code/diagnostics.py) | heurística determinística antes de qualquer retry por modelo |
| validator | [`validation.py`](../agent/code/validation.py) | `shell=False`, timeout, cancelamento; não instale pacotes |
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
| caminhos gerados | [`paths.py`](../agent/runtime/paths.py) | não espalhe literals de `runtime/` |
| configuração | [`config.py`](../agent/runtime/config.py) | valide tipo, faixa e fallback |

## Adicionar um provider

1. Implemente `provider_name`, `capabilities`, `complete`, `stream` e
   `count_tokens`.
2. Traduza payload, autenticação, reasoning e eventos somente no adapter.
3. Normalize uso e erros para os contratos do core.
4. Registre um nome explícito na factory.
5. Cubra payload, response, streaming, timeout e capacidade ausente.
6. Documente configurações e diferenças reais do backend.

Não selecione comportamento pelo nome do modelo.

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
5. Use injeção de dependência para gateway, contexto ou configuração.
6. Teste `SkillRegistry`, autorização da persona e contrato de resultado.
7. Atualize [`skills.md`](skills.md).

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

1. defina um fallback seguro em `DEFAULT_CONFIG` ou seção correspondente;
2. valide tipo e intervalo em `carregar_config()`;
3. exponha a chave em `config.example.json`;
4. teste ausência, tipo inválido, limite inválido e valor válido;
5. documente comportamento e migração em [`arquivos-raiz.md`](arquivos-raiz.md);
6. passe o valor pelo contexto/contrato, sem leitura global espalhada.

## Gates antes de concluir

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest -q
git diff --check
```

Para uma capacidade nova, adicione também cenário hermético em
`tests/fixtures/capabilities/` com oráculo de efeito real.
