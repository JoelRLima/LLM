"""Only clean-install plus original guards; fatal timeout requires VM disposal."""
from __future__ import annotations

import argparse
import faulthandler
import functools
import importlib.util
import os
import sys
import threading
import time
from pathlib import Path
from typing import Any, Callable

from scripts.w23_diagnostics.evidence import Events, Redactor, digest, write_json
from scripts.w23_diagnostics.inputs import contract
from scripts.w23_diagnostics.milestones import Milestones
from scripts.w23_diagnostics.observation import Observation
from scripts.w23_diagnostics.process import Commands, StageError


def load_verifier(source: Path) -> Any:
    if digest(source / "scripts/verify_installed_product.py") != contract()["files"]["scripts/verify_installed_product.py"]:
        raise ValueError("Verifier hash differs from authenticated source")
    sys.path[:0] = [str(source), str(source / "src")]
    spec = importlib.util.spec_from_file_location("w23_authenticated_verifier", source / "scripts/verify_installed_product.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("verifier loader unavailable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    if module.ROOT != source:
        raise RuntimeError("wrong verifier root")
    return module


class Scenario:
    def __init__(self, module: Any, output: Path, private: Path, seconds: int) -> None:
        self.module, self.output, self.private = module, output, private
        output.mkdir(parents=True, exist_ok=False)
        private.mkdir(parents=True, exist_ok=False)
        self.redactor = Redactor([module.ROOT, private])
        self.events = Events(output / "checkpoints.jsonl", self.redactor)
        self.commands = Commands(output / "children", self.redactor)
        self.active: list[str] = []
        self.finished = threading.Event()
        self.lock = threading.RLock()
        self.deadline = time.monotonic() + seconds
        self.result: dict[str, Any] = {"status": "UNKNOWN", "scenario": "clean-install", "restoration": "UNKNOWN"}
        self.observation: Observation | None = None
        self.context: Any = None
        self.save()

    def save(self) -> None:
        with self.lock:
            write_json(self.output / "result.json", self.redactor.value(self.result))

    def fatal(self, status: str, code: int) -> None:
        with self.lock:
            self.result.update(status=status, restoration="UNKNOWN", fatal=True, active=list(self.active))
            if self.observation is not None:
                self.result["observation"] = self.observation.summary()
            self.save()
        self.events.emit("fatal", status=status, active=list(self.active))
        os._exit(code)  # OS closes our job handles; outer job also owns this driver.

    def watchdog(self) -> None:
        next_stack, next_heartbeat, dumps = 0.0, 0.0, 0
        try:
            while not self.finished.wait(0.5):
                now = time.monotonic()
                if now >= self.deadline:
                    self.fatal("TIMEOUT", 124)
                if now >= next_stack and dumps < 8:
                    self.stack(dumps)
                    next_stack, dumps = now + 20, dumps + 1
                if now >= next_heartbeat:
                    self.events.emit("heartbeat", active=list(self.active), seconds_left=self.deadline - now)
                    next_heartbeat = now + 15
        except BaseException:
            self.fatal("UNKNOWN", 125)

    def stack(self, index: int) -> None:
        raw = self.private / "stack.raw"
        with raw.open("wb") as stream:
            faulthandler.dump_traceback(file=stream, all_threads=True)
        with raw.open("rb") as stream:
            text = stream.read(256 * 1024).decode("utf-8", errors="replace")
        (self.output / f"stack-{index:02d}.txt").write_text(self.redactor.text(text), encoding="utf-8")

    def wrap(self, name: str, function: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(function)
        def wrapped(*args: Any, **kwargs: Any) -> Any:
            started = time.monotonic()
            self.active.append(name)
            self.events.emit("enter", operation=name)
            try:
                return function(*args, **kwargs)
            finally:
                self.events.emit("exit", operation=name, wall_seconds=time.monotonic() - started)
                self.active.pop()
        return wrapped

    def install_instrumentation(self) -> None:
        for name, function in list(vars(self.module).items()):
            if callable(function) and getattr(function, "__module__", None) == self.module.__name__ and name.startswith("_v3_"):
                setattr(self.module, name, self.wrap(name, function))
        self.module._run = self.command

    def command(self, command: Any, *, cwd: Path, environment: Any) -> Any:
        remaining = max(1, int(self.deadline - time.monotonic()))
        observation = None
        if self.context is not None and str(self.context.install_script) in command:
            expected = [self.module._powershell_path(), "-NoProfile", "-ExecutionPolicy", "Bypass", "-File",
                        str(self.context.install_script)]
            if list(command) != expected:
                raise ValueError("Unexpected R01B installer invocation")
            if self.observation is not None:
                raise ValueError("R01B observes one clean installer only")
            authorized = Path(os.environ["LOCALAPPDATA"]).resolve() / "local-llm-agent/install"
            if self.context.install_root.exists():
                raise ValueError("R01B requires an absent clean install root")
            observation = Observation(self.output, self.redactor, Milestones(self.context.install_root, authorized))
            self.observation = observation
        result = self.commands.run("verifier-child", list(command), cwd, dict(environment), remaining, observation=observation)
        if result.status in {"TIMEOUT", "UNKNOWN"}:
            raise StageError(result.status)
        return self.module.CommandResult(tuple(command), result.code, result.stdout, result.stderr)

    def finalize(self, context: Any, installed: bool) -> str:
        self.deadline = time.monotonic() + 120
        errors = self.module._v3_finalize_context(
            context, installed=installed, keep=False, application_home=context.application_home,
        )
        restored = (not errors and not context.install_root.exists()
                    and self.module._registry_path_snapshot() == context.original_user
                    and self.module._machine_path() == context.before_machine)
        self.result["restoration"] = "PASS" if restored else "UNKNOWN"
        self.events.emit("finalization", error_count=len(errors), restoration=self.result["restoration"])
        return "PASS" if restored else "FAIL"

    def run(self, bundle: Path) -> int:
        identities = self.bundle_identity(bundle)
        write_json(self.output / "bundle-before.json", identities)
        self.install_instrumentation()
        watcher = threading.Thread(target=self.watchdog, daemon=True)
        watcher.start()
        context, installed, status = None, False, "UNKNOWN"
        try:
            self.events.emit("scenario_start", scenario="clean-install")
            context = self.module._v3_prepare_context(bundle)
            self.context = context
            hostile = self.module._v3_hostile_environment(context)
            self.module._v3_clean_install(context, hostile)
            installed, status = True, "PASS"
            self.events.emit("scenario_end", status="PASS")
        except StageError as exc:
            status = exc.status
        except BaseException as exc:
            self.events.emit("verification_exception", exception_type=type(exc).__name__)
            status = "FAIL"
        finally:
            status = self._finish(context, installed, status)
            status = self.finish_identity(bundle, identities, status)
            if self.observation is not None:
                self.result["observation"] = self.observation.summary()
                if self.observation.failed and status == "PASS":
                    status = "UNKNOWN"
            elif status == "PASS":
                status = "UNKNOWN"  # A claimed clean install must have actually reached the observed installer.
            self.finished.set()
            watcher.join(timeout=5)
            if watcher.is_alive():
                status = "UNKNOWN"
            self.result["status"] = status
            self.save()
        return 0 if status == "PASS" else 124 if status == "TIMEOUT" else 1

    def finish_identity(self, bundle: Path, identities: dict[str, str], status: str) -> str:
        try:
            after = self.bundle_identity(bundle)
            write_json(self.output / "bundle-after.json", after)
            return status if after == identities else "FAIL"
        except Exception as exc:
            self.events.emit("bundle_identity_failure", exception_type=type(exc).__name__)
            return "FAIL"

    def bundle_identity(self, bundle: Path) -> dict[str, str]:
        values = {"release-manifest.json": digest(bundle / "release-manifest.json"),
                  "install.ps1": digest(bundle / "install.ps1")}
        spec = contract()
        if (values["release-manifest.json"] != spec["release_manifest_sha256"]
                or values["install.ps1"] != spec["files"]["installer/install.ps1"]):
            raise ValueError("R01B refuses unauthenticated installer/bundle")
        return values

    def _finish(self, context: Any, installed: bool, status: str) -> str:
        if context is None:
            return status
        try:
            cleanup = self.finalize(context, installed)
        except BaseException as exc:
            self.events.emit("cleanup_exception", exception_type=type(exc).__name__)
            cleanup = "UNKNOWN"
        return cleanup if status == "PASS" else status


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--bundle", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--private", type=Path, required=True)
    parser.add_argument("--seconds", type=int, default=180, choices=range(30, 301))
    args = parser.parse_args()
    if os.name != "nt" or os.environ.get("GITHUB_ACTIONS") != "true" or os.environ.get("RUNNER_ENVIRONMENT") != "github-hosted":
        raise RuntimeError("disposable GitHub-hosted Windows runner required")
    scenario = Scenario(load_verifier(args.source.resolve()), args.output, args.private, args.seconds)
    return scenario.run(args.bundle)


if __name__ == "__main__":
    raise SystemExit(main())
