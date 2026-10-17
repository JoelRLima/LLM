"""Bounded external R01B measurements. No installer tracing or source rewriting."""
from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from scripts.w23_diagnostics.evidence import Redactor, write_json
from scripts.w23_diagnostics.metrics import ProcessSampler
from scripts.w23_diagnostics.milestones import Milestones
from scripts.w23_diagnostics.native import NativeChild, OwnedJob

INTERVAL = 15
MAX_SAMPLES = 32
MAX_BYTES = 1024 * 1024
MAX_SAMPLE_SECONDS = 0.25


class Observation:
    def __init__(self, output: Path, redactor: Redactor, milestones: Milestones | None = None) -> None:
        self.path = output / "external-samples.jsonl"
        self.summary_path = output / "external-observation.json"
        self.redactor, self.milestones = redactor, milestones
        self.sampler: ProcessSampler | None = None
        self.started = time.monotonic()
        self.count = self.written = 0
        self.failed = False
        self.overhead = 0.0
        self.last: dict[str, Any] = {}
        # Exclusive files: never append to evidence from an earlier command/run.
        with self.path.open("xb"):
            pass
        self.persist()

    def summary(self) -> dict[str, Any]:
        return {"schema": "W23-R01B-EXTERNAL-1", "status": "UNKNOWN" if self.failed or not self.count else "PASS",
                "samples": self.count, "collection_wall_seconds": self.overhead, "last_sample": self.last,
                "interpretation": "MEASURED counters; CPU is activity, not proof of installer progress; exact phase UNKNOWN"}

    def persist(self) -> None:
        write_json(self.summary_path, self.summary())

    def sample(self, job: OwnedJob, child: NativeChild) -> None:
        started = time.monotonic()
        row: dict[str, Any] = {"sample": self.count, "elapsed_seconds": started - self.started,
                               "root_pid_owned": child.pid, "status": "UNKNOWN"}
        try:
            if self.count >= MAX_SAMPLES:
                raise ValueError("Observation sample cap exceeded")
            if self.sampler is None:
                self.sampler = ProcessSampler()
            row["counters"] = self.sampler.sample(job)
            row["namespace"] = self.milestones.sample() if self.milestones else None
            if row["counters"].get("unavailable"):
                self.failed = True
                row["collection_error_type"] = "UnavailableOwnedSnapshot"
            else:
                row["status"] = "PASS"
        except Exception as exc:
            self.failed = True
            row["collection_error_type"] = type(exc).__name__  # No exception text, image paths, commands, or environment.
        elapsed = time.monotonic() - started
        row["collection_wall_seconds"] = elapsed
        if elapsed > MAX_SAMPLE_SECONDS:
            self.failed = True
            row.update(status="UNKNOWN", collection_error_type="ObservationBudgetExceeded")
        self.last = self.redactor.value(row)
        encoded = (json.dumps(self.last, sort_keys=True) + "\n").encode("utf-8")
        if self.written + len(encoded) > MAX_BYTES or self.count >= MAX_SAMPLES:
            self.failed = True
            self.persist()
            raise ValueError("Observation evidence cap exceeded")
        with self.path.open("ab") as stream:
            stream.write(encoded)
            stream.flush()
            os.fsync(stream.fileno())
        self.count += 1
        self.written += len(encoded)
        self.overhead += time.monotonic() - started  # Includes row write/fsync, excludes the following metadata write.
        self.persist()
