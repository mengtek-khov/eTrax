import subprocess
import sys
from pathlib import Path

from etrax.standalone.bot_runtime_manager import _PollingTokenLock, _process_exists


def test_polling_token_lock_blocks_second_process_acquire(tmp_path: Path) -> None:
    first = _PollingTokenLock.acquire(
        root_dir=tmp_path / "polling_locks",
        token="123456:ABCDEFGHIJKLMNOPQRSTUVWX",
        bot_id="support-bot",
    )
    assert first is not None

    child_code = (
        "import sys\n"
        "from pathlib import Path\n"
        "sys.path.insert(0, str(Path.cwd() / 'src'))\n"
        "from etrax.standalone.bot_runtime_manager import _PollingTokenLock\n"
        "lock = _PollingTokenLock.acquire("
        f"root_dir=Path(r'{(tmp_path / 'polling_locks').resolve()}'), "
        "token='123456:ABCDEFGHIJKLMNOPQRSTUVWX', "
        "bot_id='other-bot')\n"
        "print('blocked' if lock is None else 'acquired')\n"
        "sys.exit(0)\n"
    )
    child = subprocess.run(
        [sys.executable, "-c", child_code],
        cwd=Path.cwd(),
        capture_output=True,
        text=True,
        check=True,
    )
    assert child.stdout.strip() == "blocked"

    first.release()


def test_process_exists_treats_winerror_87_as_stale_pid(monkeypatch) -> None:
    error = OSError(22, "The parameter is incorrect")
    error.winerror = 87

    def fake_kill(pid: int, sig: int) -> None:
        raise error

    monkeypatch.setattr("etrax.standalone.bot_runtime_manager.os.kill", fake_kill)

    assert _process_exists(999999) is False


def test_process_exists_treats_winerror_11_as_stale_pid(monkeypatch) -> None:
    error = OSError(11, "An attempt was made to load a program with an incorrect format")
    error.winerror = 11

    def fake_kill(pid: int, sig: int) -> None:
        raise error

    monkeypatch.setattr("etrax.standalone.bot_runtime_manager.os.kill", fake_kill)

    assert _process_exists(999999) is False
