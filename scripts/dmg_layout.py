# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Render and verify the native drag-to-Applications disk-image layout.

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

from pathlib import Path
from importlib import import_module

from pydantic import BaseModel

APPLICATIONS = "Applications"
WINDOW_SIZE = (720, 420)
# Finder's window bounds include chrome; reserve room even if it shows a status bar.
FINDER_WINDOW_SIZE = (720, 480)
APP_POSITION = (180, 190)
FOLDER_POSITION = (540, 190)
ICON_SIZE = 128
STORE_WINDOW = "bwsp"
STORE_ICONS = "icvp"
STORE_LOCATION = "Iloc"
# Logical top-left coordinates: title, arrow, instruction, and footer.
BACKGROUND_INK_REGIONS = ((160, 15, 560, 105), (285, 155, 440, 225),
                          (30, 285, 690, 350), (30, 350, 690, 405))


class ImageSettings(BaseModel):
    """Named fields map directly to dmgbuild's external settings API."""

    files: list[str]
    symlinks: dict[str, str]
    icon_locations: dict[str, tuple[int, int]]
    background: str
    icon: str
    format: str = "UDZO"
    filesystem: str = "HFS+"
    window_rect: tuple[tuple[int, int], tuple[int, int]] = ((200, 200), FINDER_WINDOW_SIZE)
    default_view: str = "icon-view"
    icon_size: int = ICON_SIZE
    text_size: int = 16
    show_status_bar: bool = False
    show_toolbar: bool = False
    show_sidebar: bool = False
    show_tab_view: bool = False
    show_pathbar: bool = False


class IconView(BaseModel):
    """Finder's on-disk property names, decoded from the actual DMG."""

    iconSize: float
    backgroundType: int
    backgroundImageAlias: bytes


class WindowView(BaseModel):
    ShowToolbar: bool
    ShowSidebar: bool
    ShowStatusBar: bool


def background_image(destination: Path) -> None:
    """Draw a Retina-resolution background; the app and folder remain real icons."""
    AppKit = import_module("AppKit")

    width, height = WINDOW_SIZE
    bitmap = AppKit.NSBitmapImageRep.alloc().initWithBitmapDataPlanes_pixelsWide_pixelsHigh_bitsPerSample_samplesPerPixel_hasAlpha_isPlanar_colorSpaceName_bytesPerRow_bitsPerPixel_(
        None, width * 2, height * 2, 8, 4, True, False, AppKit.NSDeviceRGBColorSpace, 0, 0,
    )
    bitmap.setSize_(WINDOW_SIZE)
    AppKit.NSGraphicsContext.saveGraphicsState()
    try:
        AppKit.NSGraphicsContext.setCurrentContext_(
            AppKit.NSGraphicsContext.graphicsContextWithBitmapImageRep_(bitmap)
        )
        # setSize_ already maps 720x420 points to the 1440x840-pixel bitmap.
        # An additional 2x transform doubles the text and moves it off-canvas.
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.94, 0.97, 1, 1).setFill()
        AppKit.NSRectFill(((0, 0), WINDOW_SIZE))

        def text(value: str, y: int, size: int, bold: bool = False) -> None:
            paragraph = AppKit.NSMutableParagraphStyle.alloc().init()
            paragraph.setAlignment_(AppKit.NSTextAlignmentCenter)
            attributes = {
                AppKit.NSFontAttributeName: (AppKit.NSFont.boldSystemFontOfSize_(size) if bold
                                           else AppKit.NSFont.systemFontOfSize_(size)),
                AppKit.NSForegroundColorAttributeName: AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.10, 0.18, 0.28, 1),
                AppKit.NSParagraphStyleAttributeName: paragraph,
            }
            AppKit.NSString.stringWithString_(value).drawInRect_withAttributes_(
                ((24, y), (width - 48, size * 2)), attributes,
            )

        text("Email Collection Toolkit", 324, 30, True)
        arrow = AppKit.NSBezierPath.bezierPath()
        for index, point in enumerate(((296, 224), (394, 224), (394, 205),
                                       (430, 230), (394, 255), (394, 236), (296, 236))):
            if index == 0:
                arrow.moveToPoint_(point)
            else:
                arrow.lineToPoint_(point)
        arrow.closePath()
        AppKit.NSColor.colorWithSRGBRed_green_blue_alpha_(0.22, 0.49, 0.75, 1).setFill()
        arrow.fill()
        text("Drag Email Collection Toolkit to Applications to install", 70, 21)
        text("Then eject this disk and open Email Collection Toolkit from Applications.", 30, 14)
    finally:
        AppKit.NSGraphicsContext.restoreGraphicsState()
    destination.write_bytes(bytes(bitmap.TIFFRepresentation()))


def create_image(app: Path, icon: Path, destination: Path, work: Path) -> None:
    build_dmg = import_module("dmgbuild").build_dmg

    background = work / "installer-background.tiff"
    background_image(background)
    verify_background(background)
    settings = ImageSettings(
        files=[str(app)], symlinks={APPLICATIONS: "/Applications"},
        icon_locations={app.name: APP_POSITION, APPLICATIONS: FOLDER_POSITION},
        background=str(background), icon=str(icon),
    )
    build_dmg(str(destination), "Email Collection Toolkit", settings=settings.model_dump())


def verify_background(path: Path) -> None:
    """Check rendered pixels, not just metadata: double scaling clips/misplaces ink."""
    AppKit = import_module("AppKit")

    bitmap = AppKit.NSBitmapImageRep.imageRepWithContentsOfFile_(str(path))
    if bitmap is None or tuple(bitmap.size()) != WINDOW_SIZE:
        raise RuntimeError("Installer background has the wrong logical size")
    if (bitmap.pixelsWide(), bitmap.pixelsHigh()) != tuple(size * 2 for size in WINDOW_SIZE):
        raise RuntimeError("Installer background is not 2x resolution")
    counts = [0] * len(BACKGROUND_INK_REGIONS)
    for y in range(0, bitmap.pixelsHigh(), 4):
        for x in range(0, bitmap.pixelsWide(), 4):
            if bitmap.colorAtX_y_(x, y).redComponent() >= 0.8:
                continue
            for index, (left, top, right, bottom) in enumerate(BACKGROUND_INK_REGIONS):
                if left <= x / 2 <= right and top <= y / 2 <= bottom:
                    counts[index] += 1
                    break
            else:
                raise RuntimeError(f"Installer background ink outside layout at {(x / 2, y / 2)}")
    if any(count < 20 for count in counts):
        raise RuntimeError(f"Installer background is missing title, arrow, or instructions: {counts}")


def verify_layout(mount: Path, app_name: str) -> None:
    DSStore = import_module("ds_store").DSStore

    visible = {path.name for path in mount.iterdir() if not path.name.startswith(".")}
    if visible != {app_name, APPLICATIONS}:
        raise RuntimeError(f"Unexpected visible installer items: {visible}")
    with DSStore.open(str(mount / ".DS_Store"), "r") as store:
        icons = IconView.model_validate(store["."][STORE_ICONS])
        window = WindowView.model_validate(store["."][STORE_WINDOW])
        if tuple(store[app_name][STORE_LOCATION]) != APP_POSITION or tuple(store[APPLICATIONS][STORE_LOCATION]) != FOLDER_POSITION:
            raise RuntimeError("Installer app/folder positions were not saved")
    if icons.iconSize != ICON_SIZE or icons.backgroundType != 2 or not icons.backgroundImageAlias:
        raise RuntimeError("Installer icon size or background was not saved")
    if window.ShowToolbar or window.ShowSidebar or window.ShowStatusBar:
        raise RuntimeError("Installer window chrome was not hidden")
    if not (mount / ".background.tiff").is_file():
        raise RuntimeError("Installer background is missing")
    verify_background(mount / ".background.tiff")
    print("Verified Finder layout: app → Applications, large icons, background, no extra visible files")
