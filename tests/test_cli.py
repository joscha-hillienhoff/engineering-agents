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


def test_validate_reports_conforming_plant(capsys):
    main(["validate"])
    assert capsys.readouterr().out.strip() == "Conforms: yes"


def test_check_replacement_with_type_b_shows_zone_1_violation(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["check-replacement", "--type", "LevelTransmitterB"])
    assert exit_info.value.code == 1
    out = capsys.readouterr().out
    assert "Conforms: no (1 violation)" in out
    assert "ex:LT-101  ex:T-101  plant:Zone1  ex:LevelTransmitterB" in out
    assert "type Level transmitter type B is not approved for Zone 1." in out


def test_check_replacement_with_type_c_conforms(capsys):
    main(["check-replacement", "--type", "LevelTransmitterC"])
    out = capsys.readouterr().out
    assert "ex:LT-101, ex:LT-102, ex:LT-103, ex:LT-301" in out
    assert out.strip().endswith("Conforms: yes")


def test_check_replacement_with_unknown_type_is_a_usage_error(capsys):
    with pytest.raises(SystemExit) as exit_info:
        main(["check-replacement", "--type", "LevelTransmitterZ"])
    assert exit_info.value.code == 2
    assert "unknown device type" in capsys.readouterr().err


def test_format_table_aligns_columns():
    rows = [{"a": "x", "b": "long value"}, {"a": "yyy", "b": ""}]
    assert format_table(rows) == "a    b\n---  ----------\nx    long value\nyyy"


def test_format_table_without_rows():
    assert format_table([]) == "(no results)"
