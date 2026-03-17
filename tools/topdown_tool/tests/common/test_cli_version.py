# SPDX-License-Identifier: Apache-2.0
# Copyright 2026 Arm Limited

from topdown_tool import __main__


def test_version_flag_short_circuits_and_prints_identifier(capsys):
    __main__.main(["--version"])

    output = capsys.readouterr().out
    assert output.strip()


def test_version_verbose_uses_structured_metadata(monkeypatch, capsys):
    monkeypatch.setattr(__main__, "version_as_dict", lambda: {"pretty_version": "demo"})

    __main__.main(["--version", "--verbose"])

    output = capsys.readouterr().out
    assert "pretty_version" in output
