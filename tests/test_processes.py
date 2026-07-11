from __future__ import annotations

import pytest

from paper_galaxy import processes
from paper_galaxy.backup import publish, staging


def test_windows_process_probe_never_calls_os_kill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    probed: list[int] = []

    def windows_probe(pid: int) -> bool:
        probed.append(pid)
        return True

    def destructive_probe(pid: int, signal: int) -> None:
        del pid, signal
        raise AssertionError("Windows liveness checks must never call os.kill")

    monkeypatch.setattr(processes, "_uses_windows_process_probe", lambda: True)
    monkeypatch.setattr(processes, "_windows_process_is_alive", windows_probe)
    monkeypatch.setattr(processes.os, "kill", destructive_probe)

    assert processes.process_is_alive(1234) is True
    assert probed == [1234]


def test_backup_recovery_uses_shared_non_destructive_probe() -> None:
    assert staging._process_is_alive is processes.process_is_alive
    assert publish._process_is_alive is processes.process_is_alive


@pytest.mark.parametrize(
    ("exit_code", "expected_alive"),
    [(259, True), (0, False)],
)
def test_windows_process_handle_probe_is_non_destructive(
    monkeypatch: pytest.MonkeyPatch,
    exit_code: int,
    expected_alive: bool,
) -> None:
    calls: list[tuple[str, int]] = []

    class FakeKernel32:
        def OpenProcess(self, access: int, inherit: bool, pid: int) -> int:
            assert access == 0x1000
            assert inherit is False
            calls.append(("open", pid))
            return 99

        def GetExitCodeProcess(self, handle: int, output: object) -> int:
            assert handle == 99
            output._obj.value = exit_code  # type: ignore[attr-defined]
            calls.append(("status", handle))
            return 1

        def CloseHandle(self, handle: int) -> int:
            calls.append(("close", handle))
            return 1

    monkeypatch.setattr(processes, "_windows_kernel32", FakeKernel32)

    assert processes._windows_process_is_alive(4321) is expected_alive
    assert calls == [("open", 4321), ("status", 99), ("close", 99)]
