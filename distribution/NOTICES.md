# W18 local release notes

This bundle is a first-party local release candidate for the provisional
`LLM Agent` / `llm-agent` identity. It contains a self-contained Windows x64
payload: the frozen CPython runtime, the hash-bound runtime dependency closure,
and the application. The user installer consumes these bytes offline.

uv and pip are release-build tools only. They are recorded as build evidence
in the manifest and are not invoked by the production installer. If the frozen
CPython distribution retains standard-library `ensurepip` bytes, they remain
inert upstream runtime content and are never invoked by W18.

The runtime lock targets Windows x64 with the Phase 0-frozen CPython 3.12.14
embedded runtime. The bootstrap and runtime locks document release-build
inputs for hash mode and binary-only installation; they are not an install-time
package-manager contract.
