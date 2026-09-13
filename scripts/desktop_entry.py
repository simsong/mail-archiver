# Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.

"""Frozen application entry point (also usable through make self-test).

Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved.
"""

import sys


def main() -> int:
    """Keep headless diagnostics independent of Cocoa and user preferences."""
    if "--self-test" in sys.argv or "--self-test-gui" in sys.argv:
        from mailarchiver.self_test import main as test_main
        return test_main()
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        sys.argv.pop(1)
        from mailarchiver.__main__ import main as cli_main
        return cli_main()
    from mailarchiver.gui_app import main as gui_main
    return gui_main()


if __name__ == "__main__":
    raise SystemExit(main())
