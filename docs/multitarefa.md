# TaskGraph e multitarefa local

## Definição

Multitarefa neste projeto significa executar unidades de trabalho de um DAG com
dependências, estado, contexto, recursos e resultado próprios. Não significa
criar agentes distribuídos ou várias sessões de modelo concorrentes.

## Contratos

`TaskNode` contém:

- `node_id` e objetivo;
- `depends_on`;
- prioridade `low`, `medium`, `high` ou `critical`;
- recursos com modo `read`/`write`;
- capacidades/permissões;
- política de falha;
- metadados do caso de uso.

Exemplo aceito por `code_task`:

```json
{
  "action": "multitask",
  "objective": "Analisar dois módulos e aplicar uma correção",
  "graph": {
    "nodes": [
      {
        "id": "api",
        "objective": "Analisar api.py",
        "resources": [{"name": "api.py", "mode": "read"}],
        "capabilities": ["read", "analyze"],
        "metadata": {"action": "analyze", "targets": ["api.py"]}
      },
      {
        "id": "model",
        "objective": "Analisar model.py",
        "resources": [{"name": "model.py", "mode": "read"}],
        "capabilities": ["read", "analyze"],
        "metadata": {"action": "analyze", "targets": ["model.py"]}
      },
      {
        "id": "fix",
        "objective": "Aplicar a menor correção coerente com as análises",
        "depends_on": ["api", "model"],
        "resources": [
          {"name": "model", "mode": "write"},
          {"name": "api.py", "mode": "write"}
        ],
        "capabilities": ["read", "write", "process"],
        "metadata": {"action": "modify", "targets": ["api.py"]}
      }
    ]
  }
}
```

O recurso lógico `model` com modo `write` serializa nós que geram texto. Mesmo
sem ele, contextos filhos compartilham `ModelConcurrencyGate`, que tem limite 1
no perfil de 8 GB.

## Templates determinísticos

Para operações comuns, não é necessário pedir ao modelo que invente o DAG.
`agent/code/task_templates.py` oferece:

| Template | Grafo produzido |
| :--- | :--- |
| `parallel_analyze` | um nó read/analyze por target, sem dependências |
| `parallel_review` | um nó read/analyze por target, sem dependências |
| `analyze_then_modify` | análises independentes e um nó de escrita dependente de todas |

IDs são derivados deterministicamente do target, duplicatas são removidas e
recursos/capacidades são declarados pelo template. Exemplo:

```text
/code template parallel_analyze api.py models.py
/code template analyze_then_modify api.py models.py -- Preserve as APIs e elimine a duplicação
```

O template elimina o planejamento por LLM, mas não elimina validação,
confirmação ou o gate de modelo. Em `analyze_then_modify`, as dependências
garantem a ordem; o nó final relê o contexto determinístico do estado atual.

## Validação do grafo

Antes de executar qualquer efeito, `TaskGraphValidator` rejeita:

- grafo vazio;
- IDs vazios ou duplicados;
- dependência ausente;
- autodependência;
- ciclo;
- recurso sem nome.

Prioridade não ignora dependência. Ela desempata apenas nós prontos.

## Estados

- `pending`;
- `running`;
- `succeeded`;
- `unverified`;
- `failed`;
- `blocked`;
- `cancelled`.

`unverified`, falha, bloqueio ou cancelamento bloqueiam dependentes por padrão.
`continue` permite que um nó execute mesmo com dependência terminal sem sucesso;
`fail_fast` cancela nós ainda pendentes depois da falha.

## Isolamento

Cada nó recebe `TaskExecutionContext.child()` com:

- novo `task_id`;
- `parent_task_id` e `node_id`;
- permissões próprias;
- metadados copiados;
- mesmo token de cancelamento do pai;
- mesmos sinks correlacionados;
- mesmo gate de modelo;
- mesmo gate de processos e orçamento de chamadas ao modelo;
- mesmos limites imutáveis.

Resultados são `TaskResult` imutáveis. A agregação segue a ordem determinística
do batch, não a ordem em que futures terminam.

## Concorrência e recursos

O scheduler monta batches dos nós prontos:

- duas leituras do mesmo recurso são compatíveis;
- leitura e escrita sobre recursos sobrepostos conflitam;
- duas escritas sobre o mesmo caminho conflitam;
- escrever um diretório conflita com filhos desse diretório;
- um batch nunca excede `max_workers`;
- cancelamento impede novos nós.

Para o perfil `low_vram_8gb`, a skill usa `max_io_concurrency=2`. Processos de
validação continuam sequenciais e chamadas de modelo usam gate 1.

Antes de qualquer efeito, o scheduler rejeita nós que solicitam capacidades não
concedidas pelo contexto pai. O executor de código também verifica que a action
declarou todas as capacidades necessárias; um nó não pode ganhar permissões por
omissão ou por metadata.

## Checkpoint

`TaskGraphState.to_checkpoint_dict()` persiste grafo, estados e erros no schema
1 do TaskGraph. Ao restaurar, nós `running` voltam para `pending`; concluídos não
são repetidos. Esse schema é separado do checkpoint v2 do plano legado.

## Integração hierárquica

`MacroPlan` é convertido em `TaskGraph` antes de ser aceito. O executor
hierárquico atual usa ordem topológica e não executa um macro-passo cujas
dependências falharam. Ele permanece sequencial porque o microplanejamento
legado compartilha sessão e `AgentState`; paralelizá-lo violaria o isolamento.

Os workflows novos podem usar `MultitaskCodingService`, que já cria contextos
filhos isolados e usa o scheduler concorrente.

## Limitações

- execução é local, em um processo;
- não existe coordenação distribuída;
- locks são lógicos ao scheduler, não locks entre processos distintos;
- processos já iniciados só são canceláveis dentro das garantias do
  `ProcessRunner`;
- concorrência não torna seguro executar código não confiável sem sandbox forte.

## Testes relacionados

- `tests/unit/planning/test_task_graph.py`;
- `tests/unit/planning/test_hierarchical_executor.py`;
- `tests/unit/runtime/test_runtime_context.py`;
- `tests/unit/code/test_coding_workflows.py`.
- `tests/unit/code/test_code_assistance.py`.
