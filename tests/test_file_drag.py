# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Requirements: Finder receives exact exported files, never URL shortcut data."""

import sys
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier
from importlib import import_module
from pathlib import Path
from typing import Any
from urllib.parse import unquote, urlsplit

import pytest
from pydantic import BaseModel, Field

from mailarchiver.file_drag import FILE_DRAGS, FileDrags, file_writer, install_file_drag, native_drag_items, replace_file_pasteboard
from mailarchiver.gui_app import GuiApi
from mailarchiver.gui_service import describe_message, write_attachment
from tests.test_gui_service import SIMPLE_MESSAGE, make_gui_archive


def test_export_tokens_preserve_eml_bytes_and_expire_on_close(tmp_path: Path) -> None:
    """An explicit drag exports verified bytes and grants access only until close."""
    archive = make_gui_archive(tmp_path)
    api = GuiApi(archive, temporary_directory=tmp_path / "exports")
    result = api.prepare_drag([1])
    path = FILE_DRAGS.resolve(result["token"])
    assert path is not None and path.name == result["filename"]
    assert path.suffix == ".eml" and path.read_bytes() == SIMPLE_MESSAGE
    assert "url" not in result
    api.close()
    assert FILE_DRAGS.resolve(result["token"]) is None
    with pytest.raises(ValueError, match="viewer is closed"):
        api.prepare_drag([1])


def test_file_drags_reject_paths_and_missing_exports(tmp_path: Path) -> None:
    """Dragged text cannot grant access to an arbitrary file or stale export."""
    exports = FileDrags()
    path = tmp_path / "message.eml"
    path.write_bytes(b"Subject: fixture\n\nbody\n")
    token = exports.register(path)
    assert exports.resolve(str(path)) is None
    assert exports.resolve(path.as_uri()) is None
    assert exports.resolve(token) == path
    path.unlink()
    assert exports.resolve(token) is None


@pytest.mark.skipif(sys.platform != "darwin", reason="requires the macOS pasteboard")
@pytest.mark.parametrize("suffix", [".eml", ".zip"])
def test_native_drag_writers_advertise_files_only(tmp_path: Path, suffix: str) -> None:
    """Both WebKit drag APIs produce a real file writer, preserving unusual names."""
    appkit = import_module("AppKit")
    path = tmp_path / f"résumé #100% attachment{suffix}"
    path.write_bytes(b"Subject: exact\r\n\r\nunchanged\xff\r\n")
    assert list(file_writer(path).types()) == [appkit.NSPasteboardTypeFileURL]
    token = FILE_DRAGS.register(path)
    pasteboard = appkit.NSPasteboard.pasteboardWithUniqueName()
    try:
        pasteboard.setString_forType_(token, appkit.NSPasteboardTypeString)
        pasteboard.setString_forType_(path.as_uri(), appkit.NSPasteboardTypeURL)
        assert replace_file_pasteboard(pasteboard)
        assert Path(unquote(urlsplit(pasteboard.stringForType_(appkit.NSPasteboardTypeFileURL) or "").path)).samefile(path)
        assert appkit.NSPasteboardTypeURL not in pasteboard.types()
        assert appkit.NSPasteboardTypeString not in pasteboard.types()
        urls = pasteboard.readObjectsForClasses_options_([appkit.NSURL], None)
        assert len(urls) == 1 and Path(urls[0].path()).read_bytes() == path.read_bytes()

        writer = appkit.NSPasteboardItem.alloc().init()
        writer.setString_forType_(token, appkit.NSPasteboardTypeString)
        item = appkit.NSDraggingItem.alloc().initWithPasteboardWriter_(writer)
        item.setDraggingFrame_contents_(((10, 20), (32, 32)), None)
        converted = native_drag_items([item])
        assert converted[0].draggingFrame() == item.draggingFrame()
        assert list(converted[0].item().types()) == [appkit.NSPasteboardTypeFileURL]
        pasteboard.clearContents()
        assert pasteboard.writeObjects_([converted[0].item()])
        assert Path(unquote(urlsplit(pasteboard.stringForType_(appkit.NSPasteboardTypeFileURL) or "").path)).samefile(path)
        assert appkit.NSPasteboardTypeURL not in pasteboard.types()
        assert appkit.NSPasteboardTypeString not in pasteboard.types()

        pasteboard.clearContents()
        pasteboard.setString_forType_(path.as_uri(), appkit.NSPasteboardTypeString)
        assert not replace_file_pasteboard(pasteboard)
        assert pasteboard.stringForType_(appkit.NSPasteboardTypeString) == path.as_uri()
        writer.setString_forType_("ordinary text", appkit.NSPasteboardTypeString)
        assert native_drag_items([item])[0] is item
    finally:
        FILE_DRAGS.discard({token})
        pasteboard.releaseGlobally()


class NativeDragObservation(BaseModel):
    """Arguments received through Objective-C dispatch in the controlled host."""

    calls: list[str] = Field(default_factory=list)
    point: tuple[float, float] | None = None
    offset: tuple[float, float] | None = None
    slide_back: bool | None = None
    event: Any = None
    source: Any = None
    items: list[Any] = Field(default_factory=list)


@pytest.mark.skipif(sys.platform != "darwin", reason="requires Objective-C/AppKit dispatch")
def test_installed_native_selectors_convert_files_and_forward_arguments(tmp_path: Path) -> None:
    """Exercise native method injection without starting an interactive OS drag.

    A controlled NSView superclass is necessary to observe the forwarded native
    arguments without taking over the desktop mouse. Cocoa objects and selector
    dispatch are real; only the final interactive AppKit session is replaced.
    """
    appkit = import_module("AppKit")
    observed = NativeDragObservation()
    pasteboard = appkit.NSPasteboard.pasteboardWithUniqueName()

    class FileDragTestBase(appkit.NSView):
        def dragImage_at_offset_event_pasteboard_source_slideBack_(
            self, image, point, offset, event, board, source, slide_back
        ):
            observed.calls.append("legacy")
            observed.point = tuple(point)
            observed.offset = tuple(offset)
            observed.event = event
            observed.source = source
            observed.slide_back = bool(slide_back)
            assert board == pasteboard

        def beginDraggingSessionWithItems_event_source_(self, items, event, source):
            observed.calls.append("modern")
            observed.items = list(items)
            observed.event = event
            observed.source = source
            return None

    class FileDragTestHost(FileDragTestBase):
        pass

    path = tmp_path / "native dispatch.eml"
    path.write_bytes(SIMPLE_MESSAGE)
    token = FILE_DRAGS.register(path)
    try:
        install_file_drag(FileDragTestHost)
        install_file_drag(FileDragTestHost)
        host = FileDragTestHost.alloc().initWithFrame_(((0, 0), (100, 100)))
        pasteboard.setString_forType_(token, appkit.NSPasteboardTypeString)
        # Native selectors inherited from NSView carry struct/BOOL signatures;
        # dispatch through pyobjc_instanceMethods crosses the actual ObjC bridge.
        native = host.pyobjc_instanceMethods
        native.dragImage_at_offset_event_pasteboard_source_slideBack_(
            None, (12, 34), (5, 6), None, pasteboard, host, True
        )
        assert observed.calls == ["legacy"]
        assert observed.point == (12, 34) and observed.offset == (5, 6)
        assert observed.slide_back and observed.source == host and observed.event is None
        urls = pasteboard.readObjectsForClasses_options_([appkit.NSURL], None)
        assert len(urls) == 1 and Path(urls[0].path()).samefile(path)
        assert appkit.NSPasteboardTypeString not in pasteboard.types()

        writer = appkit.NSPasteboardItem.alloc().init()
        writer.setString_forType_(token, appkit.NSPasteboardTypeString)
        item = appkit.NSDraggingItem.alloc().initWithPasteboardWriter_(writer)
        item.setDraggingFrame_contents_(((1, 2), (32, 32)), None)
        assert native.beginDraggingSessionWithItems_event_source_([item], None, host) is None
        assert observed.calls == ["legacy", "modern"]
        assert observed.source == host and observed.event is None
        assert len(observed.items) == 1
        pasteboard.clearContents()
        assert pasteboard.writeObjects_([observed.items[0].item()])
        urls = pasteboard.readObjectsForClasses_options_([appkit.NSURL], None)
        assert len(urls) == 1 and Path(urls[0].path()).read_bytes() == SIMPLE_MESSAGE
        assert appkit.NSPasteboardTypeString not in pasteboard.types()
        assert appkit.NSPasteboardTypeURL not in pasteboard.types()
    finally:
        FILE_DRAGS.discard({token})
        pasteboard.releaseGlobally()


def test_drag_export_cannot_be_overwritten_by_attachment_or_later_drag(tmp_path: Path) -> None:
    """A same-name opened attachment cannot replace verified bytes behind a token."""
    archive = make_gui_archive(tmp_path)
    api = GuiApi(archive, temporary_directory=tmp_path / "exports")
    try:
        first = api.prepare_drag([1])
        path = FILE_DRAGS.resolve(first["token"])
        attachment = describe_message(archive, 2).attachments[0]
        # open_attachment writes an attachment's safe basename in this shared root.
        write_attachment(archive, 2, attachment.part_id, api.temporary_directory / first["filename"])
        second = api.prepare_drag([1])
        assert path is not None and path.read_bytes() == SIMPLE_MESSAGE
        assert path != FILE_DRAGS.resolve(second["token"])
        assert (api.temporary_directory / first["filename"]).read_bytes() != SIMPLE_MESSAGE
    finally:
        api.close()


def test_concurrent_close_and_export_cannot_leave_live_tokens(tmp_path: Path) -> None:
    """Closing during bridge export either revokes its token or rejects preparation."""
    archive = make_gui_archive(tmp_path)
    initial = set(FILE_DRAGS.paths)
    for attempt in range(8):
        api = GuiApi(archive, temporary_directory=tmp_path / f"exports-{attempt}")
        barrier = Barrier(2, timeout=5)

        def prepare() -> str | None:
            barrier.wait()
            try:
                return api.prepare_drag([1])["token"]
            except ValueError as error:
                assert str(error) == "message viewer is closed"
                return None

        def close() -> None:
            barrier.wait()
            api.close()

        with ThreadPoolExecutor(max_workers=2) as executor:
            prepared = executor.submit(prepare)
            closed = executor.submit(close)
            token = prepared.result(timeout=10)
            closed.result(timeout=10)
        assert FILE_DRAGS.resolve(token) is None
        with pytest.raises(ValueError, match="viewer is closed"):
            api.prepare_drag([1])
    assert set(FILE_DRAGS.paths) == initial
