"""Requirements: each AWS validation request launches one terminating, report-uploading worker."""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest


LAUNCHER_PATH = Path(__file__).parents[1] / "validation" / "aws" / "launcher" / "app.py"
SPEC = importlib.util.spec_from_file_location("validation_launcher", LAUNCHER_PATH)
assert SPEC is not None and SPEC.loader is not None
launcher = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = launcher
SPEC.loader.exec_module(launcher)


def request() -> dict[str, object]:
    return {
        launcher.DATASET_ID: "sf-lovers",
        launcher.RUN_ID: "20260829T120000Z-a1b2c3d4",
        launcher.INSTANCE_TYPE: "m7i.large",
        launcher.VOLUME_SIZE_GIB: 50,
    }


def test_launcher_rejects_extra_fields_and_shell_metacharacters() -> None:
    extra = request() | {"surprise": True}
    with pytest.raises(ValueError, match="Extra inputs"):
        launcher.parse_request(extra)

    malicious = request() | {launcher.DATASET_ID: "enron; shutdown now"}
    with pytest.raises(ValueError, match="dataset_id"):
        launcher.parse_request(malicious)


def test_worker_script_runs_pipeline_uploads_evidence_and_always_shuts_down() -> None:
    parsed = launcher.parse_request(request())
    script = launcher.worker_script(
        parsed,
        "validation-bucket",
        "mailarchiver-validation",
        "https://github.com/simsong/mail-archiver.git",
        "codex/validation-datasets",
    )

    assert script.startswith("#!/bin/bash\nset -Eeuo pipefail")
    assert 'make validation-run DATASET="${dataset}"' in script
    assert "mailbag.zip" in script
    assert "run-report.json" in script
    assert "status.json" in script
    assert "trap finish EXIT" in script
    assert "shutdown -h now" in script
    assert "sshd" not in script
    syntax = subprocess.run(["bash", "-n"], input=script, text=True, capture_output=True, check=False)
    assert syntax.returncode == 0, syntax.stderr


def test_sam_control_plane_uses_real_terminating_ec2_and_external_bucket() -> None:
    """Requirement: SAM launches EC2 workers but does not own the result bucket."""
    template = (LAUNCHER_PATH.parents[2] / "template.yaml").read_text(encoding="utf-8")
    source = LAUNCHER_PATH.read_text(encoding="utf-8")

    assert "AWS::EC2::SecurityGroup" in template
    assert "AWS::IAM::InstanceProfile" in template
    assert "AWS::S3::Bucket" not in template
    assert 'InstanceInitiatedShutdownBehavior="terminate"' in source
    assert '"HttpTokens": "required"' in source
    assert '"Encrypted": True' in source
    assert '"DeleteOnTermination": True' in source
