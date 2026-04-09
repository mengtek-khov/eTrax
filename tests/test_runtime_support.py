from __future__ import annotations

from etrax.standalone.runtime_support import print_runtime_error


def test_print_runtime_error_includes_trace_details(capsys) -> None:
    print_runtime_error(
        "support-bot",
        "OSError: sample failure",
        details="Traceback (most recent call last):\n  File \"worker.py\", line 1, in run",
    )

    output_lines = capsys.readouterr().out.strip().splitlines()

    assert output_lines[0].endswith("[runtime:support-bot] ERROR: OSError: sample failure")
    assert output_lines[1].endswith("[runtime:support-bot] TRACE: Traceback (most recent call last):")
    assert output_lines[2].endswith("[runtime:support-bot] TRACE:   File \"worker.py\", line 1, in run")
