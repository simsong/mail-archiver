"""Discover trusted ingest plug-ins from validated directory manifests."""

from __future__ import annotations

import hashlib
import importlib
import importlib.util
import inspect
import sys
import tomllib
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from pydantic import BaseModel, ConfigDict, ValidationError

from .plugin_api import LoadedPlugin, PluginCapabilities, PluginContext, PluginManifest, PluginRegistry, PluginType


class PluginDiscoveryError(RuntimeError):
    """Plug-in discovery failed before a usable registry could be built."""


class _Candidate(BaseModel):
    model_config = ConfigDict(frozen=True)

    manifest: PluginManifest
    directory: Path
    manifest_path: Path
    builtin: bool


def builtin_plugin_directory() -> Path:
    """Return the packaged, trusted built-in plug-in root."""
    return Path(__file__).with_name("plugins")


def load_plugins(extra_dirs: Iterable[Path] = ()) -> PluginRegistry:
    """Validate, load, and freeze built-in and explicitly trusted plug-ins."""
    builtin = builtin_plugin_directory().resolve()
    roots: list[tuple[Path, bool]] = [(builtin, True)]
    seen_roots = {builtin}
    for path in extra_dirs:
        resolved = Path(path).resolve()
        if resolved not in seen_roots:
            roots.append((resolved, False))
            seen_roots.add(resolved)

    candidates = _validated_candidates(roots)
    files = tuple(_load(candidate) for candidate in candidates if candidate.manifest.plugin_type == "file")
    context = PluginContext(files=files)
    sources = tuple(
        _load(candidate, context) for candidate in candidates if candidate.manifest.plugin_type == "source"
    )
    return PluginRegistry(
        sources=sources,
        files=files,
    )


def _validated_candidates(roots: list[tuple[Path, bool]]) -> tuple[_Candidate, ...]:
    candidates: list[_Candidate] = []
    errors: list[str] = []
    for root, builtin in roots:
        if not root.is_dir():
            errors.append(f"plug-in directory does not exist: {root}")
            continue
        for plugin_type, category in (("source", "sources"), ("file", "files")):
            category_path = root / category
            if not category_path.exists():
                continue
            if not category_path.is_dir():
                errors.append(f"plug-in category is not a directory: {category_path}")
                continue
            if not category_path.resolve().is_relative_to(root):
                errors.append(f"plug-in category escapes configured root: {category_path}")
                continue
            for directory in sorted(category_path.iterdir(), key=lambda item: item.name):
                if not directory.is_dir() or directory.name.startswith(".") or directory.name == "__pycache__":
                    continue
                if not directory.resolve().is_relative_to(root):
                    errors.append(f"plug-in directory escapes configured root: {directory}")
                    continue
                candidate = _read_candidate(directory, plugin_type, builtin, errors)
                if candidate is not None:
                    candidates.append(candidate)

    duplicates: dict[tuple[PluginType, str], list[Path]] = {}
    for candidate in candidates:
        key = (candidate.manifest.plugin_type, candidate.manifest.kind)
        duplicates.setdefault(key, []).append(candidate.manifest_path)
    for (plugin_type, kind), paths in duplicates.items():
        if len(paths) > 1:
            errors.append(
                f"duplicate {plugin_type} plug-in kind {kind}: " + ", ".join(str(path) for path in sorted(paths))
            )
    if errors:
        raise PluginDiscoveryError("invalid plug-in manifests:\n- " + "\n- ".join(sorted(errors)))
    return tuple(sorted(candidates, key=lambda item: (item.manifest.priority, item.manifest.kind)))


def _read_candidate(
    directory: Path, expected_type: PluginType, builtin: bool, errors: list[str]
) -> _Candidate | None:
    manifest_path = directory / "plugin.toml"
    if not manifest_path.is_file():
        errors.append(f"plug-in directory has no plugin.toml: {directory}")
        return None
    try:
        with manifest_path.open("rb") as source:
            manifest = PluginManifest.model_validate(tomllib.load(source))
    except (OSError, tomllib.TOMLDecodeError, ValidationError) as error:
        errors.append(f"{manifest_path}: {error}")
        return None
    if manifest.plugin_type != expected_type:
        errors.append(
            f"{manifest_path}: declares {manifest.plugin_type}, but is under {expected_type}s"
        )
    if manifest.kind != directory.name:
        errors.append(f"{manifest_path}: kind {manifest.kind!r} does not match directory {directory.name!r}")
    entrypoint_error = _validate_entrypoint(directory, manifest.entrypoint, builtin)
    if entrypoint_error is not None:
        errors.append(f"{manifest_path}: {entrypoint_error}")
    if manifest.plugin_type != expected_type or manifest.kind != directory.name or entrypoint_error is not None:
        return None
    return _Candidate(
        manifest=manifest,
        directory=directory.resolve(),
        manifest_path=manifest_path.resolve(),
        builtin=builtin,
    )


def _entrypoint_fields(entrypoint: str) -> tuple[str, str] | None:
    if entrypoint.count(":") != 1:
        return None
    module_name, attribute = entrypoint.split(":")
    if not attribute.isidentifier() or not module_name:
        return None
    return module_name, attribute


def _validate_entrypoint(directory: Path, entrypoint: str, builtin: bool) -> str | None:
    fields = _entrypoint_fields(entrypoint)
    if fields is None:
        return f"invalid entrypoint {entrypoint!r}; expected module:attribute"
    module_name, _attribute = fields
    parts = module_name.split(".")
    if not all(part.isidentifier() for part in parts):
        return f"invalid entrypoint module {module_name!r}"
    if builtin:
        if not module_name.startswith("mailarchiver."):
            return "built-in entrypoint must be within the mailarchiver package"
        return None
    module_path, package_path = _external_module_paths(directory, parts)
    if not module_path.is_file() and not package_path.is_file():
        return f"entrypoint module is not inside its plug-in directory: {module_name}"
    return None


def _external_module_paths(directory: Path, parts: list[str]) -> tuple[Path, Path]:
    base = directory.resolve().joinpath(*parts)
    module_path = base.with_suffix(".py").resolve()
    package_path = (base / "__init__.py").resolve()
    if not module_path.is_relative_to(directory.resolve()) or not package_path.is_relative_to(directory.resolve()):
        raise PluginDiscoveryError(f"plug-in entrypoint escapes its directory: {directory}")
    return module_path, package_path


def _load(candidate: _Candidate, context: PluginContext | None = None) -> LoadedPlugin:
    try:
        module_name, attribute = _entrypoint_fields(candidate.manifest.entrypoint) or ("", "")
        module = (
            importlib.import_module(module_name)
            if candidate.builtin
            else _load_external_module(candidate.directory, module_name)
        )
        entrypoint = getattr(module, attribute)
        implementation = _instantiate(entrypoint, context) if callable(entrypoint) else entrypoint
        _validate_implementation(candidate.manifest, implementation)
    except Exception as error:
        raise PluginDiscoveryError(
            f"failed to load {candidate.manifest.plugin_type} plug-in {candidate.manifest.kind} "
            f"from {candidate.manifest_path}: {type(error).__name__}: {error}"
        ) from error
    return LoadedPlugin(
        manifest=candidate.manifest,
        implementation=implementation,
        origin=candidate.directory,
        builtin=candidate.builtin,
    )


def _instantiate(entrypoint: object, context: PluginContext | None) -> object:
    signature = inspect.signature(entrypoint)
    positional = [
        parameter
        for parameter in signature.parameters.values()
        if parameter.kind in {parameter.POSITIONAL_ONLY, parameter.POSITIONAL_OR_KEYWORD}
    ]
    if context is not None and positional:
        return entrypoint(context)  # type: ignore[operator]
    return entrypoint()  # type: ignore[operator]


def _load_external_module(directory: Path, module_name: str) -> ModuleType:
    digest = hashlib.sha256(str(directory).encode()).hexdigest()[:20]
    namespace = f"_mailarchiver_plugin_{digest}"
    if namespace not in sys.modules:
        package = ModuleType(namespace)
        package.__package__ = namespace
        package.__path__ = [str(directory)]  # type: ignore[attr-defined]
        sys.modules[namespace] = package
    full_name = f"{namespace}.{module_name}"
    existing = sys.modules.get(full_name)
    if existing is not None:
        return existing
    parts = module_name.split(".")
    module_path, package_path = _external_module_paths(directory, parts)
    is_package = package_path.is_file()
    path = package_path if is_package else module_path
    spec = importlib.util.spec_from_file_location(
        full_name,
        path,
        submodule_search_locations=[str(path.parent)] if is_package else None,
    )
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot create import specification for {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[full_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        sys.modules.pop(full_name, None)
        raise
    return module


def _validate_implementation(manifest: PluginManifest, implementation: object) -> None:
    kind = getattr(implementation, "kind", None)
    if kind != manifest.kind:
        raise TypeError(f"entrypoint returned kind {kind!r}; expected {manifest.kind!r}")
    methods = ("recognizes", "messages") if manifest.plugin_type == "file" else ("recognizes", "discover", "messages")
    missing = [name for name in methods if not callable(getattr(implementation, name, None))]
    if manifest.plugin_type == "source":
        if not isinstance(getattr(implementation, "capabilities", None), PluginCapabilities):
            missing.append("capabilities")
        controls = getattr(implementation, "integrity_controls", None)
        if controls is None or not all(callable(getattr(controls, name, None)) for name in ("plan", "complete")):
            missing.append("integrity_controls.plan/complete")
        if not isinstance(getattr(controls, "control_id", None), str) or not controls.control_id.strip():
            missing.append("integrity_controls.control_id")
    if missing:
        raise TypeError("entrypoint implementation lacks or has invalid " + ", ".join(missing))
