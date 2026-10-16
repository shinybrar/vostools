"""``cp`` progress bars and the post-copy metadata wait, over scripted fake filesystems."""

from __future__ import annotations

import io
import sys
from contextlib import asynccontextmanager
from pathlib import Path
from types import MappingProxyType, SimpleNamespace
from typing import TYPE_CHECKING

import pytest
import typer
import typer.main
from fsspec.asyn import AsyncFileSystem
from fsspec.callbacks import DEFAULT_CALLBACK, TqdmCallback

from fss_cli import App
from fss_cli._cp import _progress_callback

from ._ansi import strip_ansi
from ._support import _cp_verified, _invoke, _source_must_not_run

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    from fsspec.callbacks import Callback


class _Clock:
    """A monotonic clock that only moves when ``cp`` sleeps."""

    def __init__(self) -> None:
        self.now = 0.0
        self.sleeps: list[float] = []

    def monotonic(self) -> float:
        return self.now

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)
        self.now += seconds


@pytest.fixture
def clock(monkeypatch: pytest.MonkeyPatch) -> _Clock:
    fake = _Clock()
    monkeypatch.setattr("fss_cli._cp.time", SimpleNamespace(monotonic=fake.monotonic))
    monkeypatch.setattr("fss_cli._cp.asyncio", SimpleNamespace(sleep=fake.sleep))
    return fake


class _ScriptedFileSystem(AsyncFileSystem):
    """Files and directories in memory, with scripted ``_info`` after a write.

    ``after_write[path]`` is consumed one entry per post-write ``_info``; the
    last entry repeats. An exception entry is raised.
    """

    cachable = False

    def __init__(
        self,
        files: dict[str, bytes] | None = None,
        infos: dict[str, dict[str, object]] | None = None,
        after_write: dict[str, list[object]] | None = None,
    ) -> None:
        super().__init__(asynchronous=True)
        self.files = dict(files or {})
        self.infos = dict(infos or {})
        self.after_write = {path: list(script) for path, script in (after_write or {}).items()}
        self.written: set[str] = set()
        self.events: list[tuple[str, str]] = []
        self.callbacks: list[tuple[str, Callback, object]] = []

    async def _info(self, path: str, **kwargs: object) -> object:
        del kwargs
        self.events.append(("info", path))
        if path in self.written and path in self.after_write:
            script = self.after_write[path]
            entry = script.pop(0) if len(script) > 1 else script[0]
            if isinstance(entry, BaseException):
                raise entry
            return MappingProxyType(entry) if isinstance(entry, dict) else entry
        if path in self.files:
            info = {"type": "file", "size": len(self.files[path]), **self.infos.get(path, {})}
            return MappingProxyType(info)
        if path == "/" or any(name.startswith(f"{path.rstrip('/')}/") for name in self.files):
            return MappingProxyType({"type": "directory", "size": 0})
        raise FileNotFoundError(path)

    def _record_callback(self, hook: str, callback: Callback) -> None:
        self.callbacks.append((hook, callback, sys.stderr))

    async def _get_file(
        self,
        rpath: str,
        lpath: str,
        callback: Callback = DEFAULT_CALLBACK,
        **kwargs: object,
    ) -> None:
        del kwargs
        self.events.append(("get_file", rpath))
        self._record_callback("get_file", callback)
        payload = self.files[rpath]
        callback.set_size(len(payload))
        Path(lpath).write_bytes(payload)  # noqa: ASYNC240
        callback.relative_update(len(payload))

    async def _put_file(
        self,
        lpath: str,
        rpath: str,
        mode: str = "overwrite",
        callback: Callback = DEFAULT_CALLBACK,
        **kwargs: object,
    ) -> None:
        del mode, kwargs
        self.events.append(("put_file", rpath))
        self._record_callback("put_file", callback)
        payload = Path(lpath).read_bytes()  # noqa: ASYNC240
        callback.set_size(len(payload))
        self.files[rpath] = payload
        self.written.add(rpath)
        callback.relative_update(len(payload))

    async def _cp_file(self, path1: str, path2: str, **kwargs: object) -> None:
        del kwargs
        self.events.append(("cp_file", path2))
        self.files[path2] = self.files[path1]
        self.written.add(path2)


class _ProgressCopyFileSystem(_ScriptedFileSystem):
    async def _cp_file(
        self,
        path1: str,
        path2: str,
        callback: Callback = DEFAULT_CALLBACK,
        **kwargs: object,
    ) -> None:
        del kwargs
        self.events.append(("cp_file", path2))
        self._record_callback("cp_file", callback)
        callback.set_size(len(self.files[path1]))
        self.files[path2] = self.files[path1]
        self.written.add(path2)
        callback.relative_update(len(self.files[path1]))


def _source(filesystem: AsyncFileSystem):
    @asynccontextmanager
    async def factory() -> AsyncIterator[AsyncFileSystem]:
        yield filesystem

    return factory


_PAYLOAD = b"0123456789"
_SIZE = len(_PAYLOAD)
_RESIDUE = "; destination residue may remain\n"


def _cross_source(
    after_write: list[object],
    *,
    source_infos: dict[str, object] | None = None,
    existing: bytes | None = None,
    existing_infos: dict[str, object] | None = None,
) -> tuple[_ScriptedFileSystem, _ScriptedFileSystem]:
    source = _ScriptedFileSystem(
        files={"/in/data.bin": _PAYLOAD},
        infos={"/in/data.bin": source_infos or {}},
    )
    destination = _ScriptedFileSystem(
        files={"/out/keep": b"", **({"/out/data.bin": existing} if existing is not None else {})},
        infos={"/out/data.bin": existing_infos or {}},
        after_write={"/out/data.bin": after_write},
    )
    return source, destination


def _run_cross(source: AsyncFileSystem, destination: AsyncFileSystem, *options: str):
    return _invoke(
        "cp",
        [*options, "src:/in/data.bin", "dst:/out/"],
        sources={"src": _source(source), "dst": _source(destination)},
    )


def _file(size: int, **tokens: object) -> dict[str, object]:
    return {"type": "file", "size": size, **tokens}


def _post_copy_infos(filesystem: _ScriptedFileSystem) -> int:
    write = next(index for index, event in enumerate(filesystem.events) if event[0] in {"put_file", "cp_file"})
    return sum(1 for event in filesystem.events[write:] if event == ("info", "/out/data.bin"))


def test_cp_polls_a_stale_zero_size_until_verified(clock: _Clock) -> None:
    source, destination = _cross_source([_file(0), _file(0), _file(_SIZE)])

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stdout, result.stderr) == (
        0,
        "",
        _cp_verified("dst:/out/data.bin", "uploaded", _SIZE),
    )
    assert clock.sleeps == [5, 5]
    assert _post_copy_infos(destination) == 3
    assert destination.files["/out/data.bin"] == _PAYLOAD


def test_cp_polls_the_previous_size_of_an_overwritten_file(clock: _Clock) -> None:
    source, destination = _cross_source([_file(3), _file(_SIZE)], existing=b"old")

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stderr) == (0, _cp_verified("dst:/out/data.bin", "uploaded", _SIZE))
    assert clock.sleeps == [5]


def test_cp_polls_an_unchanged_token_on_an_unchanged_size(clock: _Clock) -> None:
    source, destination = _cross_source(
        [_file(_SIZE, etag="before"), _file(_SIZE, etag="after")],
        source_infos={"ETag": "after"},
        existing=b"9876543210",
        existing_infos={"etag": "before"},
    )

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stderr) == (0, _cp_verified("dst:/out/data.bin", "uploaded", _SIZE))
    assert clock.sleeps == [5]


def test_cp_reports_not_yet_verified_when_the_wait_ends(clock: _Clock) -> None:
    source, destination = _cross_source([_file(0)])

    result = _run_cross(source, destination, "--metadata-wait", "12")

    assert (result.exit_code, result.stdout, result.stderr) == (
        1,
        "",
        f"cp: dst:/out/data.bin: uploaded {_SIZE} bytes\n"
        f"cp: dst:/out/data.bin: not yet verified (size 0, expected {_SIZE}){_RESIDUE}",
    )
    assert clock.sleeps == [5, 5, 2]
    assert _post_copy_infos(destination) == 4
    assert destination.files["/out/data.bin"] == _PAYLOAD


def test_cp_reports_an_unchanged_token_as_not_yet_verified(clock: _Clock) -> None:
    source, destination = _cross_source(
        [_file(_SIZE, etag="before")],
        source_infos={"ETag": "after"},
        existing=b"9876543210",
        existing_infos={"etag": "before"},
    )

    result = _run_cross(source, destination, "--metadata-wait", "5")

    assert (result.exit_code, result.stderr) == (
        1,
        f"cp: dst:/out/data.bin: uploaded {_SIZE} bytes\n"
        f"cp: dst:/out/data.bin: not yet verified (etag unchanged){_RESIDUE}",
    )
    assert clock.sleeps == [5]


def test_cp_zero_metadata_wait_reads_info_once(clock: _Clock) -> None:
    source, destination = _cross_source([_file(0), _file(_SIZE)])

    result = _run_cross(source, destination, "--metadata-wait", "0")

    assert result.exit_code == 1
    assert result.stderr.endswith(f"not yet verified (size 0, expected {_SIZE}){_RESIDUE}")
    assert clock.sleeps == []
    assert _post_copy_infos(destination) == 1


def test_cp_reports_a_third_size_without_polling(clock: _Clock) -> None:
    source, destination = _cross_source([_file(4), _file(_SIZE)])

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stderr) == (
        1,
        f"cp: dst:/out/data.bin: uploaded {_SIZE} bytes\n"
        f"cp: dst:/out/data.bin: size mismatch (expected {_SIZE}, observed 4){_RESIDUE}",
    )
    assert clock.sleeps == []
    assert _post_copy_infos(destination) == 1


def test_cp_reports_a_shared_token_mismatch_without_polling(clock: _Clock) -> None:
    source, destination = _cross_source(
        [_file(_SIZE, etag="other"), _file(_SIZE, etag="source")],
        source_infos={"ETag": "source"},
    )

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stderr) == (
        1,
        f"cp: dst:/out/data.bin: uploaded {_SIZE} bytes\ncp: dst:/out/data.bin: metadata mismatch (etag){_RESIDUE}",
    )
    assert clock.sleeps == []


def test_cp_reports_a_token_that_is_neither_source_nor_previous(clock: _Clock) -> None:
    source, destination = _cross_source(
        [_file(_SIZE, md5="third")],
        source_infos={"md5": "source"},
        existing=b"9876543210",
        existing_infos={"md5": "before"},
    )

    result = _run_cross(source, destination)

    assert result.stderr.endswith(f"metadata mismatch (md5){_RESIDUE}")
    assert clock.sleeps == []


def test_cp_ignores_tokens_reported_on_one_side_only(clock: _Clock) -> None:
    source, destination = _cross_source([_file(_SIZE, etag="destination-only")])

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stderr) == (0, _cp_verified("dst:/out/data.bin", "uploaded", _SIZE))
    assert clock.sleeps == []


def test_cp_verifies_an_empty_file_without_polling(clock: _Clock) -> None:
    source = _ScriptedFileSystem(files={"/in/data.bin": b""})
    destination = _ScriptedFileSystem(files={"/out/keep": b""}, after_write={"/out/data.bin": [_file(0)]})

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stderr) == (0, _cp_verified("dst:/out/data.bin", "uploaded", 0))
    assert clock.sleeps == []


@pytest.mark.parametrize(
    ("entry", "verdict"),
    [
        (PermissionError("denied"), "verification failure (PermissionError)"),
        ({"type": "directory", "size": 0}, "not a file after upload"),
        ({"type": "file", "size": -1}, "verification failure"),
        ("not-a-mapping", "verification failure"),
    ],
    ids=["info-raises", "not-a-file", "invalid-size", "incompatible"],
)
def test_cp_does_not_poll_unusable_post_copy_info(
    clock: _Clock,
    entry: object,
    verdict: str,
) -> None:
    source, destination = _cross_source([entry])

    result = _run_cross(source, destination)

    assert (result.exit_code, result.stderr) == (
        1,
        f"cp: dst:/out/data.bin: uploaded {_SIZE} bytes\ncp: dst:/out/data.bin: {verdict}{_RESIDUE}",
    )
    assert clock.sleeps == []


def test_cp_interrupt_during_the_wait_keeps_the_destination(monkeypatch: pytest.MonkeyPatch) -> None:
    async def interrupt(_seconds: float) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("fss_cli._cp.asyncio", SimpleNamespace(sleep=interrupt))
    source, destination = _cross_source([_file(0)])

    result = _run_cross(source, destination)

    assert result.exit_code == 130
    assert result.stderr.startswith(f"cp: dst:/out/data.bin: uploaded {_SIZE} bytes\n")
    assert "verified" not in result.stderr
    assert destination.files["/out/data.bin"] == _PAYLOAD


def test_cp_waits_for_same_source_copies_too(clock: _Clock) -> None:
    filesystem = _ScriptedFileSystem(
        files={"/in/data.bin": _PAYLOAD, "/out/keep": b""},
        after_write={"/out/data.bin": [_file(0), _file(_SIZE)]},
    )

    result = _invoke("cp", ["fs:/in/data.bin", "fs:/out"], sources={"fs": _source(filesystem)})

    assert (result.exit_code, result.stderr) == (0, _cp_verified("fs:/out/data.bin", "copied", _SIZE))
    assert clock.sleeps == [5]
    assert filesystem.callbacks == []


def test_cp_gives_each_transfer_its_own_closed_progress_bar(clock: _Clock) -> None:
    del clock
    source, destination = _cross_source([_file(_SIZE)])

    result = _run_cross(source, destination)

    assert result.exit_code == 0
    (get_hook, download, download_stream), *rest = source.callbacks
    ((put_hook, upload, upload_stream),) = destination.callbacks
    assert rest == []
    assert (get_hook, put_hook) == ("get_file", "put_file")
    assert isinstance(download, TqdmCallback)
    assert isinstance(upload, TqdmCallback)
    assert download is not upload
    assert download.tqdm is None
    assert upload.tqdm is None
    common = {"unit": "B", "unit_scale": True, "unit_divisor": 1024, "disable": None, "leave": True}
    assert download._tqdm_kwargs == {"desc": "src:/in/data.bin", "file": download_stream, **common}
    assert upload._tqdm_kwargs == {"desc": "dst:/out/data.bin", "file": upload_stream, **common}


def test_cp_passes_a_progress_bar_to_a_same_source_copy_that_accepts_one() -> None:
    filesystem = _ProgressCopyFileSystem(files={"/in/data.bin": _PAYLOAD, "/out/keep": b""})

    result = _invoke("cp", ["fs:/in/data.bin", "fs:/out/data.bin"], sources={"fs": _source(filesystem)})

    assert (result.exit_code, result.stderr) == (0, _cp_verified("fs:/out/data.bin", "copied", _SIZE))
    ((hook, callback, _stream),) = filesystem.callbacks
    assert hook == "cp_file"
    assert isinstance(callback, TqdmCallback)
    assert callback._tqdm_kwargs["desc"] == "fs:/out/data.bin"


class _Terminal(io.StringIO):
    def isatty(self) -> bool:
        return True


@pytest.mark.parametrize(("stream", "drawn"), [(_Terminal(), True), (io.StringIO(), False)], ids=["tty", "pipe"])
def test_progress_bar_draws_only_on_a_terminal(
    monkeypatch: pytest.MonkeyPatch,
    stream: io.StringIO,
    *,
    drawn: bool,
) -> None:
    monkeypatch.setattr("fss_cli._cp.sys", SimpleNamespace(stderr=stream))

    with _progress_callback("src:/in/\x1bdata.bin") as callback:
        callback.set_size(2048)
        callback.relative_update(2048)

    output = stream.getvalue()
    if drawn:
        assert "src:/in/\\x1bdata.bin" in output
        assert "100%" in output
        assert "2.00k" in output
    else:
        assert output == ""


def test_cp_outcome_line_write_failure_ends_the_copy(monkeypatch: pytest.MonkeyPatch) -> None:
    renderer_error = RuntimeError("stderr failed")

    def fail_stderr(_message: object = None, **kwargs: object) -> None:
        if kwargs.get("err") is True:
            raise renderer_error

    monkeypatch.setattr(typer, "echo", fail_stderr)
    source, destination = _cross_source([_file(_SIZE)])

    result = _run_cross(source, destination)

    assert result.exit_code == 1
    assert result.exception is renderer_error
    assert _post_copy_infos(destination) == 0


def test_cp_metadata_wait_rejects_negative_seconds() -> None:
    result = _invoke("cp", ["--metadata-wait", "-1", "memory:/a", "memory:/b"])

    assert result.exit_code == 2
    assert "--metadata-wait" in strip_ansi(result.stderr)


@pytest.mark.parametrize("copy", [True, False], ids=["recursive-copy", "file-copy-only"])
def test_cp_declares_metadata_wait_in_both_signatures(*, copy: bool) -> None:
    app = App({"memory": _source_must_not_run}, capabilities={"recursion": {"copy": copy}})
    command = typer.main.get_command(app.typer_app).commands["cp"]

    (option,) = [parameter for parameter in command.params if "--metadata-wait" in parameter.opts]

    assert (option.default, option.metavar, option.help is not None) == (60, "SECONDS", True)


def test_cp_rejects_stale_info_for_every_source_in_a_multi_file_copy(clock: _Clock) -> None:
    source = _ScriptedFileSystem(files={"/in/a.bin": b"aa", "/in/b.bin": b"bbb"})
    destination = _ScriptedFileSystem(
        files={"/out/keep": b""},
        after_write={"/out/a.bin": [_file(2)], "/out/b.bin": [_file(0)]},
    )

    result = _invoke(
        "cp",
        ["--metadata-wait", "0", "src:/in/a.bin", "src:/in/b.bin", "dst:/out"],
        sources={"src": _source(source), "dst": _source(destination)},
    )

    assert (result.exit_code, result.stderr) == (
        1,
        _cp_verified("dst:/out/a.bin", "uploaded", 2)
        + "cp: dst:/out/b.bin: uploaded 3 bytes\n"
        + f"cp: dst:/out/b.bin: not yet verified (size 0, expected 3){_RESIDUE}",
    )
    assert clock.sleeps == []
