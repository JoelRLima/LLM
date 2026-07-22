# Módulo `agent/` — reporting

> Parte da documentação técnica do projeto. Veja o [índice](../README.md).

---

## 4.26. [incremental_summarizer.py](../../agent/reporting/incremental_summarizer.py) 🆕
Acumulador/sumarizador incremental de resultados parciais durante execução hierárquica.
* **`IncrementalSummarizer(summarize_fn, max_items, max_chars)`**: Recebe uma função de sumarização injetada. Acumula itens de texto e os condensa periodicamente em resumos para evitar explosão de contexto.
* **`add(item)`**: Adiciona um resultado parcial.
* **`force_flush()`**: Força a condensação de itens pendentes.
* **`get_accumulated_content()`**: Retorna todo o conteúdo (resumos + itens recentes) para a consolidação final.

---

## 4.27. [task_report.py](../../agent/reporting/task_report.py) 🆕
Construtor do Relatório da Tarefa — registro de auditoria consolidado ao final de cada execução.
* **`TaskReportBuilder(config)`**: Constrói um dicionário estruturado com `task_id`, `objective`, `success`, `steps`, `replan_events`, `metrics`, `errors` e `final_answer_preview`.
* **`save_report(report, format, path)`**: Persiste o relatório em JSON (padrão) ou Markdown, com gravação atômica.
* Totalmente desacoplado do `Orchestrator` — depende apenas do estado público de `AgentState` e métricas.

---

## 4.28. [task_tracker.py](../../agent/reporting/task_tracker.py) 🆕
Rastreador de progresso da execução hierárquica. Mantém um arquivo JSON estruturado (fonte de verdade) e renderiza um arquivo Markdown para leitura humana.
* **`TaskTracker(json_path, md_path)`**: Inicializa os caminhos dos artefatos.
* **`start(objective, steps, metadata)`**: Inicia o tracking com os passos do `MacroPlan`.
* **`mark_running / mark_completed / mark_failed / mark_skipped(step_id)`**: Atualiza o status de cada passo.
* **`finish_success / finish_failure(summary)`**: Finaliza o tracking global.
* **Enums**: `StepStatus` (PENDING, RUNNING, COMPLETED, FAILED, SKIPPED) e `TaskStatus` (RUNNING, COMPLETED, FAILED).
* Todas as gravações são atômicas e falhas de I/O nunca escapam para o chamador.

---

## 4.37. [metrics_recorder.py](../../agent/reporting/metrics_recorder.py) 🆕
`MetricsRecorder` — extraído do `Orchestrator` (mais um pedaço de responsabilidade que estava acumulada nele).
* **`log_metric(entry)`**: adiciona uma entrada JSON ao final de `runtime/agent_metrics.jsonl`.
* **`count_lines() -> int`**: conta linhas existentes — usado como marca d'água no início de uma tarefa.
* **`get_entries_since(start_line) -> List[dict]`**: lê apenas as entradas gravadas após a marca d'água, ou seja, as produzidas durante a tarefa atual.
