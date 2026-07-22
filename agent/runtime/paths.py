"""
paths.py

Ponto único de definição de onde o agente escreve arquivos e diretórios
GERADOS EM RUNTIME: logs, checkpoint, memória da sessão, métricas,
relatórios de tarefa, backups de memória e pontos de restauração de
arquivos.

Antes deste módulo, cada um desses caminhos era uma string literal
duplicada e espalhada por vários arquivos diferentes (`logger.py`,
`memory.py`, `workspace.py`, `orchestrator.py`, `commands.py`, `cli.py`,
`benchmark.py`, `config.py`...), poluindo a raiz do projeto com uma
dezena de arquivos/pastas gerados automaticamente, misturados com o
código-fonte. Agora todos vivem dentro de um único diretório
(`RUNTIME_DIR`, por padrão `runtime/`), e qualquer módulo que precise de
um desses caminhos importa a constante correspondente daqui, em vez de
repetir a string.

`RUNTIME_DIR` pode ser sobrescrito via variável de ambiente
`AGENT_RUNTIME_DIR` — útil para testes (isolar cada teste em um
diretório temporário), containers, ou múltiplas instâncias do agente
rodando em paralelo na mesma máquina.

NÃO inclui `.temp_analysis/` (usado por `file_reader.py`/`file_writer.py`
para o workspace isolado de edição): esse diretório é relativo ao
`base_dir` do projeto que o agente está ANALISANDO, não ao próprio
agente — pode ser um diretório completamente diferente do runtime do
agente, então propositalmente não faz parte deste módulo.
"""
import os

RUNTIME_DIR = os.environ.get("AGENT_RUNTIME_DIR", "runtime")

# --- Logging ---
LOG_FILE = os.path.join(RUNTIME_DIR, "agent.log")

# --- Checkpoint da tarefa (retomada após interrupção) ---
CHECKPOINT_FILE = os.path.join(RUNTIME_DIR, "agent_checkpoint.json")

# --- Métricas (uma linha JSON por chamada ao modelo) ---
METRICS_FILE = os.path.join(RUNTIME_DIR, "agent_metrics.jsonl")

# --- Memória persistente da sessão do agente ---
MEMORY_FILE = os.path.join(RUNTIME_DIR, "agent_memory.json")
MEMORY_DB_FILE = os.path.join(RUNTIME_DIR, "agent_memory.db")
MEMORY_BACKUP_DIR = os.path.join(RUNTIME_DIR, "memory_backups")

# --- Pontos de restauração de arquivos (rollback do WorkspaceManager) ---
# Antes, workspace.py reaproveitava o mesmo nome "memory_backups" que
# memory.py usa para backups de memória — dois conceitos diferentes
# (rollback de edição de arquivo vs. backup de memória) compartilhando o
# mesmo nome de pasta por coincidência. Agora cada um tem o seu.
RESTORE_POINTS_DIR = os.path.join(RUNTIME_DIR, "restore_points")

# --- Histórico de chat salvo manualmente (/save, /load) ---
CHAT_HISTORY_FILE = os.path.join(RUNTIME_DIR, "chat_history.json")

# --- Relatórios de tarefa (Task Report) ---
REPORTS_DIR = os.path.join(RUNTIME_DIR, "reports")

# --- Rastreamento de execução hierárquica (TaskTracker) ---
TASK_TRACKER_JSON = os.path.join(RUNTIME_DIR, "task_tracker.json")
TASK_TRACKER_MD = os.path.join(RUNTIME_DIR, "task_tracker.md")

# --- Resultados do benchmark headless ---
BENCHMARK_RESULTS_FILE = os.path.join(RUNTIME_DIR, "benchmark_results.json")

# --- Relatório do diagnóstico de saúde (/doctor) ---
HEALTH_REPORT_FILE = os.path.join(RUNTIME_DIR, "health_report.json")


def ensure_runtime_dir() -> None:
    """Garante que `RUNTIME_DIR` exista antes de qualquer escrita.

    Chamado por `logger.py` na inicialização do processo (o logging é o
    primeiro subsistema a escrever em disco). Os demais consumidores
    (memory.py, workspace.py, checkpoint_manager.py, etc.) já criam seus
    próprios subdiretórios sob demanda via `os.makedirs(..., exist_ok=True)`,
    mas chamar esta função primeiro garante que `RUNTIME_DIR` já exista
    mesmo antes deles.
    """
    os.makedirs(RUNTIME_DIR, exist_ok=True)
