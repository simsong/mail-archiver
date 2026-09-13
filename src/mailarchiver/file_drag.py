# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Promote explicit export tokens to native file drags at the Cocoa boundary."""

from importlib import import_module
from pathlib import Path
from typing import Any
from uuid import uuid4

from pydantic import BaseModel, Field


class FileDrags(BaseModel):
    """Only files exported by this process can become native drag items."""

    paths: dict[str, Path] = Field(default_factory=dict)

    def register(self, path: Path) -> str:
        token = f"mailarchiver-export:{uuid4()}"
        self.paths[token] = path
        return token

    def discard(self, tokens: set[str]) -> None:
        for token in tokens:
            self.paths.pop(token, None)

    def resolve(self, token: str | None) -> Path | None:
        path = self.paths.get(token) if token else None
        return path if path is not None and path.is_file() else None


FILE_DRAGS = FileDrags()


def exported_path(writer: Any) -> Path | None:
    """Read a registered token, never interpret dragged text as a filesystem path."""
    appkit = import_module("AppKit")
    read = getattr(writer, "stringForType_", None)
    return FILE_DRAGS.resolve(read(appkit.NSPasteboardTypeString) if read else None)


def file_writer(path: Path) -> Any:
    """Supply exactly one modern file type; Cocoa owns any compatibility aliases."""
    appkit = import_module("AppKit")
    writer = appkit.NSPasteboardItem.alloc().init()
    if not writer.setString_forType_(path.as_uri(), appkit.NSPasteboardTypeFileURL):
        raise RuntimeError("could not write the exported file to the drag pasteboard")
    return writer


def replace_file_pasteboard(pasteboard: Any) -> bool:
    """Discard WebKit representations and write only the exported file type."""
    path = exported_path(pasteboard)
    if path is None:
        return False
    pasteboard.clearContents()
    if not pasteboard.writeObjects_([file_writer(path)]):
        raise RuntimeError("could not write the exported file to the drag pasteboard")
    return True


def native_drag_items(items: Any) -> Any:
    """Modern WebKit creates its session from writers, clearing the old pasteboard."""
    appkit = import_module("AppKit")
    result = []
    for item in items:
        path = exported_path(item.item())
        if path is None:
            result.append(item)
            continue
        replacement = appkit.NSDraggingItem.alloc().initWithPasteboardWriter_(file_writer(path))
        frame = item.draggingFrame()
        icon = appkit.NSWorkspace.sharedWorkspace().iconForFile_(str(path))
        replacement.setDraggingFrame_contents_(frame, icon)
        result.append(replacement)
    return result


def install_file_drag(host: type | None = None) -> None:
    """Keep WebKit's gesture and copy mask, replacing only registered export data."""
    if host is None:
        host = import_module("webview.platforms.cocoa").BrowserView.WebKitHost
    objc = import_module("objc")
    if "dragImage_at_offset_event_pasteboard_source_slideBack_" in host.__dict__:
        return

    # Add methods to the existing class: pywebview's own methods name that class
    # in super() calls, so replacing BrowserView.WebKitHost with a subclass recurses.
    def dragImage_at_offset_event_pasteboard_source_slideBack_(
        self, image, point, offset, event, pasteboard, source, slide_back
    ):
        replace_file_pasteboard(pasteboard)
        return objc.super(host, self).dragImage_at_offset_event_pasteboard_source_slideBack_(
            image, point, offset, event, pasteboard, source, slide_back
        )

    def beginDraggingSessionWithItems_event_source_(self, items, event, source):
        return objc.super(host, self).beginDraggingSessionWithItems_event_source_(
            native_drag_items(items), event, source
        )

    objc.classAddMethods(host, [
        dragImage_at_offset_event_pasteboard_source_slideBack_,
        beginDraggingSessionWithItems_event_source_,
    ])
