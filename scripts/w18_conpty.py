"""Small native Windows ConPTY runner for installed-product acceptance.

The harness intentionally has no third-party terminal dependency. It starts
the requested command inside a Windows pseudo-console, performs bounded
marker-driven interaction, and returns a complete diagnostic transcript.
"""

from __future__ import annotations

import ctypes
import hashlib
import os
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Mapping, Sequence


class ConPtyError(RuntimeError):
    """Raised when native pseudo-console setup or interaction fails."""


@dataclass(frozen=True)
class ConPtyResult:
    returncode: int
    transcript: str
    inputs: tuple[str, ...]
    diagnostics: Mapping[str, object] = field(default_factory=dict)
    raw_transcript_sha256: str = ""
    output_bytes: int = 0
    raw_transcript_bytes: bytes = b""


def _skip_csi(value: str, start: int) -> int:
    """Skip one CSI sequence, including its parameters and final byte."""

    index = start + 1
    length = len(value)
    while index < length:
        code = ord(value[index])
        index += 1
        if 0x40 <= code <= 0x7E:
            break
    return index


def _skip_string_control(value: str, start: int) -> int:
    """Skip an OSC/DCS-like string control through BEL or the ST terminator."""

    index = start + 1
    length = len(value)
    while index < length:
        character = value[index]
        if character == "\x07":
            return index + 1
        if character == "\x9c":
            return index + 1
        if character == "\x1b" and index + 1 < length and value[index + 1] == "\\":
            return index + 2
        index += 1
    return length


def _terminal_control_end(value: str, start: int) -> int | None:
    """Return the first character after a terminal control, if present."""

    character = value[start]
    if character == "\x1b":
        if start + 1 >= len(value):
            return start + 1
        next_character = value[start + 1]
        if next_character == "[":
            return _skip_csi(value, start + 1)
        if next_character == "]" or next_character in "P^_X":
            return _skip_string_control(value, start + 1)
        return start + 2
    if character == "\x9b":
        return _skip_csi(value, start)
    if character in "\x9d\x90\x98\x9e\x9f":
        return _skip_string_control(value, start)
    return None


def normalize_terminal_text(value: str) -> str:
    """Project a decoded ConPTY stream onto its visible terminal text.

    The input is never modified.  This is a linear, bounded scanner rather
    than a collection of regular expressions so incomplete terminal controls
    at the end of a currently-read chunk cannot create an unbounded match.
    RAW transcript storage and hashing remain the responsibility of the
    caller; this function is only for semantic marker matching.
    """

    visible: list[str] = []
    index = 0
    length = len(value)
    while index < length:
        character = value[index]
        control_end = _terminal_control_end(value, index)
        if control_end is not None:
            index = control_end
            continue

        code = ord(character)
        # C0 controls other than layout-preserving whitespace are not visible
        # terminal text.  Keep CR/LF/TAB because prompts commonly use them as
        # meaningful layout separators for substring matching.
        if code < 0x20 and character not in "\r\n\t":
            index += 1
            continue
        if 0x7F <= code <= 0x9F:
            index += 1
            continue

        visible.append(character)
        index += 1
    return "".join(visible)


def _transcript_digest_details(
    raw_bytes: bytes,
    rendered: str,
    *,
    expected_marker: str | None = None,
) -> dict[str, object]:
    normalized = normalize_terminal_text(rendered)
    details: dict[str, object] = {
        "raw_transcript_sha256": hashlib.sha256(raw_bytes).hexdigest(),
        "output_bytes": len(raw_bytes),
        "normalized_transcript_sha256": hashlib.sha256(normalized.encode("utf-8")).hexdigest(),
    }
    if expected_marker is not None:
        details["expected_marker"] = expected_marker
        details["marker_observed"] = expected_marker in normalized
    return details


def bounded_conpty_diagnostics(
    result: ConPtyResult,
    *,
    expected_marker: str | None = None,
) -> dict[str, object]:
    """Return log-safe transcript evidence without exposing transcript text."""

    details = _transcript_digest_details(
        result.raw_transcript_bytes,
        result.transcript,
        expected_marker=expected_marker,
    )
    structural_keys = (
        "read_calls",
        "output_bytes_read",
        "read_error",
        "child_process_created",
        "child_exit_code",
        "transcript_encoding",
    )
    structural = {
        key: result.diagnostics[key]
        for key in structural_keys
        if key in result.diagnostics
    }
    if structural:
        details["structural_diagnostics"] = structural
    return details


class _SecurityAttributes(ctypes.Structure):
    _fields_ = [
        ("nLength", ctypes.c_uint32),
        ("lpSecurityDescriptor", ctypes.c_void_p),
        ("bInheritHandle", ctypes.c_int),
    ]


class _Coord(ctypes.Structure):
    _fields_ = [("X", ctypes.c_int16), ("Y", ctypes.c_int16)]


class _StartupInfo(ctypes.Structure):
    _fields_ = [
        ("cb", ctypes.c_uint32),
        ("lpReserved", ctypes.c_wchar_p),
        ("lpDesktop", ctypes.c_wchar_p),
        ("lpTitle", ctypes.c_wchar_p),
        ("dwX", ctypes.c_uint32),
        ("dwY", ctypes.c_uint32),
        ("dwXSize", ctypes.c_uint32),
        ("dwYSize", ctypes.c_uint32),
        ("dwXCountChars", ctypes.c_uint32),
        ("dwYCountChars", ctypes.c_uint32),
        ("dwFillAttribute", ctypes.c_uint32),
        ("dwFlags", ctypes.c_uint32),
        ("wShowWindow", ctypes.c_uint16),
        ("cbReserved2", ctypes.c_uint16),
        ("lpReserved2", ctypes.c_void_p),
        ("hStdInput", ctypes.c_void_p),
        ("hStdOutput", ctypes.c_void_p),
        ("hStdError", ctypes.c_void_p),
    ]


class _StartupInfoEx(ctypes.Structure):
    _fields_ = [
        ("StartupInfo", _StartupInfo),
        ("lpAttributeList", ctypes.c_void_p),
    ]


class _ProcessInformation(ctypes.Structure):
    _fields_ = [
        ("hProcess", ctypes.c_void_p),
        ("hThread", ctypes.c_void_p),
        ("dwProcessId", ctypes.c_uint32),
        ("dwThreadId", ctypes.c_uint32),
    ]


@dataclass(frozen=True)
class _ConPtyApi:
    create_pipe: Any
    close_handle: Any
    read_file: Any
    write_file: Any
    create_pseudo_console: Any
    close_pseudo_console: Any
    initialize_attributes: Any
    update_attribute: Any
    delete_attributes: Any
    create_process: Any
    wait_for_single_object: Any
    get_exit_code: Any
    terminate_process: Any
    resume_thread: Any
    create_job: Any
    assign_job: Any
    terminate_job: Any


def _last_error() -> int:
    return int(ctypes.__dict__["get_last_error"]())


def _error(label: str) -> ConPtyError:
    return ConPtyError(f"{label} (Win32 error {_last_error()})")


def _load_conpty_api() -> _ConPtyApi:
    kernel32 = ctypes.__dict__["WinDLL"]("kernel32", use_last_error=True)
    create_pipe = kernel32.CreatePipe
    create_pipe.argtypes = [
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(_SecurityAttributes),
        ctypes.c_uint32,
    ]
    create_pipe.restype = ctypes.c_int
    close_handle = kernel32.CloseHandle
    close_handle.argtypes = [ctypes.c_void_p]
    close_handle.restype = ctypes.c_int
    read_file = kernel32.ReadFile
    read_file.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    read_file.restype = ctypes.c_int
    write_file = kernel32.WriteFile
    write_file.argtypes = [
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_uint32),
        ctypes.c_void_p,
    ]
    write_file.restype = ctypes.c_int
    create_pseudo_console = getattr(kernel32, "CreatePseudoConsole", None)
    if create_pseudo_console is None:
        raise ConPtyError("CreatePseudoConsole is unavailable on this Windows host")
    create_pseudo_console.argtypes = [
        _Coord,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_void_p),
    ]
    create_pseudo_console.restype = ctypes.c_long
    close_pseudo_console = getattr(kernel32, "ClosePseudoConsole", None)
    if close_pseudo_console is None:
        raise ConPtyError("ClosePseudoConsole is unavailable on this Windows host")
    close_pseudo_console.argtypes = [ctypes.c_void_p]
    close_pseudo_console.restype = None
    initialize_attributes = kernel32.InitializeProcThreadAttributeList
    initialize_attributes.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_uint32,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    initialize_attributes.restype = ctypes.c_int
    update_attribute = kernel32.UpdateProcThreadAttribute
    update_attribute.argtypes = [
        ctypes.c_void_p,
        ctypes.c_uint32,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.c_size_t,
        ctypes.c_void_p,
        ctypes.POINTER(ctypes.c_size_t),
    ]
    update_attribute.restype = ctypes.c_int
    delete_attributes = kernel32.DeleteProcThreadAttributeList
    delete_attributes.argtypes = [ctypes.c_void_p]
    delete_attributes.restype = None
    create_process = kernel32.CreateProcessW
    create_process.argtypes = [
        ctypes.c_wchar_p,
        ctypes.c_wchar_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_int,
        ctypes.c_uint32,
        ctypes.c_void_p,
        ctypes.c_wchar_p,
        ctypes.POINTER(_StartupInfoEx),
        ctypes.POINTER(_ProcessInformation),
    ]
    create_process.restype = ctypes.c_int
    wait_for_single_object = kernel32.WaitForSingleObject
    wait_for_single_object.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    wait_for_single_object.restype = ctypes.c_uint32
    get_exit_code = kernel32.GetExitCodeProcess
    get_exit_code.argtypes = [ctypes.c_void_p, ctypes.POINTER(ctypes.c_uint32)]
    get_exit_code.restype = ctypes.c_int
    terminate_process = kernel32.TerminateProcess
    terminate_process.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    terminate_process.restype = ctypes.c_int
    resume_thread = kernel32.ResumeThread
    resume_thread.argtypes = [ctypes.c_void_p]
    resume_thread.restype = ctypes.c_uint32
    create_job = kernel32.CreateJobObjectW
    create_job.argtypes = [ctypes.c_void_p, ctypes.c_wchar_p]
    create_job.restype = ctypes.c_void_p
    assign_job = kernel32.AssignProcessToJobObject
    assign_job.argtypes = [ctypes.c_void_p, ctypes.c_void_p]
    assign_job.restype = ctypes.c_int
    terminate_job = kernel32.TerminateJobObject
    terminate_job.argtypes = [ctypes.c_void_p, ctypes.c_uint32]
    terminate_job.restype = ctypes.c_int
    return _ConPtyApi(
        create_pipe,
        close_handle,
        read_file,
        write_file,
        create_pseudo_console,
        close_pseudo_console,
        initialize_attributes,
        update_attribute,
        delete_attributes,
        create_process,
        wait_for_single_object,
        get_exit_code,
        terminate_process,
        resume_thread,
        create_job,
        assign_job,
        terminate_job,
    )


def _diagnostics() -> dict[str, object]:
    return {
        "input_pipe_created": False,
        "output_pipe_created": False,
        "pseudo_console_created": False,
        "attribute_list_initialized": False,
        "pseudo_console_attribute_updated": False,
        "reader_started": False,
        "read_calls": 0,
        "output_bytes_read": 0,
        "read_error": None,
        "child_process_created": False,
        "child_pid": None,
        "child_exit_code": None,
        "host_input_closed": False,
        "pseudo_console_closed": False,
        "transcript_characters": 0,
    }


@dataclass
class _ConPtySession:
    api: _ConPtyApi
    timeout_seconds: float
    host_write: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    pty_input: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    host_read: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    pty_output: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    pseudo_console: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    job: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    process_info: _ProcessInformation = field(default_factory=_ProcessInformation)
    attribute_buffer: Any = None
    attribute_ptr: ctypes.c_void_p = field(default_factory=ctypes.c_void_p)
    reader: threading.Thread | None = None
    chunks: list[bytes] = field(default_factory=list)
    reader_errors: list[int] = field(default_factory=list)
    read_calls: int = 0
    output_bytes_read: int = 0
    details: dict[str, object] = field(default_factory=_diagnostics)

    def close_native(self, handle: Any) -> None:
        value = handle.value if isinstance(handle, ctypes.c_void_p) else int(handle or 0)
        if value:
            self.api.close_handle(ctypes.c_void_p(value))
            if isinstance(handle, ctypes.c_void_p):
                handle.value = None

    def create_console(self) -> None:
        if not self.api.create_pipe(ctypes.byref(self.pty_input), ctypes.byref(self.host_write), None, 0):
            raise _error("CreatePipe(input) failed")
        self.details["input_pipe_created"] = True
        if not self.api.create_pipe(ctypes.byref(self.host_read), ctypes.byref(self.pty_output), None, 0):
            raise _error("CreatePipe(output) failed")
        self.details["output_pipe_created"] = True
        result = self.api.create_pseudo_console(
            _Coord(120, 40),
            self.pty_input,
            self.pty_output,
            0,
            ctypes.byref(self.pseudo_console),
        )
        if result != 0:
            raise _error("CreatePseudoConsole failed")
        self.details["pseudo_console_created"] = True

    def initialize_attribute_list(self) -> None:
        required = ctypes.c_size_t(0)
        self.api.initialize_attributes(None, 1, 0, ctypes.byref(required))
        if not required.value:
            raise _error("InitializeProcThreadAttributeList size query failed")
        self.attribute_buffer = ctypes.create_string_buffer(required.value)
        self.attribute_ptr = ctypes.cast(self.attribute_buffer, ctypes.c_void_p)
        if not self.api.initialize_attributes(self.attribute_ptr, 1, 0, ctypes.byref(required)):
            raise _error("InitializeProcThreadAttributeList failed")
        self.details["attribute_list_initialized"] = True
        if not self.api.update_attribute(
            self.attribute_ptr,
            0,
            ctypes.c_size_t(0x00020016),
            self.pseudo_console,
            ctypes.sizeof(self.pseudo_console),
            None,
            None,
        ):
            raise _error("UpdateProcThreadAttribute(PSEUDOCONSOLE) failed")
        self.details["pseudo_console_attribute_updated"] = True

    def read_output(self) -> None:
        try:
            while True:
                buffer = ctypes.create_string_buffer(4096)
                count = ctypes.c_uint32(0)
                self.read_calls += 1
                self.details["read_calls"] = self.read_calls
                if not self.api.read_file(
                    self.host_read,
                    ctypes.byref(buffer),
                    len(buffer),
                    ctypes.byref(count),
                    None,
                ):
                    error_code = _last_error()
                    self.details["read_error"] = error_code
                    if error_code not in (109, 232):
                        self.reader_errors.append(error_code)
                    return
                if count.value:
                    self.output_bytes_read += count.value
                    self.details["output_bytes_read"] = self.output_bytes_read
                    self.chunks.append(buffer.raw[: count.value])
        except BaseException:
            self.reader_errors.append(-1)

    def start_reader(self) -> None:
        self.reader = threading.Thread(target=self.read_output, name="w18-conpty-reader", daemon=True)
        self.reader.start()
        self.details["reader_started"] = True

    def create_child(self, command: Sequence[str], cwd: Path, environment: Mapping[str, str]) -> None:
        startup = _StartupInfoEx()
        startup.StartupInfo.cb = ctypes.sizeof(_StartupInfoEx)
        startup.StartupInfo.dwFlags = 0x00000100
        startup.lpAttributeList = self.attribute_ptr.value
        command_buffer = ctypes.create_unicode_buffer(" ".join(command))
        environment_block = "\0".join(
            f"{key}={environment[key]}" for key in sorted(environment, key=str.casefold)
        ) + "\0\0"
        environment_buffer = ctypes.create_unicode_buffer(environment_block)
        flags = 0x00080000 | 0x00000400 | 0x00000004
        if not self.api.create_process(
            None,
            command_buffer,
            None,
            None,
            0,
            flags,
            ctypes.cast(environment_buffer, ctypes.c_void_p),
            str(cwd),
            ctypes.byref(startup),
            ctypes.byref(self.process_info),
        ):
            raise _error("CreateProcessW failed")
        self.details["child_process_created"] = True
        self.details["child_pid"] = int(self.process_info.dwProcessId)
        self.close_native(self.pty_input)
        self.close_native(self.pty_output)
        self.details["child_side_pipe_ends_closed"] = True
        self._assign_job_and_resume()

    def _assign_job_and_resume(self) -> None:
        self.job.value = self.api.create_job(None, None)
        if not self.job.value:
            raise _error("CreateJobObjectW failed")
        if not self.api.assign_job(self.job, self.process_info.hProcess):
            raise _error("AssignProcessToJobObject failed")
        if self.api.resume_thread(self.process_info.hThread) == 0xFFFFFFFF:
            raise _error("ResumeThread failed")
        self.close_native(self.process_info.hThread)
        self.process_info.hThread = None

    def transcript(self) -> str:
        return b"".join(self.chunks).decode("utf-8", errors="replace")

    def wait_for_marker(self, marker: str) -> None:
        deadline = time.monotonic() + self.timeout_seconds
        while marker not in normalize_terminal_text(self.transcript()):
            if time.monotonic() >= deadline:
                evidence = _transcript_digest_details(
                    b"".join(self.chunks),
                    self.transcript(),
                    expected_marker=marker,
                )
                raise ConPtyError(
                    f"timed out waiting for marker {marker!r}; bounded evidence={evidence}"
                )
            time.sleep(0.02)

    def send_interactions(self, interactions: Sequence[tuple[str, str]]) -> tuple[str, ...]:
        sent: list[str] = []
        for marker, value in interactions:
            self.wait_for_marker(marker)
            self.write_input(value)
            sent.append(value)
        return tuple(sent)

    def write_input(self, value: str) -> None:
        if not self.host_write.value:
            raise ConPtyError("ConPTY input handle is unavailable")
        payload = value.encode("utf-8")
        written = ctypes.c_uint32(0)
        succeeded = self.api.write_file(
            self.host_write,
            ctypes.c_char_p(payload),
            len(payload),
            ctypes.byref(written),
            None,
        )
        if not succeeded or written.value != len(payload):
            raise _error("ConPTY input write failed")

    def wait_for_exit(self) -> int:
        if self.api.wait_for_single_object(self.process_info.hProcess, int(self.timeout_seconds * 1000)) != 0:
            self.terminate()
            evidence = _transcript_digest_details(b"".join(self.chunks), self.transcript())
            raise ConPtyError(f"ConPTY process did not exit; bounded evidence={evidence}")
        exit_code = ctypes.c_uint32()
        if not self.api.get_exit_code(self.process_info.hProcess, ctypes.byref(exit_code)):
            raise _error("GetExitCodeProcess failed")
        self.details["child_exit_code"] = int(exit_code.value)
        return int(exit_code.value)

    def close_output_path(self) -> None:
        self.close_native(self.host_write)
        self.details["host_input_closed"] = True
        time.sleep(0.5)
        self.close_console()
        if self.reader is not None:
            self.reader.join(timeout=2)
        if self.reader_errors:
            raise ConPtyError(f"ConPTY output read failed (Win32 error {self.reader_errors[0]})")

    def close_console(self) -> None:
        if self.pseudo_console.value:
            self.api.close_pseudo_console(self.pseudo_console)
            self.pseudo_console.value = None
            self.details["pseudo_console_closed"] = True

    def build_result(self, exit_code: int, sent: tuple[str, ...]) -> ConPtyResult:
        raw_transcript = b"".join(self.chunks)
        try:
            rendered = raw_transcript.decode("utf-8")
            self.details["transcript_encoding"] = "utf-8"
        except UnicodeDecodeError:
            rendered = raw_transcript.decode("cp850")
            self.details["transcript_encoding"] = "cp850"
        self.details["transcript_characters"] = len(rendered)
        return ConPtyResult(
            exit_code,
            rendered,
            sent,
            dict(self.details),
            hashlib.sha256(raw_transcript).hexdigest(),
            len(raw_transcript),
            raw_transcript,
        )

    def terminate(self) -> None:
        if self.job.value:
            self.api.terminate_job(self.job, 1)
        if self.process_info.hProcess:
            self.api.terminate_process(self.process_info.hProcess, 1)
            self.api.wait_for_single_object(self.process_info.hProcess, 5000)

    def terminate_if_running(self) -> None:
        if (
            self.process_info.hProcess
            and self.api.wait_for_single_object(self.process_info.hProcess, 0) == 0x00000102
        ):
            self.terminate()

    def cleanup(self) -> None:
        self.terminate_if_running()
        self.close_console()
        if self.reader is not None:
            self.reader.join(timeout=2)
        self.close_native(self.host_write)
        self.close_native(self.host_read)
        self.close_native(self.pty_input)
        self.close_native(self.pty_output)
        if self.attribute_buffer is not None:
            self.api.delete_attributes(ctypes.cast(self.attribute_buffer, ctypes.c_void_p))
        self.close_native(self.process_info.hProcess)
        self.process_info.hProcess = None
        self.close_native(self.process_info.hThread)
        self.close_native(self.job)

    def execute(
        self,
        command: Sequence[str],
        cwd: Path,
        environment: Mapping[str, str],
        interactions: Sequence[tuple[str, str]],
        terminate_after_marker: str | None = None,
    ) -> ConPtyResult:
        try:
            self.create_console()
            self.initialize_attribute_list()
            self.start_reader()
            self.create_child(command, cwd, environment)
            sent = self.send_interactions(interactions)
            if terminate_after_marker is not None:
                self.wait_for_marker(terminate_after_marker)
                self.details["terminated_after_marker"] = terminate_after_marker
                self.terminate()
                self.close_output_path()
                return self.build_result(-1, sent)
            exit_code = self.wait_for_exit()
            self.close_output_path()
            return self.build_result(exit_code, sent)
        except ConPtyError as exc:
            raise ConPtyError(f"{exc}; diagnostics={self.details}") from exc
        except (OSError, ValueError) as exc:
            raise ConPtyError(f"ConPTY interaction failed: {exc}") from exc
        finally:
            self.cleanup()


def run_conpty(
    command: Sequence[str],
    *,
    cwd: Path,
    environment: Mapping[str, str],
    interactions: Sequence[tuple[str, str]],
    timeout_seconds: float = 30.0,
    terminate_after_marker: str | None = None,
) -> ConPtyResult:
    """Run one command in ConPTY and perform marker-driven interactions.

    ``terminate_after_marker`` is reserved for bounded diagnostic probes that
    must capture a prompt without allowing an interactive process to continue.
    Its returned ``returncode`` is ``-1`` because the child was deliberately
    terminated after the marker was observed.
    """

    if os.name != "nt":
        raise ConPtyError("native Windows ConPTY is unavailable on this platform")
    if not command:
        raise ConPtyError("ConPTY command cannot be empty")
    return _ConPtySession(_load_conpty_api(), timeout_seconds).execute(
        command,
        cwd,
        environment,
        interactions,
        terminate_after_marker,
    )


__all__ = [
    "ConPtyError",
    "ConPtyResult",
    "bounded_conpty_diagnostics",
    "normalize_terminal_text",
    "run_conpty",
]
