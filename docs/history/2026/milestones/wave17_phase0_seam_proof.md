# Wave 17 Phase 0 seam proof

Authority is limited to the Wave 17 Contract and Spec named in this task. The
baseline is commit `9408a1e12eac694941fa55aa3a9ba8ff93e52540` with tree
`9710facb27fcde832cfe9cd41da3327063448a81`. The target branch was created at
that commit and no commit, push, merge, rebase or publication was performed.

## Canonical owners

* Natural and typed task execution are owned by `AgentApplication.interact`,
  `AgentApplication.run` and `AgentApplication.resume`, with the existing
  `InteractionService` and task runtime below them.
* `/code` already has a distinct typed owner:
  `CodingApplicationService.execute`. The current CLI calls that owner
  directly, so W17 must move the call behind the one worker without changing
  the owner or workflow.
* Explicit `/read`, `/ls` and `/find` currently call
  `ToolInvocationGateway` from `command_handlers.py`. This is a Phase 0 gap:
  W17 must provide bounded, non-agentic query ownership outside the active
  task gateway.

## Cancellation seam

The existing `AgentApplication.cancel()` first requests cancellation of an
active model response and otherwise delegates to
`OrchestratorOperations.cancel_task()`. The latter calls
`mark_terminal_cancelled()` and saves a checkpoint, so it is terminalizing
and is not a request-only UI action. `CancellationToken` also has a mutable
boolean without a lock or run-generation identity. Therefore seams 1 and 2
from Section 19 are required: a thread-safe request-only signal and a
worker-owned canonical settlement path. The Phase 0 focused proof requirement
is that UI/controller cancellation cannot call terminal marking, persistence,
or canonical terminal-state mutation before worker settlement.

## Approval and event seams

`ApprovalPort` and `ApprovalDecision` are the existing approval contracts.
`ConsoleChangeApprover` and `ConsoleApproval` currently use `console.input`.
The additive `ApprovalWaitCancelled` exception and a broker adapter can
preserve those contracts while binding attention to run generation,
request fingerprint and metadata. `RuntimeEventDispatcher` already supports
additive sinks through `add_sink`/`remove_sink`; it is the selected
observational event seam. Approval events must not become a second actionable
owner.

## Prompt and headless proof

The one-off PTK spike passed with a multiline `PromptSession`, custom Enter
and Ctrl-J behavior, toolbar refresh, explicit output, mouse capture disabled,
and Windows-path/unicode input. The existing headless parser routes `run`,
`task`, `doctor`, `config`, `state`, `tools`, `extensions` and `inspect`
without constructing a chat prompt. PTK must remain lazy and TTY-only.

## Direct writer disposition

`docs/wave17_phase0_worker_output_inventory.json` is the machine-reviewable
inventory. Every currently discovered direct worker-reachable writer is marked
for the narrow Section 19 seam 4 change; health, maintenance, receipt,
continuity and historical inspector writers are outside the interactive worker
and are marked unreachable. No reachable writer is left unknown.

## Phase 0 result

Baseline and authority identity passed. The inventory is closed over the
installed exact/prefix command surface, including aliases and special exit
forms. The gaps are actionable under the listed narrow seams. No second
runtime, full-screen framework, Qwen call, or live model call is required.
