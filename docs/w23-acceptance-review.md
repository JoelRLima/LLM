# W23 acceptance review

The review source starts from the authenticated delivery tree
`e3fa6c642697cb0de6289eb6bbcbfa351072e634`. CAND-03 source bytes remain
unchanged. This branch does not select, activate, release or merge anything.

## Prospective canonical path

`scripts/w23_authority.py` is the version-specific prospective entry point.
`verify_prospective` authenticates a detached proposal by an independently
expected SHA-256, authenticates the exact v009 predecessor, checks the complete
candidate membership, raw bytes, executable modes and Git tree, and separates
archived tooling verification from current tooling identity. It requires every
mandatory gate and binds each observation to that exact source tree and its
hashed raw evidence. A simulated result cannot satisfy an acceptance gate.

The legacy v007 API in `scripts/w23_acceptance.py` remains unchanged. Its
historical rejection of a different authority is not relabelled as current
success. The v009 raw `.git/index` mismatch is permanently a historical finding;
current semantic identity does not repair the old raw pin.

A detached proposal, Contract, Spec, candidate manifest, predecessor bytes,
current tooling record and gate observations are delivered with the final audit
handoff. They remain outside the source tree to avoid a manifest hashing itself.
Their installation bindings must resolve wholly under the immutable
`quality/w23/authority/v010-cand03/` namespace. No private `.agent-local` files,
selector, caches, local test residues or audit dumps are included in this PR.

`verify_owner_approval` is a future verification operation only. It requires a
separately owner-approved digest for the exact proposal and source tree, every
mandatory gate, the exact predecessor, and denied activation/publication/merge.
There is no discovered/default approval digest. No owner approval is shipped,
and even successful verification does not write an active selector or declare
GREEN. Consumers must obtain the approval digest through the owner's trusted
review channel, never derive trust from the untrusted approval file itself.

## Transaction rehearsal

`scripts/w23_authority_transaction.py` refuses Git worktrees and requires a
unique disposable-fixture marker. It authenticates all input bytes and all
absent destinations before writes, rechecks predecessor and created identities,
changes the fixture selector last, and removes only exact owned files on
rollback. Modified or substituted files are retained and reported as
`ROLLBACK_UNCONFIRMED`. These tests are **SIMULATED**, not a production activation
or proof against malicious concurrent processes of the same user. Live
activation remains a separate owner-controlled operation; this fixture API must
never be repurposed by dropping its checks.

## CI execution boundary

Full pytest, installed-wheel acceptance and W18 installed-product lifecycle run
only on disposable GitHub-hosted runners. Linux uses a dedicated systemd cgroup
with 4 GiB memory, 128 tasks, a 1,800-second deadline and scope-owned cleanup.
Windows uses an owned Job Object with 4 GiB aggregate memory, 3 GiB per process,
128 processes and a 1,800-second deadline. Source identities and bounded logs are
preserved after failures. Scratch is external to the source checkout to prevent
recursive test copies. Qwen is not loaded. Native Windows symlink skips remain
visible. No baseline, historical authority or evidence is weakened.
