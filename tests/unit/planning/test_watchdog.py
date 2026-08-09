"""
Testes para agent/watchdog.py — Watchdog de execução.

Cobre: timeout global, detecção de loop sem progresso, detecção de falhas
consecutivas com o mesmo erro, e ponto de entrada check_all().
"""
import time

import pytest

from agent.watchdog import Watchdog

# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
def config() -> dict:
    return {
        "max_task_wall_seconds": 5,
        "max_repeated_no_progress": 3,
        "max_consecutive_same_error": 3,
    }


@pytest.fixture
def tool_history_ok() -> list:
    """Histórico com uma execução bem‑sucedida."""
    return [
        {
            "tool": "file_reader",
            "args": {"file_path": "test.txt"},
            "result": {"ok": True, "data": "conteúdo"},
        }
    ]


@pytest.fixture
def tool_history_repeated_fail() -> list:
    """Histórico com 3 falhas idênticas consecutivas."""
    return [
        {
            "tool": "file_reader",
            "args": {"file_path": "x.txt"},
            "result": {"ok": False, "error": "Arquivo não encontrado"},
        },
        {
            "tool": "file_reader",
            "args": {"file_path": "x.txt"},
            "result": {"ok": False, "error": "Arquivo não encontrado"},
        },
        {
            "tool": "file_reader",
            "args": {"file_path": "x.txt"},
            "result": {"ok": False, "error": "Arquivo não encontrado"},
        },
    ]


# ---------------------------------------------------------------------------
# 1. Timeout global
# ---------------------------------------------------------------------------

def test_timeout_global_nao_atingido(config: dict) -> None:
    start = Watchdog.start_task()
    result = Watchdog.check_global_timeout(start, config)
    assert result is None


def test_timeout_global_atingido(config: dict) -> None:
    start = time.monotonic() - (config["max_task_wall_seconds"] + 1)
    result = Watchdog.check_global_timeout(start, config)
    assert result is not None
    assert "Timeout global" in result


def test_timeout_global_sem_start_time(config: dict) -> None:
    result = Watchdog.check_global_timeout(None, config)
    assert result is None


# ---------------------------------------------------------------------------
# 2. Loop sem progresso
# ---------------------------------------------------------------------------

def test_no_progress_loop_ok(config: dict, tool_history_ok: list) -> None:
    result = Watchdog.check_no_progress_loop(tool_history_ok, config)
    assert result is None


def test_no_progress_loop_detectado(config: dict, tool_history_repeated_fail: list) -> None:
    result = Watchdog.check_no_progress_loop(tool_history_repeated_fail, config)
    assert result is not None
    assert "Loop sem progresso" in result


def test_no_progress_ignores_ephemeral_invocation_ids(config: dict) -> None:
    history = [
        {"tool": "file_reader", "args": {"file_path": "x"}, "result": {"ok": True, "status": "succeeded", "data": "same", "invocation_id": f"id-{index}"}}
        for index in range(3)
    ]
    assert Watchdog.check_no_progress_loop(history, config) is not None


def test_no_progress_keeps_semantic_result_differences(config: dict) -> None:
    history = [
        {"tool": "file_reader", "args": {"file_path": "x"}, "result": {"ok": True, "data": value, "invocation_id": f"id-{index}"}}
        for index, value in enumerate(("one", "two", "three"))
    ]
    assert Watchdog.check_no_progress_loop(history, config) is None


def test_signature_is_invariant_to_mapping_insertion_order() -> None:
    first = Watchdog._signature(
        "file_reader",
        {"file_path": "x", "options": {"b": 2, "a": 1}},
        {"ok": True, "data": {"z": 3, "a": 1}},
    )
    second = Watchdog._signature(
        "file_reader",
        {"options": {"a": 1, "b": 2}, "file_path": "x"},
        {"data": {"a": 1, "z": 3}, "ok": True},
    )
    assert first == second


def test_signature_strips_only_framework_ids_at_result_root() -> None:
    first = Watchdog._signature(
        "file_reader", {"file_path": "x"},
        {"ok": True, "invocation_id": "one", "attempt_id": "a", "data": {"value": 1}},
    )
    second = Watchdog._signature(
        "file_reader", {"file_path": "x"},
        {"ok": True, "invocation_id": "two", "attempt_id": "b", "data": {"value": 1}},
    )
    assert first == second


def test_signature_preserves_nested_payload_ids_and_argument_semantics() -> None:
    first = Watchdog._signature(
        "file_reader", {"payload": {"invocation_id": "one"}},
        {"ok": True, "data": {"invocation_id": "one"}},
    )
    second = Watchdog._signature(
        "file_reader", {"payload": {"invocation_id": "two"}},
        {"ok": True, "data": {"invocation_id": "two"}},
    )
    assert first != second


def test_no_progress_loop_historico_insuficiente(config: dict) -> None:
    history = [
        {"tool": "a", "args": {}, "result": {"ok": False, "error": "x"}},
        {"tool": "a", "args": {}, "result": {"ok": False, "error": "x"}},
    ]
    result = Watchdog.check_no_progress_loop(history, config)
    assert result is None


# ---------------------------------------------------------------------------
# 3. Falhas consecutivas com o mesmo erro
# ---------------------------------------------------------------------------

def test_consecutive_same_error_ok(config: dict, tool_history_ok: list) -> None:
    result = Watchdog.check_consecutive_same_error(tool_history_ok, config)
    assert result is None


def test_consecutive_same_error_detectado(config: dict) -> None:
    history = [
        {"tool": "x", "args": {}, "result": {"ok": False, "error": "Erro A"}},
        {"tool": "x", "args": {"diferente": True}, "result": {"ok": False, "error": "Erro A"}},
        {"tool": "x", "args": {}, "result": {"ok": False, "error": "Erro A"}},
    ]
    result = Watchdog.check_consecutive_same_error(history, config)
    assert result is not None
    assert "falhas consecutivas" in result.lower()


def test_consecutive_same_error_com_sucesso_no_meio(config: dict) -> None:
    history = [
        {"tool": "x", "args": {}, "result": {"ok": False, "error": "Erro A"}},
        {"tool": "x", "args": {}, "result": {"ok": True, "data": "ok"}},
        {"tool": "x", "args": {}, "result": {"ok": False, "error": "Erro A"}},
    ]
    result = Watchdog.check_consecutive_same_error(history, config)
    assert result is None


# ---------------------------------------------------------------------------
# 4. check_all — ponto de entrada único
# ---------------------------------------------------------------------------

def test_check_all_ok(config: dict, tool_history_ok: list) -> None:
    start = Watchdog.start_task()
    result = Watchdog.check_all(start, tool_history_ok, config)
    assert result is None


def test_check_all_timeout(config: dict) -> None:
    start = time.monotonic() - (config["max_task_wall_seconds"] + 1)
    result = Watchdog.check_all(start, [], config)
    assert result is not None
    assert "Timeout" in result


def test_check_all_loop(config: dict, tool_history_repeated_fail: list) -> None:
    start = Watchdog.start_task()
    result = Watchdog.check_all(start, tool_history_repeated_fail, config)
    assert result is not None
    assert "Loop sem progresso" in result


def test_check_all_consecutive_errors(config: dict) -> None:
    start = Watchdog.start_task()
    history = [
        {"tool": "x", "args": {"file": "a.txt"}, "result": {"ok": False, "error": "Erro B"}},
        {"tool": "x", "args": {"file": "b.txt"}, "result": {"ok": False, "error": "Erro B"}},
        {"tool": "x", "args": {"file": "c.txt"}, "result": {"ok": False, "error": "Erro B"}},
    ]
    result = Watchdog.check_all(start, history, config)
    assert result is not None
    assert "falhas consecutivas" in result.lower()


# ---------------------------------------------------------------------------
# 5. Telemetria / mensagens
# ---------------------------------------------------------------------------

def test_build_watchdog_event() -> None:
    event = Watchdog.build_watchdog_event("Timeout", time.monotonic())
    assert "reason" in event
    assert "elapsed_seconds" in event
    assert event["reason"] == "Timeout"


def test_build_watchdog_summary(config: dict, tool_history_repeated_fail: list) -> None:
    summary = Watchdog.build_watchdog_summary(
        tool_history_repeated_fail, "Loop detectado"
    )
    assert "Loop detectado" in summary
    assert "file_reader" in summary
