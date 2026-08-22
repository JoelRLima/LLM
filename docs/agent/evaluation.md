# Evaluation

> **STATUS: CURRENT — PRIMARY HOME.** Este documento define o núcleo de eval,
> seus conjuntos curados e o nível de evidência que eles fornecem. Estratégia
> transversal de testes fica em [testes.md](../testes.md).

## Escopo e evidência

`agent/evaluation/` executa cenários herméticos em workspaces temporários e
avalia efeitos observáveis. Ele não substitui testes unitários nem demonstra
qualidade de um modelo real.

Há três níveis que não devem ser confundidos:

- **determinístico/scripted**: runtime real com decisões de modelo controladas;
- **reproducer focal**: node pytest que protege uma regressão específica;
- **real-model evidence**: execução repetível com backend/modelo declarado.

Somente o primeiro e o segundo estão cobertos pelo Block A atual.

## Capability Set

`CURATED_CAPABILITY_SET`, em `agent/evaluation/curated.py`, contém **9** cenários:

| ID | Propriedade observada |
| --- | --- |
| `cap-read` | leitura sem mutação |
| `cap-search` | busca sem mutação |
| `cap-modify-validate` | alteração pelo fluxo suportado e validação |
| `cap-shell` | superfície Shell/Git reduzida |
| `cap-extension` | extension stdio autorizada e preparada pelo harness |
| `cap-no-tool` | resposta sem tool |
| `cap-failure` | falha de capability sem falso sucesso |
| `cap-denial-recovery` | negação preserva o workspace |
| `cap-recovery` | rollback após alteração inválida |

O teste de integração executa oito cenários internos em conjunto e o cenário
de extension separadamente; a contagem do conjunto continua sendo 9.

## Regression Set

`CURATED_REGRESSION_SET`, em `agent/evaluation/regressions.py`, contém **8**
reprodutores focais: authority, terminalidade, ownership de processo stdio,
bypass do writer, Git/shell, identidade do protocolo stdio, probe instalado e
projeção de measurement.

O caso `writer-validation-bypass` aponta para
`test_model_planned_file_writer_is_excluded_with_auto_approval_and_no_mutation`:
uma decisão do modelo tenta `file_writer` sob `AutoApprove`; o teste confirma
que `code_task` permanece apresentado, que `file_writer` não aparece na persona
nem na planning view, que nenhuma tool é invocada e que o workspace não muda.
O caminho positivo de modificação é protegido separadamente pelos testes de
`code_task`.

## Block 7 installed acceptance projection

O gate canônico continua sendo `scripts/verify_installed_package.py`, que
constrói e executa o wheel fora do checkout. Com `--summary-json`, ele também
emite uma projeção limitada em `schema_version=1` para o relatório do Block 7;
essa projeção mapeia import/entry point, leitura/busca, resposta direta, shell e
Git, `code_task`, rollback, bypass do writer, stdio/authority, terminalidade,
measurement e isolamento do checkout. Ela não cria uma segunda jornada nem
reinterpreta o status canônico da aplicação.

## Block 7 H-series real-model acceptance

O conjunto versionado `B7-HSERIES-V1.0` contém exatamente H1–H12 e é executado
pelo mesmo `CapabilityEvaluator` usado pelos cenários existentes. O recorder de
modelo é observacional: não altera requests, respostas, retries, orçamento ou
status canônico. Cada repetição usa workspace, home e identidade de tarefa
novos, com fixture determinístico e evidência limitada/sanitizada.

A política final exige cinco repetições válidas para H2. H1 e H3–H12 começam
com três; cenários unânimes terminam em três e cenários mistos recebem
exatamente duas repetições adicionais, sem rerun-until-pass. Falhas válidas são
classificadas como `MODEL_VARIANCE`, `MODEL_CAPABILITY`, `HARNESS_DEFECT`,
`RUNTIME_DEFECT`, `ENVIRONMENTAL` ou `UNKNOWN`; a análise final deve deixar
`UNKNOWN` em zero para falhas.

O epoch `B7-REAL-MODEL-EPOCH-1` avaliou o perfil Qwen local declarado
`local_8gb` (`openai_compatible`, modelo `default`, temperatura `0.2`,
`max_tokens=2048`, timeout de 300 segundos). Foram registrados 43 runs válidos:
14 passaram e 29 foram classificados como `MODEL_CAPABILITY`; o veredicto
foi `NOT_RELEASE_READY_MODEL`. Esse resultado é específico ao fingerprint do
modelo/configuração testado e não declara portabilidade para outros provedores,
modelos ou classes de tarefas.

O diagnóstico instalado offline passou fora do checkout, mas a aceitação limpa
foi limitada localmente por timeout de 180 segundos durante a construção do
wheel; o diagnóstico offline não substitui a aceitação com dependências limpas.
Os relatórios bounded ficam em `.audit-local/out/` e o rerun 7B autorizado é:

```powershell
.venv\Scripts\python.exe scripts\run_block7.py --phase 5 --qwen-loaded --profile local_8gb --epoch B7-REAL-MODEL-EPOCH-2 --output reports\acceptance\block7\epoch-2.json
```

## Block 7 corrective campaign contract

The Sol corrective runner is the canonical adaptive state machine: H1 counts
paired scenario repetitions separately from arm executions; H2 always has five
valid repetitions; other scenarios stop after unanimous three or extend mixed
three-of-three samples by exactly two. Environmental attempts are preserved but
excluded from the valid denominator. Failure attribution requires explicit
evidence and never defaults a real-model failure to `MODEL_CAPABILITY`.

The report carries a semantic candidate manifest for runtime, evaluation,
fixtures, provider configuration, and the campaign runner, plus a stable
non-secret model/config fingerprint. The deterministic analyzer computes rates,
incidents, causal counts, and one policy verdict from the preserved records;
it does not call an LLM judge. `B7-REAL-MODEL-EPOCH-1` remains
`DIAGNOSTIC / SUPERSEDED_FOR_FINAL_SCORING` and is never combined with
`B7-REAL-MODEL-EPOCH-2`.

The command above is authorization-gated. Deterministic preparation must stop
at `BLOCK 7 CORRECTIVE READY — QWEN RELOAD REQUIRED`; the command is not run
until the user confirms that Qwen has been reloaded and explicitly authorizes
the new epoch.

## Contratos e execução

- `CapabilityScenario` declara objetivo, arquivos iniciais, expectativas e
  metadata de preparação.
- `AgentApplicationScenarioExecutor` adapta a composition root real ao cenário;
  preparação específica (por exemplo, um repositório Git ou uma extension) é
  fornecida pelo callback do harness, não inferida automaticamente do metadata.
- `CapabilityEvaluator` cria workspace vazio, captura hashes antes/depois,
  executa e aplica grading determinístico.
- `ScenarioReport` e `EvaluationSetReport` preservam falhas, observação,
  mudanças e agregados; a exportação é JSON serializável.
- caminhos dos fixtures são relativos e validados; quando
  `allowed_changed_files` declara uma allowlist, mudanças fora dela fazem o
  cenário falhar. `unchanged_files` protege somente os arquivos explicitamente
  listados; caches transitórios (`.git`, `.pytest_cache`, `__pycache__` e
  `.temp_analysis`) são ignorados pelo snapshot do harness.

Measurement é coletado pelo executor e projetado no export de eval; o dado
continua pertencendo ao runtime/reporting, não a uma métrica inventada pelo
grader. Veja [reporting.md](reporting.md).

## Estado do Marco 3

```text
Block A = GREEN LOCAL
Block B = NOT COMPLETED
Block C = NOT COMPLETED
Standalone V1 = NOT YET DECLARED
```

Block A entrega core reutilizável, 9 capability scenarios, 8 regression cases,
grading determinístico, agregação/export e reuso de measurement. Não declara
benchmark real-model, comparação de planners/modelos, release gate final nem
fresh-wheel de V1.
