import pytest

from engineering_agents.cli import format_table, main


def test_query_prints_table(capsys):
    main(["query", "installation-points", "LevelTransmitterA"])
    lines = capsys.readouterr().out.splitlines()
    assert lines[0].split() == ["device", "location", "unit", "zone", "medium"]
    assert len(lines) == 2 + 4  # header, separator, four devices
    assert lines[2].startswith("ex:LT-101")


def test_missing_argument_is_a_usage_error(capsys):
    with pytest.raises(SystemExit):
        main(["query", "type-approvals"])
    assert "needs an argument" in capsys.readouterr().err


def test_format_table_aligns_columns():
    rows = [{"a": "x", "b": "long value"}, {"a": "yyy", "b": ""}]
    assert format_table(rows) == "a    b\n---  ----------\nx    long value\nyyy"


def test_format_table_without_rows():
    assert format_table([]) == "(no results)"
