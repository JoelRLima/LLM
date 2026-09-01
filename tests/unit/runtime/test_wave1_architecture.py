from __future__ import annotations

import pytest

from scripts.check_wave1_architecture import check_source, run_checks


def test_wave1_static_ownership_gates_are_green() -> None:
    assert run_checks() == []


def test_s1_detects_model_gateway_complete_through_an_alias() -> None:
    findings = check_source(
        "gw = context.model_gateway\ngw.complete(request)\n",
        "agent/runtime/adversarial_consumer.py",
    )

    assert any(item.startswith("S1:") for item in findings)


def test_s3_detects_direct_model_call_metric_publication() -> None:
    findings = check_source(
        'context.record_metric("model_call", data)\n',
        "agent/runtime/adversarial_consumer.py",
    )

    assert any(item.startswith("S3:") for item in findings)


def test_s4_tracks_capability_dataflow_without_a_capability_named_variable() -> None:
    findings = check_source(
        'c = profile["capabilities"]\nc.get("streaming")\n',
        "agent/runtime/adversarial_consumer.py",
    )

    assert any(item.startswith("S4:") for item in findings)


def test_s1_rejects_a_bound_gateway_method_alias() -> None:
    findings = check_source(
        "gw = context.model_gateway\ninvoke = gw.complete\ninvoke(request)\n",
        "agent/runtime/adversarial_consumer.py",
    )

    assert any(item.startswith("S1:") for item in findings)


def test_s3_rejects_a_bound_metric_method_alias() -> None:
    findings = check_source(
        'rec = context.record_metric\nrec("model_call", data)\n',
        "agent/runtime/adversarial_consumer.py",
    )

    assert any(item.startswith("S3:") for item in findings)


def test_s4_rejects_a_bound_capability_getter_alias() -> None:
    findings = check_source(
        'capabilities_alias = profile["capabilities"]\n'
        'getter = capabilities_alias.get\ngetter("streaming")\n',
        "agent/runtime/adversarial_consumer.py",
    )

    assert any(item.startswith("S4:") for item in findings)


def test_bound_method_aliases_do_not_create_unrelated_blanket_bans() -> None:
    findings = check_source(
        "invoke = client.complete\ninvoke(request)\n"
        "getter = config.get\ngetter(\"unrelated\")\n"
        "rec = logger.record_metric\nrec(\"other\", data)\n",
        "agent/runtime/adversarial_consumer.py",
    )

    assert findings == []


@pytest.mark.parametrize(
    ("source", "gate"),
    [
        (
            "from agent.llm.contracts import ModelGateway\n"
            "def rogue(client: ModelGateway, request):\n"
            "    return client.complete(request)\n",
            "S1",
        ),
        (
            "from agent.llm.contracts import ModelGateway as GatewayPort\n"
            "def rogue(client: GatewayPort, request):\n"
            "    first = client\n"
            "    second = first\n"
            "    invoke = getattr(second, 'stream')\n"
            "    return invoke(request)\n",
            "S1",
        ),
        (
            "from agent.llm.contracts import ModelGateway\n"
            "def rogue(client: ModelGateway, request):\n"
            "    invoke = getattr(client, 'complete')\n"
            "    hop = invoke\n"
            "    return hop(request)\n",
            "S1",
        ),
        (
            "from agent.llm.contracts import ModelGateway\n"
            "def rogue(client: ModelGateway, request):\n"
            "    return getattr(client, 'complete')(request)\n",
            "S1",
        ),
    ],
)
def test_s1_tracks_typed_gateway_aliases_and_literal_getattr(
    source: str, gate: str
) -> None:
    findings = check_source(source, "agent/runtime/adversarial_consumer.py")

    assert any(item.startswith(f"{gate}:") for item in findings)


@pytest.mark.parametrize(
    "source",
    [
        (
            "from agent.runtime.context import TaskExecutionContext\n"
            "def rogue(context: TaskExecutionContext, data):\n"
            "    rec = context.record_metric\n"
            "    hop = rec\n"
            "    hop('model_call', data)\n"
        ),
        (
            "from agent.runtime.context import TaskExecutionContext\n"
            "def rogue(context: TaskExecutionContext, data):\n"
            "    rec = getattr(context, 'record_metric')\n"
            "    rec('model_call', data)\n"
        ),
        (
            "from agent.runtime.context import TaskExecutionContext\n"
            "def rogue(context: TaskExecutionContext, data):\n"
            "    getattr(context, 'record_metric')('model_call', data)\n"
        ),
    ],
)
def test_s3_tracks_typed_context_metric_aliases_and_literal_getattr(source: str) -> None:
    findings = check_source(source, "agent/runtime/adversarial_consumer.py")

    assert any(item.startswith("S3:") for item in findings)


@pytest.mark.parametrize(
    "source",
    [
        (
            "from agent.llm.contracts import ModelGateway\n"
            "def rogue(client: ModelGateway):\n"
            "    caps = getattr(client, 'capabilities')\n"
            "    getter = caps.get\n"
            "    hop = getter\n"
            "    hop('streaming')\n"
        ),
        (
            "from agent.llm.contracts import ModelGateway\n"
            "def rogue(client: ModelGateway):\n"
            "    caps = getattr(client, 'capabilities')\n"
            "    getter = getattr(caps, 'get')\n"
            "    getter('streaming')\n"
        ),
        (
            "from agent.llm.contracts import ModelGateway\n"
            "def rogue(client: ModelGateway):\n"
            "    caps = getattr(client, 'capabilities')\n"
            "    getattr(caps, 'get')('streaming')\n"
        ),
    ],
)
def test_s4_tracks_typed_gateway_capability_aliases_and_literal_getattr(source: str) -> None:
    findings = check_source(source, "agent/runtime/adversarial_consumer.py")

    assert any(item.startswith("S4:") for item in findings)


def test_semantic_alias_policy_allows_unrelated_typed_objects() -> None:
    findings = check_source(
        "class Unrelated:\n"
        "    def complete(self, request): ...\n"
        "    def record_metric(self, kind, data): ...\n"
        "    capabilities = {}\n"
        "def allowed(client: Unrelated, request, data):\n"
        "    invoke = client.complete\n"
        "    invoke(request)\n"
        "    rec = client.record_metric\n"
        "    rec('model_call', data)\n"
        "    caps = client.capabilities\n"
        "    getter = caps.get\n"
        "    getter('streaming')\n",
        "agent/runtime/adversarial_consumer.py",
    )

    assert findings == []
