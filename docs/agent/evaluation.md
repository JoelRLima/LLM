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

O primeiro e o segundo são evidência local determinística. Evidência de
modelo real continua sendo uma etapa separada, com identidade observada,
aceitação instalada e autorização explícita para o epoch correspondente.

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

## Installed acceptance projection

The clean-installed summary is candidate-bound and is written to
`.audit-local/out/installed-acceptance.json`; offline diagnostics never satisfy
the final release precondition.

O gate canônico continua sendo `scripts/verify_installed_package.py`, que
constrói e executa o wheel fora do checkout. Com `--summary-json`, ele também
emite uma projeção limitada em `schema_version=2` para o relatório da campanha;
essa projeção mapeia import/entry point, leitura/busca, resposta direta, shell e
Git, `code_task`, rollback, bypass do writer, stdio/authority, terminalidade,
measurement e isolamento do checkout. Ela não cria uma segunda jornada nem
reinterpreta o status canônico da aplicação.

O modo de aceitação limpa com resolução de dependências é obrigatório para
qualquer veredicto final. `--offline-diagnostic` é útil para diagnóstico, mas
não satisfaz esse requisito. A execução aceita deve ocorrer fora do checkout e
a projeção limitada deve ser preservada com `--summary-json` quando o resultado
for consumido pela campanha.

## Deterministic campaign closure

O envelope versionado da campanha é validado antes de calcular taxas,
classificações ou veredicto. Cada relatório e cada run preservam separadamente
`declared_model_identity` e `observed_model_identity`; identidade observada
indisponível é explícita e mantém o resultado inconclusivo, nunca é inferida a
partir de sucesso textual. A métrica de `tool_calls` vem do measurement/budget
canônico do runtime, não de histórico ou cache projetado.

O caminho sem modelo real é executado por:

```powershell
.venv\Scripts\python.exe scripts\run_evaluation_campaign.py --mode dry-run --output .audit-local\out\evaluation-corrective-dry-run.json --write-config
.venv\Scripts\python.exe scripts\run_evaluation_campaign.py --mode adversarial-audit --output .audit-local\out\evaluation-adversarial-audit.json
.venv\Scripts\python.exe scripts\run_evaluation_campaign.py --mode corrective-ready --output .audit-local\out\evaluation-corrective-ready.json
```

Esse caminho deve concluir H1–H19 com 164 execuções válidas, sem chamada de
modelo vivo. Seu veredicto é deliberadamente `INCONCLUSIVE` com
`REAL_MODEL_EPOCH_REQUIRED`; a execução live-model permanece fora da
preparação determinística e exige autorização explícita.

## H-series real-model acceptance

O conjunto versionado `H-SERIES-V1.5` contém exatamente H1–H19 e é executado
pelo mesmo `CapabilityEvaluator` usado pelos cenários existentes. O recorder de
modelo é observacional: não altera requests, respostas, retries, orçamento ou
status canônico. Cada repetição usa workspace, home e identidade de tarefa
novos, com fixture determinístico e evidência limitada/sanitizada.

A política final exige cinco repetições válidas para H2. H1 e H3–H19 começam
com três; cenários unânimes terminam em três e cenários mistos recebem
exatamente duas repetições adicionais, sem rerun-until-pass. Falhas válidas são
classificadas como `MODEL_VARIANCE`, `MODEL_CAPABILITY`, `HARNESS_DEFECT`,
`RUNTIME_DEFECT`, `ENVIRONMENTAL` ou `UNKNOWN`; a análise final deve deixar
`UNKNOWN` em zero para falhas.

O epoch `REAL-MODEL-EPOCH-1` avaliou o perfil Qwen local declarado
`local_8gb` (`openai_compatible`, modelo `default`, temperatura `0.2`,
`max_tokens=2048`, timeout de 300 segundos). Foram registrados 43 runs válidos:
14 passaram e 29 foram classificados como `MODEL_CAPABILITY`; o veredicto
foi `NOT_RELEASE_READY_MODEL`. Esse resultado é específico ao fingerprint do
modelo/configuração testado e não declara portabilidade para outros provedores,
modelos ou classes de tarefas.

Os relatórios bounded ficam em `.audit-local/out/`. A aceitação limpa atual
passou fora do checkout com resolução de dependências; o diagnóstico offline
continua sendo insuficiente por si só. Um rerun de modelo real permanece
authorization-gated:

```powershell
.venv\Scripts\python.exe scripts\run_evaluation_campaign.py --mode live-model --qwen-loaded --profile local_8gb --epoch REAL-MODEL-EPOCH-2 --output .audit-local\out\real-model-epoch-2.json
```

## Corrective campaign contract

The corrective runner is the canonical adaptive state machine: H1 counts
paired scenario repetitions separately from arm executions; H2 always has five
valid repetitions; other scenarios stop after unanimous three or extend mixed
three-of-three samples by exactly two. Environmental attempts are preserved but
excluded from the valid denominator. Failure attribution requires explicit
evidence and never defaults a real-model failure to `MODEL_CAPABILITY`.

The report carries a semantic candidate manifest for runtime, evaluation,
fixtures, provider configuration, and the campaign runner, plus a stable
non-secret model/config fingerprint. The deterministic analyzer computes rates,
incidents, causal counts, and one policy verdict from the preserved records;
it does not call an LLM judge. `REAL-MODEL-EPOCH-1` remains
`DIAGNOSTIC / SUPERSEDED_FOR_FINAL_SCORING` and is never combined with
`REAL-MODEL-EPOCH-2`.

The command above is authorization-gated. Deterministic preparation must stop
at the deterministic readiness artifact; the live-model command is not run
until the user confirms that Qwen has been reloaded and explicitly authorizes
the new epoch.

## R1–R8 closure ownership

- R1: exact source evidence is distinct from derived/lossy projections and
  checkpoint reentry cannot upgrade fidelity;
- R2/R3: nested execution shares the parent ownership tree, budget and
  cancellation, while the invocation gateway owns terminal publication and
  quiescence;
- R4/R5: scheduler resources come from declared effect intent and canonical
  observation commit is atomic;
- R6: requested effects, prohibited effects and observed footprints are
  evaluated separately, including read-only shell semantics;
- R7: model-proposed obligations require trusted admission and causal evidence
  before they can become durable;
- R8: the analyzer consumes the preserved envelope, installed acceptance,
  candidate/model identities and canonical measurements without reconstructing
  facts from history.

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

## LONG_HORIZON_V1 (Wave 15)

`scripts/run_evaluation_campaign.py --mode long-horizon-dry-run` estende o
harness scripted com os cenarios LH15-01--LH15-16. O conjunto e deterministico
e usa os owners canonicos de frontier, receipt, observacao, pressao,
convergencia e checkpoint, sem chamar Qwen ou outro modelo vivo. LH15-13--16
exercitam as rotas reais de PlanExecutor, TaskGraphScheduler e ContextManager
com gateways deterministas; os contadores vem de budgets e eventos do runtime. O
relatorio bounded registra unidades logicas, model/tool calls, decisoes
FULL/COMPACT, avancos, ciclos, cache reuse, rehydration, replans, terminal
reason, arquivos alterados e integridade do candidate/workspace.

O gate esperado e `LH15 = 16/16`, `unknown = 0` e `qwen_used = false`. Os
cenarios incluem preservacao da frontier durante pressure, overflow obrigatorio,
resume com plateau, paralelo/TaskGraph e a sequencia reversivel
`{A} -> {A,B} -> {A,C} -> {A,B} -> {A,C}`. Isso comprova invariantes
deterministicos do runtime, nao qualidade de modelo real.

LH15-13 mede tres slots (cache, leitura fisica, rehydration), exatamente dois
ciclos sem progresso e nenhum credito novo. LH15-14 recria filhos no scheduler
real ate seis ciclos de plateau no root, sem credito ou cobranca delegada dupla.
LH15-15 executa 32 unidades, verifica um milestone omitido da projecao externa
e depois alterna B/C ate plateau sem permitir credito por restauracao.
LH15-16 executa 24 leituras com requests canonicos repetidos, poison de summary
ativo, respostas deterministas corretas e historico duravel preservado a cada
ajuste de contexto. Cache reuse significa zero physical tool dispatch; hashing
local continua sendo I/O de controle de freshness.

## Fechamento PRE-V1 (Wave 15.5)

O candidato determinístico fecha C2, C3 e C9 sem alterar os owners anteriores.
O gate de wheel instalado prova a execução fora do checkout e reconstrói,
diretamente do trace persistido, o `run_audit_receipt` de builtin, extension
bem-sucedida, negação e credencial. A projeção mantém separadas identidade
declarada/observada, authority/approval, efeitos canônicos, validação,
terminalidade e referências de artifact bounded; ela não é autoridade nem
transporta o valor de uma credencial.

O workspace de toda rota task-producing headless é exigido antes da aplicação,
modelo, tools e mutação. O chat interativo conserva o chooser explícito, e as
rotas read-only/admin conservam seus owners. A aceitação determinística usa
somente o gateway scripted ou fixture HTTP local. A campanha H1–H19 continua
com 164 execuções válidas e veredicto `INCONCLUSIVE`/
`REAL_MODEL_EPOCH_REQUIRED`; isso não é aceitação de modelo real.

## Estado atual

```text
Evaluation core = GREEN LOCAL
Deterministic campaign = GREEN LOCAL
Real-model campaign = GATED / NOT RUN
Standalone V1 = NOT YET DECLARED
```

O core de avaliação entrega 9 capability scenarios, 8 regression cases,
grading determinístico, agregação/export e reuso de measurement. A preparação
determinística da campanha e a aceitação fresh-wheel estão verdes localmente;
isso não declara benchmark real-model nem release final de modelo.
