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
