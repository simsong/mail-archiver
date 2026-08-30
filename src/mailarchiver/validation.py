"""Acquire public validation corpora and build independently verified Mailbags."""

from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import mailbox
import os
import re
import shutil
import stat
import subprocess
import sys
import tarfile
import tomllib
import urllib.parse
import urllib.request
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path, PurePosixPath
from typing import Iterator, Literal

import py7zr
from pydantic import BaseModel, ConfigDict, Field, model_validator


CONFIG_SCHEMA_VERSION = 1
USER_AGENT = "mailarchiver-validation/0.1"
SOURCE_MANIFEST = "source-manifest.json"
RUN_REPORT = "run-report.json"
DEFAULT_CONFIG_DIR = Path("validation/datasets")
DEFAULT_DATA_DIR = Path("data")


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid")


class SourceConfig(StrictModel):
    """One downloadable artifact, directory listing, or public-inbox repository."""

    kind: Literal["http", "git"]
    url: str
    filename: str | None = None
    archive: Literal["none", "tar", "zip", "7z", "gzip"] = "none"
    sha256: str | None = None
    ref: str = "HEAD"

    @model_validator(mode="after")
    def validate_kind_fields(self) -> "SourceConfig":
        if self.kind == "http" and not self.filename:
            self.filename = PurePosixPath(urllib.parse.urlparse(self.url).path).name
        if self.kind == "git" and self.archive != "none":
            raise ValueError("git source cannot declare an archive format")
        if self.sha256 is not None and not re.fullmatch(r"[0-9a-f]{64}", self.sha256):
            raise ValueError("sha256 must contain 64 lowercase hexadecimal characters")
        return self


class PreprocessConfig(StrictModel):
    """Dataset-specific conversion into files the normal ingest CLI recognizes."""

    kind: Literal["messages", "mailboxes", "mixed", "sf-lovers", "public-inbox"]
    include: list[str] = Field(default_factory=lambda: ["**/*"])
    exclude: list[str] = Field(default_factory=list)


class AwsDatasetConfig(StrictModel):
    instance_type: str = "m7i.large"
    volume_size_gib: int = Field(default=100, ge=20, le=16384)


class DatasetConfig(StrictModel):
    """Versioned definition of one public validation dataset."""

    schema_version: int
    id: str
    title: str
    homepage: str
    enabled: bool = True
    availability_note: str | None = None
    expected_messages: int | None = Field(default=None, ge=0)
    max_unpacked_bytes: int = Field(default=100 * 1024**3, ge=1)
    sources: list[SourceConfig]
    preprocess: PreprocessConfig
    aws: AwsDatasetConfig = Field(default_factory=AwsDatasetConfig)

    @model_validator(mode="after")
    def validate_dataset(self) -> "DatasetConfig":
        if self.schema_version != CONFIG_SCHEMA_VERSION:
            raise ValueError(f"unsupported dataset schema version: {self.schema_version}")
        if not re.fullmatch(r"[a-z0-9][a-z0-9-]*", self.id):
            raise ValueError("dataset id must be lowercase letters, digits, and hyphens")
        if not self.sources:
            raise ValueError("dataset must contain at least one source")
        return self


class AcquiredArtifact(StrictModel):
    source_kind: Literal["http", "git"]
    url: str
    path: str
    archive: Literal["none", "tar", "zip", "7z", "gzip"]
    byte_length: int | None = None
    sha256: str | None = None
    git_commit: str | None = None


class SourceManifest(StrictModel):
    schema_version: int = CONFIG_SCHEMA_VERSION
    dataset_id: str
    acquired_at: str
    artifacts: list[AcquiredArtifact]


class RunReport(StrictModel):
    schema_version: int = CONFIG_SCHEMA_VERSION
    dataset_id: str
    completed_at: str
    source_manifest_sha256: str
    prepared_files: int
    mailbag: str
    result_zip: str
    result_sha256: str


class LaunchRequest(StrictModel):
    dataset_id: str
    run_id: str
    instance_type: str
    volume_size_gib: int


class LaunchResponse(StrictModel):
    dataset_id: str
    run_id: str
    instance_id: str
    output_prefix: str


class DataLayout(StrictModel):
    root: Path

    def downloads(self, dataset_id: str) -> Path:
        return self.root / "downloads" / dataset_id

    def extracted(self, dataset_id: str) -> Path:
        return self.root / "extracted" / dataset_id

    def prepared(self, dataset_id: str) -> Path:
        return self.root / "prepared" / dataset_id

    def mailbag(self, dataset_id: str) -> Path:
        return self.root / "mailbags" / dataset_id

    def result(self, dataset_id: str) -> Path:
        return self.root / "results" / f"{dataset_id}.mailbag.zip"

    def run_dir(self) -> Path:
        return self.root / "runs"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as source:
        for block in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def atomic_json(path: Path, model: StrictModel) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(model.model_dump_json(indent=2) + "\n", encoding="utf-8")
    os.replace(temporary, path)


def load_dataset(path: Path) -> DatasetConfig:
    with path.open("rb") as handle:
        return DatasetConfig.model_validate(tomllib.load(handle))


def load_datasets(config_dir: Path) -> dict[str, DatasetConfig]:
    datasets: dict[str, DatasetConfig] = {}
    for path in sorted(config_dir.glob("*.toml")):
        dataset = load_dataset(path)
        if dataset.id in datasets:
            raise ValueError(f"duplicate dataset id: {dataset.id}")
        datasets[dataset.id] = dataset
    if not datasets:
        raise ValueError(f"no dataset configurations found in {config_dir}")
    return datasets


def require_dataset(config_dir: Path, dataset_id: str) -> DatasetConfig:
    datasets = load_datasets(config_dir)
    try:
        return datasets[dataset_id]
    except KeyError as error:
        raise ValueError(f"unknown dataset {dataset_id}; choose one of {', '.join(datasets)}") from error


def download(url: str, destination: Path, expected_sha256: str | None = None) -> AcquiredArtifact:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        digest = sha256_file(destination)
    else:
        temporary = destination.with_name(f".{destination.name}.part")
        digest_object = hashlib.sha256()
        request = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
        try:
            with urllib.request.urlopen(request, timeout=60) as response, temporary.open("wb") as output:
                while block := response.read(1024 * 1024):
                    output.write(block)
                    digest_object.update(block)
                output.flush()
                os.fsync(output.fileno())
            digest = digest_object.hexdigest()
            if expected_sha256 is not None and digest != expected_sha256:
                raise ValueError(f"SHA-256 mismatch for {url}: expected {expected_sha256}, received {digest}")
            os.replace(temporary, destination)
        finally:
            temporary.unlink(missing_ok=True)
    if expected_sha256 is not None and digest != expected_sha256:
        raise ValueError(f"SHA-256 mismatch for cached {destination}: expected {expected_sha256}, received {digest}")
    return AcquiredArtifact(
        source_kind="http",
        url=url,
        path=str(destination),
        archive="none",
        byte_length=destination.stat().st_size,
        sha256=digest,
    )


def acquire_git(source: SourceConfig, destination: Path) -> AcquiredArtifact:
    if not destination.exists():
        destination.parent.mkdir(parents=True, exist_ok=True)
        subprocess.run(["git", "clone", "--mirror", source.url, str(destination)], check=True)
    commit = subprocess.run(
        ["git", "--git-dir", str(destination), "rev-parse", source.ref],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()
    return AcquiredArtifact(
        source_kind="git", url=source.url, path=str(destination), archive="none", git_commit=commit
    )


def acquire(dataset: DatasetConfig, layout: DataLayout) -> SourceManifest:
    target = layout.downloads(dataset.id)
    artifacts: list[AcquiredArtifact] = []
    for number, source in enumerate(dataset.sources, 1):
        if source.kind == "git":
            artifact = acquire_git(source, target / f"{number:02d}.git")
            artifacts.append(artifact)
            continue
        name = source.filename
        if not name or PurePosixPath(name).name != name:
            raise ValueError(f"unsafe or missing download filename: {name!r}")
        destination = target / f"{number:02d}" / name
        artifact = download(source.url, destination, source.sha256)
        artifact.archive = source.archive
        artifacts.append(artifact)
    manifest = SourceManifest(dataset_id=dataset.id, acquired_at=utc_now(), artifacts=artifacts)
    atomic_json(target / SOURCE_MANIFEST, manifest)
    return manifest


def safe_relative(value: str) -> Path:
    if not value or "\\" in value or "\0" in value:
        raise ValueError(f"unsafe archive member: {value}")
    path = PurePosixPath(value)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"unsafe archive member: {value}")
    return Path(*path.parts)


def extract_tar(source: Path, destination: Path, maximum: int) -> int:
    with tarfile.open(source, "r:*") as archive:
        members = archive.getmembers()
        total = 0
        for member in members:
            safe_relative(member.name)
            if not (member.isfile() or member.isdir()):
                raise ValueError(f"unsupported tar member: {member.name}")
            total += member.size
            if total > maximum:
                raise ValueError(f"tar expands beyond configured limit: {source}")
        archive.extractall(destination, members=members, filter="data")
    return total


def extract_zip(source: Path, destination: Path, maximum: int) -> int:
    with zipfile.ZipFile(source) as archive:
        total = 0
        for member in archive.infolist():
            safe_relative(member.filename.rstrip("/"))
            file_type = stat.S_IFMT(member.external_attr >> 16)
            if file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                raise ValueError(f"unsupported ZIP member: {member.filename}")
            total += member.file_size
            if total > maximum:
                raise ValueError(f"ZIP expands beyond configured limit: {source}")
        archive.extractall(destination)
    return total


def extract_7z(source: Path, destination: Path, maximum: int) -> int:
    with py7zr.SevenZipFile(source, mode="r") as archive:
        total = 0
        for member in archive.list():
            safe_relative(member.filename)
            if member.is_symlink or not (member.is_file or member.is_directory):
                raise ValueError(f"unsupported 7z member: {member.filename}")
            total += member.uncompressed
            if total > maximum:
                raise ValueError(f"7z expands beyond configured limit: {source}")
        archive.extractall(destination)
    return total


def extract_gzip(source: Path, destination: Path, maximum: int) -> int:
    output = destination / source.name.removesuffix(".gz")
    total = 0
    try:
        with gzip.open(source, "rb") as compressed, output.open("wb") as extracted:
            while block := compressed.read(1024 * 1024):
                total += len(block)
                if total > maximum:
                    raise ValueError(f"gzip expands beyond configured limit: {source}")
                extracted.write(block)
    except BaseException:
        output.unlink(missing_ok=True)
        raise
    return total


def tree_bytes(root: Path) -> int:
    return sum(path.stat().st_size for path in root.rglob("*") if path.is_file() and path.name != ".complete")


def extract(dataset: DatasetConfig, manifest: SourceManifest, layout: DataLayout) -> list[Path]:
    root = layout.extracted(dataset.id)
    root.mkdir(parents=True, exist_ok=True)
    inputs: list[Path] = []
    remaining = dataset.max_unpacked_bytes
    for number, artifact in enumerate(manifest.artifacts, 1):
        source = Path(artifact.path)
        if artifact.source_kind == "git":
            inputs.append(source)
            continue
        if artifact.archive == "none":
            inputs.append(source)
            continue
        destination = root / f"{number:04d}"
        marker = destination / ".complete"
        if not marker.exists():
            if destination.exists():
                shutil.rmtree(destination)
            destination.mkdir(parents=True)
            if artifact.archive == "tar":
                expanded = extract_tar(source, destination, remaining)
            elif artifact.archive == "zip":
                expanded = extract_zip(source, destination, remaining)
            elif artifact.archive == "7z":
                expanded = extract_7z(source, destination, remaining)
            else:
                expanded = extract_gzip(source, destination, remaining)
            marker.touch()
        else:
            expanded = tree_bytes(destination)
        if expanded > remaining:
            raise ValueError(f"dataset expands beyond configured limit: {dataset.id}")
        remaining -= expanded
        inputs.append(destination)
    return inputs


def selected(path: Path, base: Path, config: PreprocessConfig) -> bool:
    relative = path.relative_to(base)
    return any(relative.match(pattern) or path.name == pattern for pattern in config.include) and not any(
        relative.match(pattern) for pattern in config.exclude
    )


def candidate_files(inputs: list[Path], config: PreprocessConfig) -> Iterator[tuple[Path, Path]]:
    for base in inputs:
        if base.name.endswith(".git"):
            continue
        paths = [base] if base.is_file() else base.rglob("*")
        for path in paths:
            if path.is_file() and path.name != ".complete" and selected(path, base if base.is_dir() else base.parent, config):
                yield base, path


def header_like(raw: bytes) -> bool:
    head = raw[:65536]
    return bool(re.search(br"(?mi)^(date|from|to|subject|message-id|received):[ \t]*", head))


def read_head(path: Path) -> bytes:
    with path.open("rb") as handle:
        return handle.read(65536)


def link_or_copy(source: Path, destination: Path) -> None:
    destination.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)


def prepared_destination(root: Path, ordinal: int, path: Path, suffix: str) -> Path:
    safe_name = re.sub(r"[^A-Za-z0-9._-]", "_", path.name)[:120]
    return root / f"{ordinal:08d}-{safe_name}{suffix}"


def write_babyl_messages(source: Path, prepared: Path, start: int) -> int:
    box = mailbox.Babyl(source, factory=None, create=False)
    count = start
    try:
        for key in box.iterkeys():
            raw = box.get_message(key).as_bytes(unixfrom=False)
            destination = prepared / f"{count:08d}-{source.name}.eml"
            destination.write_bytes(raw)
            count += 1
    finally:
        box.close()
    return count


def write_mbox_messages(source: Path, prepared: Path, start: int) -> int:
    """Convert one envelope-wrapped source file into derived RFC 5322 files."""

    box = mailbox.mbox(source, factory=None, create=False)
    count = start
    try:
        for key in box.iterkeys():
            destination = prepared_destination(prepared, count, source, ".eml")
            destination.write_bytes(box.get_bytes(key, from_=False))
            count += 1
    finally:
        box.close()
    if count == start:
        raise ValueError(f"MBOX source contains no messages: {source}")
    return count


def git_blob_ids(repository: Path) -> list[str]:
    object_lines = subprocess.run(
        [
            "git",
            "--git-dir",
            str(repository),
            "cat-file",
            "--batch-all-objects",
            "--batch-check=%(objectname) %(objecttype)",
        ],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.splitlines()
    return [line.split(" ", 1)[0] for line in object_lines if line.endswith(" blob")]


def prepare_public_inbox(repository: Path, prepared: Path, start: int) -> int:
    count = start
    with subprocess.Popen(
        ["git", "--git-dir", str(repository), "cat-file", "--batch"],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
    ) as process:
        assert process.stdin is not None and process.stdout is not None
        for object_id in git_blob_ids(repository):
            process.stdin.write(f"{object_id}\n".encode())
            process.stdin.flush()
            response = process.stdout.readline().decode("ascii").split()
            if len(response) != 3 or response[1] != "blob":
                raise ValueError(f"unexpected git cat-file response for {object_id}: {response}")
            raw = process.stdout.read(int(response[2]))
            if process.stdout.read(1) != b"\n":
                raise ValueError(f"unterminated git cat-file response for {object_id}")
            if header_like(raw):
                (prepared / f"{count:08d}-{object_id}.eml").write_bytes(raw)
                count += 1
        process.stdin.close()
        if process.wait() != 0:
            raise subprocess.CalledProcessError(process.returncode, process.args)
    return count


def prepare(dataset: DatasetConfig, inputs: list[Path], layout: DataLayout) -> int:
    target = layout.prepared(dataset.id)
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True)
    count = 0
    if dataset.preprocess.kind == "public-inbox":
        for source in inputs:
            if source.name.endswith(".git"):
                count = prepare_public_inbox(source, target, count)
        return validate_prepared_count(dataset, count)
    for _base, path in candidate_files(inputs, dataset.preprocess):
        head = read_head(path)
        if dataset.preprocess.kind == "sf-lovers" and head.startswith(b"BABYL OPTIONS:"):
            count = write_babyl_messages(path, target, count)
            continue
        is_mbox = head.startswith(b"From ")
        if dataset.preprocess.kind == "messages" and is_mbox:
            count = write_mbox_messages(path, target, count)
            continue
        if dataset.preprocess.kind == "mailboxes" and not is_mbox:
            continue
        if dataset.preprocess.kind in {"mixed", "sf-lovers"} and not (is_mbox or header_like(head)):
            continue
        suffix = ".mbox" if is_mbox else ".eml"
        link_or_copy(path, prepared_destination(target, count, path, suffix))
        count += 1
    return validate_prepared_count(dataset, count)


def validate_prepared_count(dataset: DatasetConfig, count: int) -> int:
    if count == 0:
        raise ValueError(f"preprocessing produced no ingestible files for {dataset.id}")
    if dataset.expected_messages is not None and count != dataset.expected_messages:
        raise ValueError(
            f"preprocessing produced {count} files for {dataset.id}; expected {dataset.expected_messages}"
        )
    return count


def run_process(command: list[str]) -> None:
    print("+", " ".join(command), flush=True)
    subprocess.run(command, check=True)


def create_result_zip(mailbag: Path, result: Path) -> str:
    result.parent.mkdir(parents=True, exist_ok=True)
    temporary = result.with_name(f".{result.name}.tmp")
    try:
        with zipfile.ZipFile(temporary, "w", compression=zipfile.ZIP_DEFLATED, allowZip64=True) as archive:
            for path in sorted(mailbag.rglob("*")):
                if path.is_symlink():
                    raise ValueError(f"Mailbag ZIP cannot contain symlink: {path}")
                if path.is_file():
                    archive.write(path, (Path(mailbag.name) / path.relative_to(mailbag)).as_posix())
        os.replace(temporary, result)
    finally:
        temporary.unlink(missing_ok=True)
    return sha256_file(result)


def run_dataset(dataset: DatasetConfig, layout: DataLayout, owner_names: Path) -> RunReport:
    if not dataset.enabled:
        raise ValueError(f"dataset {dataset.id} is disabled: {dataset.availability_note or 'no reason recorded'}")
    layout.result(dataset.id).unlink(missing_ok=True)
    (layout.run_dir() / f"{dataset.id}.json").unlink(missing_ok=True)
    manifest = acquire(dataset, layout)
    inputs = extract(dataset, manifest, layout)
    prepared_files = prepare(dataset, inputs, layout)
    mailbag = layout.mailbag(dataset.id)
    if mailbag.exists():
        shutil.rmtree(mailbag)
    run_process(
        [
            sys.executable,
            "-m",
            "mailarchiver",
            "--archive",
            str(mailbag),
            "ingest",
            "--owner-names-file",
            str(owner_names),
            "--clamav",
            str(layout.prepared(dataset.id)),
        ]
    )
    verifier = mailbag / "verify_mail_archive.py"
    run_process([sys.executable, "-I", str(verifier), str(mailbag)])
    result = layout.result(dataset.id)
    result_sha256 = create_result_zip(mailbag, result)
    source_manifest_path = layout.downloads(dataset.id) / SOURCE_MANIFEST
    report = RunReport(
        dataset_id=dataset.id,
        completed_at=utc_now(),
        source_manifest_sha256=sha256_file(source_manifest_path),
        prepared_files=prepared_files,
        mailbag=str(mailbag),
        result_zip=str(result),
        result_sha256=result_sha256,
    )
    atomic_json(layout.run_dir() / f"{dataset.id}.json", report)
    return report


def aws_output(stack: str, key: str) -> str:
    query = f"Stacks[0].Outputs[?OutputKey=='{key}'].OutputValue | [0]"
    return subprocess.run(
        ["aws", "cloudformation", "describe-stacks", "--stack-name", stack, "--query", query, "--output", "text"],
        check=True,
        capture_output=True,
        text=True,
    ).stdout.strip()


def aws_start(dataset: DatasetConfig, layout: DataLayout, stack: str) -> LaunchResponse:
    function_name = aws_output(stack, "LauncherFunctionName")
    request = LaunchRequest(
        dataset_id=dataset.id,
        run_id=datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + "-" + uuid.uuid4().hex[:8],
        instance_type=dataset.aws.instance_type,
        volume_size_gib=dataset.aws.volume_size_gib,
    )
    layout.run_dir().mkdir(parents=True, exist_ok=True)
    response_path = layout.run_dir() / f"{request.run_id}.launch.json"
    metadata = subprocess.run(
        [
            "aws",
            "lambda",
            "invoke",
            "--function-name",
            function_name,
            "--cli-binary-format",
            "raw-in-base64-out",
            "--payload",
            request.model_dump_json(),
            str(response_path),
        ],
        check=True,
        capture_output=True,
        text=True,
    )
    invocation = json.loads(metadata.stdout)
    if invocation.get("FunctionError"):
        raise RuntimeError(response_path.read_text(encoding="utf-8"))
    return LaunchResponse.model_validate_json(response_path.read_text(encoding="utf-8"))


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config-dir", type=Path, default=DEFAULT_CONFIG_DIR)
    parser.add_argument("--data-dir", type=Path, default=DEFAULT_DATA_DIR)
    parser.add_argument("--owner-names-file", type=Path, default=Path("owner-names.txt"))
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("list")
    for command in ("fetch", "prepare", "run"):
        subparser = commands.add_parser(command)
        subparser.add_argument("dataset")
    commands.add_parser("run-all")
    aws_parser = commands.add_parser("aws-start")
    aws_parser.add_argument("dataset")
    aws_parser.add_argument("--stack", required=True)
    aws_all_parser = commands.add_parser("aws-start-all")
    aws_all_parser.add_argument("--stack", required=True)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    layout = DataLayout(root=args.data_dir.resolve())
    try:
        if args.command == "list":
            for dataset in load_datasets(args.config_dir).values():
                state = "enabled" if dataset.enabled else "disabled"
                print(f"{dataset.id:20} {state:8} {dataset.title}")
            return 0
        if args.command == "run-all":
            for dataset in load_datasets(args.config_dir).values():
                if dataset.enabled:
                    run_dataset(dataset, layout, args.owner_names_file.resolve())
            return 0
        if args.command == "aws-start-all":
            for dataset in load_datasets(args.config_dir).values():
                if dataset.enabled:
                    print(aws_start(dataset, layout, args.stack).model_dump_json())
            return 0
        dataset = require_dataset(args.config_dir, args.dataset)
        if args.command == "fetch":
            print(acquire(dataset, layout).model_dump_json(indent=2))
        elif args.command == "prepare":
            manifest = acquire(dataset, layout)
            print(f"prepared {prepare(dataset, extract(dataset, manifest, layout), layout):,} files")
        elif args.command == "run":
            print(run_dataset(dataset, layout, args.owner_names_file.resolve()).model_dump_json(indent=2))
        else:
            print(aws_start(dataset, layout, args.stack).model_dump_json(indent=2))
        return 0
    except (OSError, ValueError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"validation failed: {error}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
