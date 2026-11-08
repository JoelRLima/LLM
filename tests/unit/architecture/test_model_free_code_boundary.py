"""Optional conversations are restricted to the canonical read-only code actions."""
from pathlib import Path

import pytest

from scripts.check_current_architecture import _conversation_interface_c7_errors

SOURCE = Path(__file__).resolve().parents[3] / "src/llm_agent/application/code_commands.py"


@pytest.mark.parametrize("old,new", [
    ("ConversationRuntime | None", "Any"),
    ('{"analyze", "review"}:\n        gateway = None', '{"analyze", "review", "modify"}:\n        gateway = None'),
    ('elif conversation is None:', 'elif False:'),
    ('gateway = _resolve_gateway(conversation)', 'gateway = None'),
    ('context = build_code_context(config, gateway)', 'context = build_code_context(config, None)'),
    ('context = build_code_context(config, gateway)', 'gateway = None\n    context = build_code_context(config, gateway)'),
])
def test_checker_rejects_broadened_model_free_seam(tmp_path, old, new):
    source = SOURCE.read_text(encoding="utf-8")
    assert old in source
    target = tmp_path / "src/llm_agent/application/code_commands.py"
    target.parent.mkdir(parents=True)
    target.write_text(source.replace(old, new), encoding="utf-8")
    assert _conversation_interface_c7_errors(tmp_path)


def test_current_read_only_seam_is_admitted():
    assert not _conversation_interface_c7_errors(SOURCE.parents[3])
