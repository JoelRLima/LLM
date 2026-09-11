"""Canonical local artifact paths for evaluation and release readiness."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class EvaluationArtifactPaths:
    root: Path

    @property
    def audit_root(self) -> Path:
        return self.root / ".audit-local"

    @property
    def output_dir(self) -> Path:
        return self.audit_root / "out"

    @property
    def installed_acceptance(self) -> Path:
        return self.output_dir / "installed-acceptance.json"

    @property
    def corrective_dry_run(self) -> Path:
        return self.output_dir / "evaluation-corrective-dry-run.json"

    @property
    def corrective_ready(self) -> Path:
        return self.output_dir / "evaluation-corrective-ready.json"

    @property
    def real_model_preflight(self) -> Path:
        return self.output_dir / "real-model-preflight.json"

    @property
    def real_model_epoch_2(self) -> Path:
        return self.output_dir / "real-model-epoch-2.json"

    @property
    def prior_real_model_epoch(self) -> Path:
        return self.output_dir / "real-model-epoch-1.json"

    @property
    def real_model_epoch_2_partial(self) -> Path:
        return self.output_dir / "real-model-epoch-2.partial.json"


def canonical_artifact_paths(repo_root: str | Path) -> EvaluationArtifactPaths:
    return EvaluationArtifactPaths(Path(repo_root).resolve())


def reserved_live_artifact_paths(repo_root: str | Path) -> tuple[Path, ...]:
    """Return every canonical artifact path protected from arbitrary live output."""

    paths = canonical_artifact_paths(repo_root)
    return tuple(
        dict.fromkeys(
            path.resolve()
            for path in (
                paths.installed_acceptance,
                paths.corrective_dry_run,
                paths.corrective_ready,
                paths.real_model_preflight,
                paths.real_model_epoch_2,
                paths.prior_real_model_epoch,
                paths.real_model_epoch_2_partial,
            )
        )
    )


def live_owned_artifact_paths(repo_root: str | Path) -> tuple[Path, ...]:
    """Return the two paths owned by the canonical live campaign."""

    paths = canonical_artifact_paths(repo_root)
    return (paths.real_model_epoch_2.resolve(), paths.real_model_epoch_2_partial.resolve())


def resolve_output_path(output_path: str | Path, *, root: str | Path) -> Path:
    """Resolve a CLI output once against the repository root."""

    output = Path(output_path)
    selected = output if output.is_absolute() else Path(root).resolve() / output
    return selected.resolve()


def progress_path_for(output_path: str | Path, *, root: str | Path | None = None) -> Path:
    """Derive one unambiguous progress path from the selected live output."""

    output = Path(output_path)
    if root is not None:
        output = output if output.is_absolute() else Path(root).resolve() / output
    return output.with_name(f"{output.stem}.partial{output.suffix}")


__all__ = [
    "EvaluationArtifactPaths",
    "canonical_artifact_paths",
    "live_owned_artifact_paths",
    "progress_path_for",
    "reserved_live_artifact_paths",
    "resolve_output_path",
]
