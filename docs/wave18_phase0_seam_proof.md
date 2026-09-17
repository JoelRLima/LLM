# WAVE 18 — Phase 0 Seam Proof

## Verdict

**PHASE 0 GREEN — STOPPED FOR SOL REVIEW**

Phase 0 completed on `feat/wave18-installed-product-experience`. No production installer was implemented and no Phase 1 work was started.

## Authority and branch identity

The post-bootstrap preflight was repeated from the working branch rather than inferred from the earlier STOP:

| Check | Observed |
| --- | --- |
| `HEAD` | `b7f33854ea4af14c3aa7c93e59a42d520eaf5a74` |
| `HEAD^{tree}` | `ca8a100f7741ddae025af2358d7040dbde7b1a7d` |
| `origin/main` | `b7f33854ea4af14c3aa7c93e59a42d520eaf5a74` |
| branch | `feat/wave18-installed-product-experience` |
| staging | empty |
| `git status --short` | empty |
| `git diff --check` | empty |
| Contract SHA-256 | `f5d2c66c6d443efc31d707ba1d5fae443d862cf3de79efb8c5fa550b8ea25f3a` |
| Spec SHA-256 | `0e3fc3c159463dde4db5eb98ab0ddeb08e01638b19216762ed5531fbc28d265c` |

Branch bootstrap was the only Git mutation before this evidence was created:

```text
BRANCH BOOTSTRAP
- previous branch: main
- branch existence before action: local no; origin no
- action: git switch -c feat/wave18-installed-product-experience
- resulting branch: feat/wave18-installed-product-experience
- resulting HEAD: b7f33854ea4af14c3aa7c93e59a42d520eaf5a74
- files changed by branch bootstrap: none
```

## Repository inventory

- Packaging is owned by `pyproject.toml`, using `setuptools.build_meta`, distribution `local-llm-agent`, version `0.1.0`, and console entry point `llm-agent = agent.interfaces.cli.app:main`.
- Runtime package data is `agent.resources/*.json`; the installed wheel contains `agent/resources/default_config.json` and the entry-point metadata.
- Current version lookup is in `agent/__init__.py`: metadata lookup for `local-llm-agent` with source fallback `0.1.0`. Production consumers found are the CLI parser, standalone health checks, and observability export.
- `agent.runtime.paths.AppPaths`/`WorkspacePaths` remain the owners of config, data, state, cache, logs, workspace state, and the durable `local-llm-agent` namespace.
- W17 remains the owner of first-run configuration, workspace selection, interactive shell, streaming/cancellation, approval UI, and teardown. The installer seam ends before those responsibilities.
- `scripts/verify_installed_package.py` remains the owner of existing clean installed-wheel application behavior. Phase 0 did not duplicate or modify it.
- The existing CI matrix remains Ubuntu/Windows × Python 3.10/3.12 with quality/static checks, Mypy, pytest, and installed-wheel acceptance in its existing order.
- Current developer/standalone instructions were inventoried in `README.md` and `docs/operacao-standalone.md`; they were not rewritten in Phase 0.
- Current name surfaces were enumerated without a broad rename: distribution/version (`pyproject.toml`, `agent/__init__.py`, health checks), user-facing CLI (`pyproject.toml`, parser, first-run), durable namespace/compatibility (`agent/runtime/paths.py`, `config_environment.py`, installed-package verifier), internal observability IDs, and temporary dev/harness names. Historical docs/tests retain their literals. `.agent-local/` and `.audit-local/` remain gitignored and their authority files were not edited.

## Pinned uv and managed Python substrate

The exact Windows x64 archive was fetched into each disposable scratch root from the official release URL:

`https://github.com/astral-sh/uv/releases/download/0.12.13/uv-x86_64-pc-windows-msvc.zip`

Official checksum evidence:

`https://releases.astral.sh/github/uv/releases/download/0.12.13/uv-x86_64-pc-windows-msvc.zip.sha256`

| Evidence | Value |
| --- | --- |
| official archive SHA-256 | `a86c9dc7bad9b03f388583b7187c05fe9951c2e0d392217e8fd43d97787f6ec2` |
| local SHA-256, both roots | same exact value |
| archive size | 17,612,025 bytes |
| `uv --version` | `uv 0.12.13 (0ebbd9274 2026-09-10 x86_64-pc-windows-msvc)` |
| archive members | `uv.exe`, `uvw.exe`, `uvx.exe` only; no traversal member |

The extracted `uv.exe` reported local Authenticode `Valid`. The observed signer was `CN="OpenAI OpCo, LLC"` with thumbprint `DF08A554062ECAD03193D010BCC9170BF5D75AE4`; the observed timestamp certificate was Microsoft Public RSA Time Stamping Authority with thumbprint `FF73F729152A9059805E5E0832449D996EF60411`. These facts are recorded literally. This report does not claim that the signer is Astral and does not make an end-to-end signed-installer authenticity claim. The official release page also advertises an artifact-attestation asset; a local `gh` verification was not claimed because the `gh` executable is unavailable.

The pinned uv, with `--no-config`, enumerated managed stable CPython 3.12 candidates. The highest stable CPython candidate exposed by that build was:

```text
cpython-3.12.14-windows-x86_64-none
```

The managed runtime was provisioned privately with `uv python install 3.12.14 --managed-python`. Its exact identity was:

```text
Python 3.12.14
CPython 3.12.14 (main, Sep 1 2026, 14:17:39) [MSC v.1944 64 bit (AMD64)]
BUILD=20260901
```

The corresponding official python-build-standalone artifact evidence is:

`https://github.com/astral-sh/python-build-standalone/releases/download/20260901/cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only.tar.gz`

SHA-256: `e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f`.

The private `python-bin` roots remained empty. No system Python was used as the product runtime; the existing repository `.venv` was used only as the normal development environment to build the baseline wheel.

## Disposable launcher seam

Two roots outside the repository were used:

1. `C:\Users\JoelR\AppData\Local\Temp\wave18-phase0-space-20260914`
2. `C:\Users\JoelR\AppData\Local\Temp\wave18-phase0-José-Teste-20260914`

For each root the proof used private uv/cache/Python/venv paths, disabled Python-bin exposure, created an ordinary no-seed venv, and installed pip only through the venv interpreter using the exact contract hash:

```text
pip==26.2.1
sha256:71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e
```

The runtime closure was compiled into a temporary lock using the PyPI default index, `--generate-hashes`, and `--only-binary :all:`. Installation used `--require-hashes` and binary-only mode. It resolved 15 packages, including `ddgs==9.16.0`, `prompt-toolkit==3.0.53`, `requests==2.34.2`, and `rich==15.0.0`; both environments returned `No broken requirements found.` from `python -m pip check`.

The current baseline wheel was built by the normal development Python and installed last with `venv\Scripts\python.exe -m pip --no-deps`:

```text
local_llm_agent-0.1.0-py3-none-any.whl
SHA-256: ce77455ee73190519f551c58a31596178f5305bc41c37ee96a85e6bef867b750
```

Wheel/package-data checks passed: `agent/resources/default_config.json`, `METADATA`, and `entry_points.txt` were present; task authority files were absent. The installed resource loader read `default_config.json` successfully and metadata version equaled `agent.__version__` (`0.1.0`).

From an outside working directory, both generated `venvs\candidate\Scripts\llm-agent.exe` launchers passed:

```text
llm-agent.exe --version  ->  llm-agent 0.1.0
llm-agent.exe --help     ->  exit code 0
```

The import-origin probes showed `agent` under the candidate venv `site-packages`, `sys.executable` under the candidate venv, and no checkout path in either the imported file or `sys.path`. The Unicode probe observed code point `U+00E9` in the actual path even where console rendering substituted a replacement glyph. Therefore the required spaces-plus-Unicode launcher seam is **PASS**.

## PowerShell 5.1, PATH, and transaction substrate

The normative shell is present as `5.1.26100.9444`. Read-only compatibility checks confirmed `Invoke-WebRequest -UseBasicParsing`, `Expand-Archive`, `Get-FileHash`, explicit TLS 1.2 selection, and .NET `ProcessStartInfo` with `UseShellExecute = false`. A child PowerShell returned exit code 7 through the PS5.1-compatible process API.

A current-user-SID-qualified `Local\...` mutex was created/acquired/released successfully. A same-volume journal-file probe flushed a temporary file and promoted it with `System.IO.File.Replace` using an explicit backup, preserving the old content in the backup and the new content at the destination. PS5.1 overload binding rejected a null backup argument during an early probe; that implementation shape is explicitly excluded from the later installer design.

Machine PATH was reproved read-only through both Windows owners. The API call
`[Environment]::GetEnvironmentVariable("Path", [EnvironmentVariableTarget]::Machine)`
returned a nonempty expanded value of length 153, with deterministic UTF-16LE SHA-256
`274d547a462adaa04c65313da3f29496a0488d39be0b5fecd466c3bc265dbb81`.

The corresponding key actually consulted was
`HKLM:\SYSTEM\CurrentControlSet\Control\Session Manager\Environment`. The key was
present and its `Path` value was present as `ExpandString`, with raw length 163 and
deterministic UTF-16LE SHA-256
`cd811235886247b90cbe851409df20781f1f7f278e3d6e1eca9b7cca87ba1efc`.
Expanding the raw registry value produced length 153 and matched the API result exactly.

PATH registry observation was read-only:

- Current-user `Environment\Path` exists as `ExpandString`, length 221, with a recorded UTF-16 SHA-256 but no candidate product entry.
- The correct `LocalMachine` environment key and `Path` value were present; no candidate product entry was present.
- No registry value was written and no PATH process or persistent value was changed.

The Phase 3 fresh-shell strategy is explicit:

```text
fresh-shell effective PATH
= persistent Machine PATH
+ persistent User PATH
```

Phase 0 did not implement or run a fresh-shell harness.

An in-memory PATH design probe normalized quotes, trailing separators, and `%LOCALAPPDATA%` equivalence; repeated insertion produced exactly one owned entry and removal preserved unrelated raw segments/order. The candidate design entry was `%LOCALAPPDATA%\local-llm-agent\install\bin`; this was only a semantic probe, not a production mutation.

The later installer must therefore use a per-user `Local` mutex, a crash-recoverable journal, stage-before-promote, exact raw PATH capture/restore, and ownership limited to the install subtree plus one stable product-bin entry. AppPaths data/config/state/cache/logs are outside that boundary. The wheel is the application artifact; manifest/lock/installer material is a separate release boundary. No public release or publication is part of Phase 0.

## Adversarial self-review

| Question | Phase 0 answer |
| --- | --- |
| Did any proof depend on the source checkout/current directory for runtime identity? | No; launcher and import probes ran from scratch `outside-cwd` directories. |
| Did uv/Python/pip escape private scratch paths? | No; uv/cache/Python/python-bin were explicit and python-bin was empty. |
| Could hostile cwd/config redirect the selected uv? | The required selection/provisioning commands used `--no-config` and scrubbed relevant config/environment variables. |
| Can an sdist enter the runtime proof? | No; hash-required binary-only dependency compilation and installation were used. |
| Can PATH be partially mutated without recovery design? | No production mutation occurred; the required design is journal-first with exact raw restoration. |
| Can lifecycle processes race? | No production lifecycle exists yet; the substrate proof establishes the required SID-qualified Local mutex shape. |
| Does Unicode/space installation work? | Yes, direct launcher version/help and import-origin probes passed in both roots. |
| Does PowerShell 5.1 run the required substrate operations? | Yes, with the explicit `File.Replace` backup constraint recorded. |
| Does daily CLI need uv/pip/network? | No production installer/runtime integration was implemented; the generated CLI proof used the installed wheel and venv only. |
| Would a future rename require moving user state? | The durable `local-llm-agent` AppPaths namespace and `LLM_AGENT_*` compatibility identities remain unchanged; no rename was attempted. |
| Were W17/core semantics or quality gates changed? | No. |
| Was anything published remotely? | No. |

Unresolved Phase 0 blockers: **0**.

## Scope and files

Intentional Phase 0 evidence files:

- `docs/wave18_phase0_inventory.json`
- `docs/wave18_phase0_seam_proof.md`

The two disposable scratch roots were removed after evidence capture. No user AppPaths were touched. No installer scripts, release manifest, runtime authority, PATH/Registry value, task authority, commit, push, tag, publication, Qwen call, live-model call, full pytest, or final installed acceptance was performed.

The final checks are `git diff --check`, empty staging, authority SHA recheck, and status inventory. Phase 1 remains not started.

**NEXT AUTHORIZED ACTION: NONE. STOP FOR SOL REVIEW.**
