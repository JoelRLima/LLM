"""
Testes para agent/watchdog.py — Watchdog de execução.

Cobre: timeout global, detecção de loop sem progresso, detecção de falhas
consecutivas com o mesmo erro, e ponto de entrada check_all().
"""
import time
from unittest.mock import patch

import pytest

from agent.watchdog import DEFAULT_MAX_TASK_WALL_SECONDS, Watchdog


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