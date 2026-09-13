<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Native type interfaces

These development-only stubs describe the Cocoa selectors used by the desktop
application, packaging scripts, and native probes. PyObjC exposes these APIs
dynamically, so they are absent from its Python source interface. Both ty and
Pyright load this directory; it is not installed into the runtime package.

Keep parameter and return types precise. Cocoa `id` values whose concrete class
depends on the installed window or delegate use `Any` at that native boundary.
Do not add a catch-all module or class `__getattr__` to hide missing APIs. Extend
the relevant selector declaration when native usage changes, and validate native
behavior separately from static checks.
