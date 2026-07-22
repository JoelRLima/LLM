from scripts.check_quality import (
    _ratchet_failures,
    check_architecture,
    check_markdown_links,
    check_text_encoding,
)


def test_quality_ratchet_accepts_only_the_recorded_debt() -> None:
    assert _ratchet_failures(label="test", current={"module": 12}, allowed={"module": 12}) == []


def test_quality_ratchet_rejects_new_or_increased_debt() -> None:
    failures = _ratchet_failures(
        label="test",
        current={"existing": 13, "new": 11},
        allowed={"existing": 12},
    )

    assert failures == [
        "test: existing aumentou de 12 para 13",
        "test: nova divida em new (11)",
    ]


def test_quality_ratchet_requires_baseline_cleanup_after_improvement() -> None:
    failures = _ratchet_failures(
        label="test",
        current={"smaller": 11},
        allowed={"removed": 15, "smaller": 12},
    )

    assert failures == [
        "test: reduza a baseline de smaller de 12 para 11",
        "test: remova da baseline a entrada obsoleta removed",
    ]


def test_repository_respects_stable_architecture_boundaries() -> None:
    failures, checked_modules = check_architecture()

    assert checked_modules > 0
    assert failures == []


def test_local_documentation_links_are_valid() -> None:
    failures, checked_links = check_markdown_links()

    assert checked_links > 0
    assert failures == []


def test_repository_text_files_are_bom_free_utf8() -> None:
    failures, checked_files = check_text_encoding()

    assert checked_files > 0
    assert failures == []
