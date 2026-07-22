# Testes e gates de qualidade

## Baseline atual

- pytest: suíte completa passando;
- Ruff: repositório limpo;
- mypy: pacote `agent`, scripts e fachadas da raiz analisados, sem erros e sem
  overrides por módulo;
- quality policy: zero exceções de complexidade/tamanho, fontes Python do projeto
  visíveis ao Git, limites arquiteturais, links locais válidos e textos em
  UTF-8 sem BOM;
- `git diff --check`: limpo; avisos de conversão LF/CRLF no Windows não são
  erros de whitespace.

Comandos:

```powershell
.venv\Scripts\python.exe scripts\check_quality.py
.venv\Scripts\python.exe -m ruff check .
.venv\Scripts\python.exe -m mypy
.venv\Scripts\python.exe -m pytest -q
git diff --check
```

## Organização

- `tests/unit/`: comportamento isolado por domínio (`code`, `llm`, `planning`,
  `runtime` e `skills`);
- `tests/integration/`: composição, compatibilidade e capacidades ponta a ponta;
- `tests/policy/`: gates aplicados ao próprio repositório;
- `tests/regression/`: contratos históricos e planos persistidos;
- `tests/fixtures/`: dados herméticos compartilhados.

## Capacidades e modelos

| Teste | Cobertura |
| :--- | :--- |
| [`test_capability_evaluation.py`](../tests/integration/test_capability_evaluation.py) | cenários herméticos, efeitos no filesystem, respostas, allowlist e limites |
| [`test_model_gateway.py`](../tests/unit/llm/test_model_gateway.py) | perfil legado/novo, payload no adapter, resposta, stream e capacidades |
| [`test_structured_output.py`](../tests/unit/llm/test_structured_output.py) | JSON Schema, GBNF, fallback por prompt e validação runtime |
| [`test_session.py`](../tests/unit/runtime/test_session.py) | histórico e fachada de compatibilidade da sessão |
| [`test_grammar.py`](../tests/unit/llm/test_grammar.py) | seleção/fallback GBNF do caminho legado |

As seis fixtures em `tests/fixtures/capabilities/` representam analyze,
generate, modify, repair, review e multitask. O evaluator não aceita uma
resposta textual convincente sem os efeitos esperados.

## Runtime, skills e multitarefa

| Teste | Cobertura |
| :--- | :--- |
| [`test_runtime_context.py`](../tests/unit/runtime/test_runtime_context.py) | perfil de 8 GB, contexto filho, gates, orçamento e métricas correlacionadas |
| [`test_skill_registry.py`](../tests/unit/skills/test_skill_registry.py) | catálogo, duplicatas, factories, nomes e política de capacidades |
| [`test_router.py`](../tests/unit/llm/test_router.py) | personas e derivação de skills por capacidade |
| [`test_task_graph.py`](../tests/unit/planning/test_task_graph.py) | schema, ciclo, prioridade, dependência, políticas, permissões, recursos, concorrência, `unverified` e checkpoint |
| [`test_hierarchical_executor.py`](../tests/unit/planning/test_hierarchical_executor.py) | dependências e gateway no caminho hierárquico legado |

## Engenharia de código

| Teste | Cobertura |
| :--- | :--- |
| [`test_code_intelligence.py`](../tests/unit/code/test_code_intelligence.py) | descoberta, AST Python, símbolos, diagnósticos, fallback textual e cache |
| [`test_changeset.py`](../tests/unit/code/test_changeset.py) | create/modify/edit/move, diff, hashes, âncoras, aliases, revalidação pré-commit e rollback |
| [`test_project_validation.py`](../tests/unit/code/test_project_validation.py) | sucesso, falha, indisponibilidade, move/delete, timeout e cancelamento |
| [`test_coding_workflows.py`](../tests/unit/code/test_coding_workflows.py) | análise sem modelo, review read-only, geração, rollback, `unverified`, reparo, bloqueio por confiança e capacidades multitarefa |
| [`test_code_assistance.py`](../tests/unit/code/test_code_assistance.py) | seleção por target/símbolo/import, diretórios, policy, classificador, `/code` e templates |

## Núcleo legado protegido

A suíte preserva a regressão das fases anteriores:

- estado por passo e checkpoint v2;
- `ExecutionGateway`, `PlanExecutor` e `StepExecutor`;
- fluxo reativo e hierárquico;
- cost guard, watchdog e cancelamento;
- file writer, shell e sandbox Python;
- memória, parsers, health check, configuração e CLI/orchestrator.

Os arquivos correspondentes estão nos grupos acima. O mypy descobre todo o
código de produção; `pyproject.toml` não possui overrides que desativem
`disallow_untyped_defs`.

## Como testar uma extensão

- provider: execute a mesma suíte de contrato do gateway atual;
- linguagem: cubra fonte válida, inválida, grande e fallback;
- validator: cubra comando ausente, falha, timeout e cancelamento;
- skill: cubra catálogo, construção, política e schema;
- workflow: verifique artifacts, diagnósticos, mudanças e rollback;
- TaskGraph: verifique ciclo, capabilities, recursos e política de falha;
- capacidade nova: adicione fixture com oráculo de efeito real.
