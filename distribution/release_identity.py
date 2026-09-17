"""Canonical W18 release identity and frozen release-build pins."""

from __future__ import annotations

from agent._version import VERSION as RELEASE_VERSION

SCHEMA_VERSION = "W18-RELEASE-MANIFEST-V3"
DISPLAY_NAME = "LLM Agent"
CLI_NAME = "llm-agent"
DISTRIBUTION_NAME = "local-llm-agent"
APPLICATION_NAMESPACE = "local-llm-agent"
IDENTITY_STATUS = "provisional"
PLATFORM = "windows-x64"
INSTALL_SCOPE = "user"

UV_VERSION = "0.12.13"
UV_ASSET_URL = (
    "https://github.com/astral-sh/uv/releases/download/0.12.13/"
    "uv-x86_64-pc-windows-msvc.zip"
)
UV_SHA256 = "a86c9dc7bad9b03f388583b7187c05fe9951c2e0d392217e8fd43d97787f6ec2"
UV_SIGNATURE = {
    "status": "Valid",
    "signer_subject": 'CN="OpenAI OpCo, LLC", O="OpenAI OpCo, LLC", '
    'L=San Francisco, S=California, C=US',
    "signer_thumbprint": "DF08A554062ECAD03193D010BCC9170BF5D75AE4",
    "timestamp_subject": "CN=Microsoft Public RSA Time Stamping Authority, "
    "OU=nShield TSS ESN:A500-05E0-D947, OU=Microsoft America Operations, "
    "O=Microsoft Corporation, L=Redmond, S=Washington, C=US",
    "timestamp_thumbprint": "FF73F729152A9059805E5E0832449D996EF60411",
    "evidence": "Phase 0 local Authenticode observation; not an Astral signer claim",
}

PYTHON_IMPLEMENTATION = "cpython"
PYTHON_VERSION = "3.12.14"
PYTHON_PLATFORM = "windows-x86_64"
PYTHON_BUILD_IDENTITY = "cpython-3.12.14-windows-x86_64-none"
PYTHON_BUILD_DATE = "20260901"
PYTHON_SOURCE_ARTIFACT_URL = (
    "https://github.com/astral-sh/python-build-standalone/releases/download/20260901/"
    "cpython-3.12.14+20260901-x86_64-pc-windows-msvc-install_only.tar.gz"
)
PYTHON_SOURCE_ARTIFACT_SHA256 = "e90c1b6419da3bd812dd73bb3de40287a21abf153438147639ec5e20375ea93f"

BOOTSTRAP_PIP_VERSION = "26.2.1"
BOOTSTRAP_PIP_WHEEL = "pip-26.2.1-py3-none-any.whl"
BOOTSTRAP_PIP_SHA256 = "71138adf1f4ca900cdb7d289c21b7494329f2332b6d85f0e1c42108c0384ed3e"

APPLICATION_WHEEL = "local_llm_agent-0.2.0rc1-py3-none-any.whl"
RUNTIME_LOCK = "runtime-windows-py312.lock"
BOOTSTRAP_PIP_LOCK = "bootstrap-pip.lock"
PAYLOAD_ARCHIVE = "payload-windows-x64.zip"
PAYLOAD_INVENTORY = "payload-files.json"
PAYLOAD_INVENTORY_SCHEMA = "W18-PAYLOAD-FILES-V1"
RELEASE_NOTICES = "NOTICES.md"
INSTALL_WRAPPERS = ("install.cmd", "install.ps1", "uninstall.cmd", "uninstall.ps1")

BUNDLE_MEMBERS = (
    *INSTALL_WRAPPERS,
    "release-manifest.json",
    PAYLOAD_ARCHIVE,
    PAYLOAD_INVENTORY,
    RUNTIME_LOCK,
    BOOTSTRAP_PIP_LOCK,
    APPLICATION_WHEEL,
    RELEASE_NOTICES,
)

RUNTIME_PINS = {
    "certifi": "2026.7.22",
    "charset-normalizer": "3.5.1",
    "click": "8.5.0",
    "ddgs": "9.16.0",
    "idna": "3.19",
    "lxml": "6.1.3",
    "markdown-it-py": "4.2.0",
    "mdurl": "0.1.2",
    "primp": "2.0.1",
    "prompt-toolkit": "3.0.53",
    "pygments": "2.21.0",
    "requests": "2.34.2",
    "rich": "15.0.0",
    "urllib3": "2.7.0",
    "wcwidth": "0.8.3",
}

__all__ = [
    "APPLICATION_NAMESPACE",
    "APPLICATION_WHEEL",
    "BOOTSTRAP_PIP_LOCK",
    "BOOTSTRAP_PIP_SHA256",
    "BOOTSTRAP_PIP_VERSION",
    "BOOTSTRAP_PIP_WHEEL",
    "BUNDLE_MEMBERS",
    "CLI_NAME",
    "DISPLAY_NAME",
    "DISTRIBUTION_NAME",
    "IDENTITY_STATUS",
    "INSTALL_SCOPE",
    "INSTALL_WRAPPERS",
    "PAYLOAD_ARCHIVE",
    "PAYLOAD_INVENTORY",
    "PAYLOAD_INVENTORY_SCHEMA",
    "PLATFORM",
    "PYTHON_BUILD_DATE",
    "PYTHON_BUILD_IDENTITY",
    "PYTHON_IMPLEMENTATION",
    "PYTHON_PLATFORM",
    "PYTHON_SOURCE_ARTIFACT_SHA256",
    "PYTHON_SOURCE_ARTIFACT_URL",
    "PYTHON_VERSION",
    "RELEASE_NOTICES",
    "RELEASE_VERSION",
    "RUNTIME_LOCK",
    "RUNTIME_PINS",
    "SCHEMA_VERSION",
    "UV_ASSET_URL",
    "UV_SHA256",
    "UV_SIGNATURE",
    "UV_VERSION",
]
