from agent.skills.shell import _is_command_allowed, _split_command


def test_shell_allows_read_only_validation_commands():
    assert _is_command_allowed(_split_command("pytest -q"))
    assert _is_command_allowed(_split_command("git diff --stat"))


def test_shell_blocks_arbitrary_runtimes_and_mutating_git():
    assert not _is_command_allowed(_split_command("python -c print(1)"))
    assert not _is_command_allowed(_split_command("node script.js"))
    assert not _is_command_allowed(_split_command("pip install package"))
    assert not _is_command_allowed(_split_command("git commit -m test"))
