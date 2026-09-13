<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Third-party and separately licensed material

Email Collection Toolkit retains the notices and license terms of material that is not
covered by the repository's `COPYRIGHT` notice. A packaged application must
include this file, `COPYRIGHT`, and the complete license texts collected from
the exact runtime environment used to build that application.

## Material stored in this repository

| Component | Location | License and notice |
| --- | --- | --- |
| Tabulator 6.5.2 | `gui/vendor/tabulator/` | MIT; Copyright (c) 2015-2026 Oli Folkerd. The complete upstream text is in `gui/vendor/tabulator/LICENSE`. |
| Envelope Rainbow website theme | `website/themes/envelope-rainbow/` | MIT; Copyright (c) 2026 Simson L. Garfinkel. The complete text is in `website/themes/envelope-rainbow/LICENSE`. |
| proxy_tools 0.1.0 | Runtime dependency | BSD; Copyright (c) 2013 Armin Ronacher and Copyright (c) 2014 Jonathan Tushman. Its wheel metadata incorrectly says MIT and omits the upstream license file, so the reviewed upstream text is retained in `licenses/proxy_tools-BSD.txt`. |

The Tabulator directory is vendored and must remain byte-for-byte identical to
the reviewed upstream files. Minified files are never rewritten to add project
headers.

The upstream website illustration and photograph retain their separate terms
and attribution in `website/static/images/ATTRIBUTION.md`; their source bytes
are excluded from project notice insertion. Shared generated workflow files
are also retained without rewriting their notices.

## Python runtime dependencies

The locked runtime includes permissively licensed packages and the following
LGPL-2.1-or-later compression packages: `inflate64`, `multivolumefile`,
`py7zr`, `pybcj`, and `pyppmd`. LGPL components remain dynamically imported
Python packages; a release must preserve their notices, license texts, and the
rights required by their licenses.

The PyObjC framework wheels share the PyObjC MIT terms. Some small framework
wheels omit a duplicate license file; the runtime bundle retains the complete
license shipped by other PyObjC wheels in the same locked family.

`pylint` and `astroid` are GPL/LGPL development tools, and `pytest` and
Playwright are test tools. They are not runtime dependencies and must not be
included in an application bundle.

Run `make runtime-license-check` in each platform's production build
environment. Run `make runtime-license-bundle LICENSE_OUTPUT=PATH` to create a
ready-to-package notices directory containing `COPYRIGHT`, this file, a
machine-readable inventory, and complete license files for the exact runtime
closure. The audit rejects unknown licenses, GPL/AGPL runtime packages, and
development packages in that closure. Platform-specific dependencies mean that
a macOS audit cannot stand in for the required Windows audit, or vice versa.

This inventory is an engineering control, not legal advice. The copyright
owner or counsel must approve the notices and redistribution terms before a
public binary release.
