<!-- Copyright (C) 2026 Simson L. Garfinkel. All Rights Reserved. -->

# Windows development setup

This guide prepares a clean Windows 11 system to develop and test Email
Collection Toolkit's planned **compiled Rust desktop experience with a Python backend**,
including the planned full ingest workflow. See [DIOXUS.md](DIOXUS.md). Use Windows
directly inside the VMware VM. WSL runs Linux and cannot validate Windows
filesystem locking, WebView2, Explorer integration, or Windows packaging.

Plan comparable trial implementations in Dioxus and Tauri before choosing a
framework. Either approach retains the Python archive engine.

**Status:** these directions have been checked against the repository and vendor
documentation, but have not yet been executed on a clean Windows VM. They set
up a development environment; they do not make the current application a
supported Windows release. The current scanner imports POSIX-only `fcntl`,
archive writing explicitly rejects Windows, and several GUI operations require
Cocoa. Full ingest remains the Windows delivery requirement, not an optional
follow-up to a viewer-only release. See [Remaining implementation](#remaining-implementation).

## 1. Finish Windows and VMware setup

1. Finish Windows Update and restart when requested.
2. Install VMware Tools and complete its restart. Keep the VM network adapter
   connected using **Share with my Mac (NAT)**.
3. If desired, enable Fusion's **Virtual Machine → Settings → Display → Use full
   resolution for Retina display** and adjust Windows **Settings → System →
   Display → Scale**.
4. Check **Settings → System → About → System type**. An Apple Silicon Mac runs
   an ARM-based Windows VM. An Intel/AMD PC normally runs x64 Windows.
5. Take a VMware snapshot named `Windows clean` before installing development
   tools. Work as your normal Windows user; elevate only when an installer asks.

Start with **x64 Windows CPython 3.12** on both kinds of machine. Windows 11 ARM
can run x64 applications under emulation. This gives the VM and the initial x64
customer build the same Python dependency architecture. An ARM64 `uv.exe` may
manage x64 Python; the interpreter and its extension modules must agree on
architecture. Native ARM64 packaging is a separate validation target.
[Microsoft emulation documentation](https://learn.microsoft.com/en-us/windows/arm/apps-on-arm-x86-emulation),
[uv architecture guidance](https://docs.astral.sh/uv/concepts/python-versions/#transparent-x86_64-emulation-on-aarch64).

## 2. Install uv; let uv install Python

### Choose the Windows architecture

Check **Settings → System → About → System type** inside Windows:

| Windows system | Native uv build | Python used by this guide |
| --- | --- | --- |
| ARM64, including a Fusion VM on Apple Silicon | `aarch64-pc-windows-msvc` | x64 CPython under Windows emulation |
| Intel/AMD x64 (also called x86_64) | `x86_64-pc-windows-msvc` | x64 CPython running natively |

Here, x64 means 64-bit Intel/AMD; do not select the 32-bit `i686`/x86 download.
An ARM Windows VM needs a **Windows** uv executable, not the macOS ARM download.
Native ARM64 uv can install and run x64 Python. Selecting x64 Python below is
intentional for the initial customer build, not a limitation of uv's ARM support.

### Automatic installation: ARM64 and x64

Open **Windows PowerShell** from Start, as your normal user. The same
[Windows installer](https://docs.astral.sh/uv/getting-started/installation/)
command supports both architectures:

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://astral.sh/uv/install.ps1 | iex"
```

The installer detects the Windows architecture and selects a build. Check its
download message: on ARM64 expect `aarch64-pc-windows-msvc`; on Intel/AMD x64
expect `x86_64-pc-windows-msvc`. Astral's
[installer](https://astral.sh/uv/install.ps1) notes that older PowerShell/Windows
combinations can detect ARM Windows as x64. That build can run under Windows 11
emulation, but use the manual method below to ensure native ARM64 uv.

The command allows the installer script for that process; it does not set
a permanent PowerShell execution policy. Close and reopen PowerShell, then run
`uv --version`. If installation succeeded with the intended build, skip the
manual method and continue to **Install Python**.

### Manual installation: explicitly choose ARM64 or x64

Use this method if automatic detection selects the wrong build or the installer
cannot run. No existing Python installation is needed.

1. Open [Astral's latest uv release](https://github.com/astral-sh/uv/releases/latest)
   in the Windows browser and expand **Assets** if necessary.
2. Download the ZIP matching the **Windows system**, not the planned Python:
   - **ARM64:** `uv-aarch64-pc-windows-msvc.zip`.
   - **Intel/AMD x64:** `uv-x86_64-pc-windows-msvc.zip`.
3. In File Explorer, extract the ZIP. Enter `%USERPROFILE%` in the address bar,
   create `.local` and then `bin` within it if absent, and copy the extracted uv
   executables into `%USERPROFILE%\.local\bin`. Ensure `uv.exe` is directly in
   `bin`, not another nested folder. Close running uv processes before replacing
   an earlier build.
4. In PowerShell, verify the selected executable and enable it for this session:

   ```powershell
   & "$env:USERPROFILE\.local\bin\uv.exe" --version
   $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
   uv --version
   ```

The PATH assignment lasts for this PowerShell session. Repeat it in a new
PowerShell window when using the manual installation. Section 3 separately
configures the MSYS2 terminal's PATH for daily development.

### Install Python: the initial x64 application target

After either uv installation method, run these in PowerShell on **both ARM64
and x64 Windows**:

```powershell
uv python install cpython-3.12-windows-x86_64-none
uv python find cpython-3.12-windows-x86_64-none
```

The last command should identify a Windows Python installation with
`windows-x86_64` in its managed installation path. If `uv` is not found, inspect
`$env:USERPROFILE\.local\bin` and the installer output before proceeding.
Do not install a second Python from the Microsoft Store, Anaconda, or MSYS2.

## 3. Install Git, Make, and shell utilities with MSYS2

The Makefile uses a POSIX shell and tools such as `xargs`. **Git Bash alone does
not supply GNU Make.** Use one MSYS2 tool environment for these utilities, while
the application itself runs with native Windows Python.

1. Download the **x86_64 installer** from [MSYS2](https://www.msys2.org/). Use the
   same installer on Windows 11 ARM for this x64 development setup.
2. Install at the default `C:\msys64` location.
3. Open **MSYS2 UCRT64** from Start and update it:

   ```bash
   pacman -Syu
   ```

4. If the update asks to close the terminal, allow it, reopen **MSYS2 UCRT64**,
   and run `pacman -Syu` again. Repeat until up to date.
5. Install the command-line tools:

   ```bash
   pacman -S --needed git make diffutils findutils
   ```

6. In that terminal, put the Windows uv installation on the shell's path:

   ```bash
   export PATH="$(cygpath -u "$USERPROFILE")/.local/bin:$PATH"
   export UV_PYTHON=cpython-3.12-windows-x86_64-none
   git --version
   make --version
   uv --version
   uv python find "$UV_PYTHON"
   ```

Repeat the two `export` commands each time you open a development terminal, or
save those two lines in the MSYS2 user's `~/.bashrc`. If uv was installed at a
custom location, use that location instead. `cygpath` converts Windows paths to
MSYS2 shell paths; it does not move files.

Use **this MSYS2 UCRT64 terminal** for all remaining Bash command blocks. Do not
mix Git Bash's runtime files with MSYS2's. The shell provides build utilities;
`uv` and the selected CPython execute Windows code. A terminal that looks like
Bash is not evidence that the application is running on Linux.

## 4. Install Rust/Dioxus tools and check WebView2

Windows 11 normally includes the **Microsoft Edge WebView2 Evergreen Runtime**.
If it is missing, use Microsoft's
[Evergreen Bootstrapper](https://developer.microsoft.com/en-us/microsoft-edge/webview2/)
to install it; the bootstrapper selects the device architecture. Merely having
the Edge browser is not the runtime check. See
[Microsoft's distribution guidance](https://learn.microsoft.com/en-us/microsoft-edge/webview2/concepts/distribution).
The Dioxus Desktop system-webview renderer uses WebView2 on Windows. The current
Python/pywebview baseline also uses WebView2 and Python.NET; the project sync
below installs those Python dependencies while the migration is pending.

The following frontend tooling prepares the Dioxus trial. Tauri-specific setup
will be documented when its trial is introduced; the framework choice is pending.

### Rust, MSVC, and the Dioxus CLI

These tools are required to develop the compiled Dioxus UI. They are not needed
by end users of the eventual packaged application.

1. Install **Visual Studio Build Tools** using Microsoft's installer. Select
   **Desktop development with C++**, the MSVC x64/x86 tools, and a Windows SDK.
   On ARM64 Windows also install the MSVC ARM64 tools for native host builds.
   See [Rust's MSVC prerequisites](https://rust-lang.github.io/rustup/installation/windows-msvc.html).
   MSYS2 Make/shell utilities do not replace the MSVC linker and SDK.
2. From [Rust's installer page](https://rust-lang.org/tools/install/), download
   and run **rustup-init.exe (ARM64)** on the ARM VM or **rustup-init.exe (x64)**
   on Intel/AMD Windows. Select the MSVC toolchain, then reopen PowerShell.
3. Install an explicit x64 toolchain for the initial customer artifact on
   either Windows architecture:

   ```powershell
   rustup toolchain install stable-x86_64-pc-windows-msvc
   rustc +stable-x86_64-pc-windows-msvc -vV
   cargo +stable-x86_64-pc-windows-msvc --version
   ```

   The `host` reported by rustc should be `x86_64-pc-windows-msvc`. In the ARM
   VM this toolchain runs under emulation. Keep native ARM64 builds as a
   separate target; installing ARM64 rustup does not force an ARM64 artifact.
4. Install and inspect the Dioxus CLI in PowerShell:

   ```powershell
   cargo +stable-x86_64-pc-windows-msvc install dioxus-cli --locked
   dx --version
   dx doctor
   ```

   CLI compilation can take time. These commands install developer tooling,
   not this application. See [Dioxus setup](https://dioxuslabs.com/learn/0.7/getting_started/).
   Record the installed versions; pin the Rust toolchain, Dioxus dependency,
   and matching CLI when the application's Cargo workspace is introduced.
   A successful `dx doctor` is not an application build or ingest test.
5. In MSYS2 UCRT64, add Cargo's tools alongside uv for the current session:

   ```bash
   export PATH="$(cygpath -u "$USERPROFILE")/.cargo/bin:$(cygpath -u "$USERPROFILE")/.local/bin:$PATH"
   ```

   Save this line in `~/.bashrc` for subsequent development terminals. Future
   project build/run/test commands must be provided through the Makefile;
   no Dioxus application targets exist in this documentation-only change.

An editor such as Visual Studio Code is useful but optional. Select
`.venv\Scripts\python.exe` as its Python interpreter after the next step.

You do **not** initially need Docker, WSL, Java, or a separate Node.js
installation. Rust/MSVC are required for the compiled UI trials. PyInstaller and Python test/lint tools are project
dependencies. The Python Pyright wrapper can provision Node automatically, and
Python Playwright supplies its driver. If a dependency unexpectedly requires a
compiler, record the package, Python architecture, and error; first check that
x64 Python and the committed lockfile are in use.
[Pyright installation](https://github.com/microsoft/pyright/blob/main/docs/installation.md).

## 5. Clone onto the Windows disk and install dependencies

Keep the checkout, virtual environment, and initial test archives on the VM's
local Windows disk. Avoid VMware shared folders, OneDrive, network shares, and
WSL mounts for this baseline: their path and locking behavior differs from
local NTFS. Choose a short writable path without spaces:

```bash
mkdir -p /c/dev
cd /c/dev
git -c core.autocrlf=false clone https://github.com/simsong/email-collection-toolkit.git
cd email-collection-toolkit
git config core.autocrlf false
git config core.longpaths true
git remote -v
git status --short --branch
git rev-parse HEAD
uv sync --locked --all-groups --python "$UV_PYTHON"
```

If Windows denies creation of `C:\dev`, choose a writable directory on the same
local disk. Use HTTPS for a read-only clone; GitHub authentication and signing
are separate setup for contributors who will publish changes. Do not copy the
Mac's virtual environment or Git/SSH credentials into the VM.

`--locked` must succeed without changing `uv.lock`. Do not work around a failure
with an unlocked update. Record the error and confirm that the intended branch
contains its matching `pyproject.toml` and lockfile.

For work on a published development branch, fetch and switch to its exact name
before syncing dependencies. A branch that exists only in a Mac checkout must
be published or transferred before the Windows clone can use it. Keep the Mac
and Windows tests on the same commit, and record `git rev-parse HEAD` in test
reports.

## 6. Install the test browser and run the initial checks

From the checkout, use the existing Makefile targets:

```bash
make install-test-browser
make ruff
make pylint
make ty
make pyright
```

Run these sequentially and investigate each failure before advancing. The first
command downloads Playwright's Chromium browser; it does not validate WebView2.
Do not pass Linux's `--with-deps` option on Windows. The two type-analysis tools
follow linting, as in `make check`.

**Current porting boundary:** successful tool installation or linting does not
mean the GUI or import works. At the documented baseline, `scanner.py` imports
`fcntl` at module load, and `gui_app.py` imports that scanner module. A
`ModuleNotFoundError: No module named 'fcntl'` is a source portability defect,
not a request to install a package named `fcntl` or switch to WSL. Other native
Windows failures may appear once this first blocker is fixed.

After backend blockers are addressed, the existing Python validation and
baseline pywebview launch entry points are:

```bash
make test
make test-e2e
make check
make gui
```

These are **future acceptance steps, not currently passing Windows commands**.
`make gui` still launches pywebview, not Dioxus. Add separate candidate-framework build,
launch, and native acceptance targets when the Rust frontend exists; the
commands above cannot certify that migration.
`make check` repeats lint/type/unit/Chromium checks and adds the website source
checks. Zola is needed for the separate `make website-build-check`, not for
the normal application checks. macOS-only smoke tests that skip on Windows
must be replaced by Windows-specific coverage before claiming native GUI parity.

## 7. Prepare for full ingest and scanner testing

Use purpose-made source fixtures and disposable destination archives. Keep source
bytes and hashes unchanged; never use the potential user's only copy as a test
fixture. Test local NTFS first, then the user's actual storage arrangement.

ClamAV is optional in the application, but both explicitly unscanned ingest and
real scanned ingest need Windows validation. Installing ClamAV alone does not
fix the current scanner's POSIX locking and socket assumptions.

For the scanner development phase, obtain the official **Windows x64 ZIP** from
[ClamAV](https://www.clamav.net/downloads), extract it to a development directory
such as `C:\Tools\ClamAV`, and retain its `conf_examples`. Use the upstream
[configuration guide](https://docs.clamav.net/manual/Usage/Configuration.html)
when implementing the Windows setup target. That target must configure a
writable definitions directory, perform an explicit FreshClam update, and run
ClamAV on demand with a loopback-only transport. Upstream Windows `clamd` uses
TCP; the current Unix-socket readiness check must be adapted. Do not register
Windows services or scheduled scans. Keep scanner run/test commands in the
Makefile rather than relying on manually started daemons.

Do not disable Windows Defender to obtain passing scanner tests. If it
quarantines EICAR before ClamAV reads it, report that as an unmet scanner-test
prerequisite; it is not proof that ClamAV routing passed.

## Remaining implementation

| Area | Required Windows work and acceptance evidence |
| --- | --- |
| Startup | Remove unconditional POSIX dependencies from Windows import paths; launch a real WebView2 window. |
| Archive writer | Replace the Windows rejection with a native exclusive writer lease, safe directory/file handling, and reparse-point checks. Test independent competing processes, termination, reacquisition, and interrupted publication on NTFS. |
| Preservation | Exercise byte preservation, mboxrd quoting, malformed MIME, invalid encodings, missing dates, duplicate Message-ID with different content, autosave exclusion, rollover, recovery, and source idempotence. A platform port must not hide existing requirement gaps. |
| Scanner | Implement Windows discovery, startup serialization, transport, readiness, timeouts, and cleanup. Exercise real EICAR routing, scanner errors, and explicit unscanned import without dropping source messages. |
| Desktop | Trial Dioxus and Tauri, choose the UI framework, and implement the typed local Python worker contract, clipboard, attachment/link opening, dialogs, shortcuts, printing, drag/export, and import-aware close/quit. Exercise native WebView2 and high-DPI scaling. |
| Packaging | Add Windows build and packaged-app test targets to the Makefile. Bundle the chosen compiled UI, Python worker, dependencies, and assets; handle WebView2, installer/uninstaller, and signing. No Windows EXE/MSI target exists yet; `make dmg` still builds macOS pywebview. |
| Release | Run common checks on Windows x64 CI and native UI/ingest tests in this VM, using the same commit as macOS. Test the resulting installer in a clean VM without Git, uv, Python, or MSYS2. Confirm the prospective user's CPU architecture and input formats. |

The initial Windows deliverable must create an archive, ingest supported source
formats, verify canonical hashes, resume safely after interruption, and support
the same search/view workflow as macOS. Windows support does not itself add
Outlook PST/OST support; confirm the user's sources against
[ON_DISK_MAIL_FORMATS.md](ON_DISK_MAIL_FORMATS.md).

Build the x64 Rust UI on Windows with the MSVC x64 toolchain and package its
Python worker with x64 Python. If PyInstaller is used for the worker, it is not
a cross-compiler or the Rust UI compiler. A working x64 build under ARM emulation
does not replace validation on Windows x64 hardware or CI.
[PyInstaller documentation](https://pyinstaller.org/en/stable/).

After setup, take a second VM snapshot named `Windows development tools`.
Record OS version/architecture, Python path, uv version, source commit, commands,
failures, and skipped tests with each validation run. Keep the initial clean
snapshot available for installer testing.
