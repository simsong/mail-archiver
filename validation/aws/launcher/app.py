"""SAM launcher for one self-terminating validation EC2 worker."""

from __future__ import annotations

import base64
import os
import shlex
import textwrap
from typing import Any

from pydantic import BaseModel, ConfigDict, Field


DATASET_ID = "dataset_id"
RUN_ID = "run_id"
INSTANCE_TYPE = "instance_type"
VOLUME_SIZE_GIB = "volume_size_gib"
INSTANCE_ID = "instance_id"
OUTPUT_PREFIX = "output_prefix"


class StrictModel(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)


class LaunchRequest(StrictModel):
    dataset_id: str = Field(pattern=r"^[a-z0-9][a-z0-9-]*$")
    run_id: str = Field(pattern=r"^[0-9TZ-]+-[0-9a-f]{8}$")
    instance_type: str = Field(pattern=r"^[a-z0-9][a-z0-9.-]*$")
    volume_size_gib: int = Field(ge=20, le=16384)


class LaunchResponse(StrictModel):
    dataset_id: str
    run_id: str
    instance_id: str
    output_prefix: str


def parse_request(event: dict[str, Any]) -> LaunchRequest:
    """Strictly validate the external Lambda event before constructing user data."""

    return LaunchRequest.model_validate(event)


def required_environment(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        raise RuntimeError(f"missing environment variable {name}")
    return value


def worker_script(request: LaunchRequest, bucket: str, prefix: str, repository: str, ref: str) -> str:
    """Create a fail-closed Ubuntu worker script using only validated/quoted values."""

    values = {
        "bucket": shlex.quote(bucket),
        "dataset": shlex.quote(request.dataset_id),
        "prefix": shlex.quote(prefix.strip("/")),
        "repository": shlex.quote(repository),
        "ref": shlex.quote(ref),
        "run_id": shlex.quote(request.run_id),
    }
    return textwrap.dedent(
        f"""\
        #!/bin/bash
        set -Eeuo pipefail
        exec > >(tee /var/log/mailarchiver-validation.log) 2>&1
        bucket={values['bucket']}
        prefix={values['prefix']}
        run_id={values['run_id']}
        dataset={values['dataset']}
        destination="s3://${{bucket}}/${{prefix}}/${{run_id}}/${{dataset}}"
        status=failed
        finish() {{
          set +e
          printf '{{"dataset_id":"%s","run_id":"%s","status":"%s"}}\\n' "$dataset" "$run_id" "$status" >/tmp/status.json
          command -v aws >/dev/null && aws s3 cp /tmp/status.json "$destination/status.json"
          command -v aws >/dev/null && aws s3 cp /var/log/mailarchiver-validation.log "$destination/worker.log"
          shutdown -h now
        }}
        trap finish EXIT

        export DEBIAN_FRONTEND=noninteractive
        apt-get update
        apt-get install -y awscli clamav clamav-daemon curl git make
        systemctl stop clamav-daemon clamav-freshclam || true
        freshclam || compgen -G '/var/lib/clamav/*.c[lv]d' >/dev/null
        curl -LsSf https://astral.sh/uv/install.sh | env UV_INSTALL_DIR=/usr/local/bin sh

        install -d -m 0755 /opt/mailarchiver /var/lib/mailarchiver-validation /run/clamav /var/log/clamav
        git -C /opt/mailarchiver init repository
        git -C /opt/mailarchiver/repository remote add origin {values['repository']}
        git -C /opt/mailarchiver/repository fetch --depth=1 origin {values['ref']}
        git -C /opt/mailarchiver/repository checkout --detach FETCH_HEAD
        cd /opt/mailarchiver/repository
        uv sync --frozen --no-dev

        cat >/etc/clamav/mailarchiver-clamd.conf <<'EOF'
        DatabaseDirectory /var/lib/clamav
        LocalSocket /run/clamav/mailarchiver-validation.sock
        FixStaleSocket yes
        PidFile /run/clamav/mailarchiver-validation.pid
        LogFile /var/log/clamav/mailarchiver-validation.log
        LogTime yes
        User root
        ScanMail yes
        EOF
        export MAILARCHIVER_CLAMD=/usr/sbin/clamd
        export MAILARCHIVER_CLAMDSCAN=/usr/bin/clamdscan
        export MAILARCHIVER_CLAMD_CONFIG=/etc/clamav/mailarchiver-clamd.conf
        export MAILARCHIVER_CLAMD_SOCKET=/run/clamav/mailarchiver-validation.sock

        if make validation-run DATASET="${{dataset}}" VALIDATION_DATA_DIR=/var/lib/mailarchiver-validation/data; then
          aws s3 cp "/var/lib/mailarchiver-validation/data/runs/${{dataset}}.json" "$destination/run-report.json"
          aws s3 cp "/var/lib/mailarchiver-validation/data/results/${{dataset}}.mailbag.zip" "$destination/${{dataset}}.mailbag.zip"
          status=succeeded
        fi
        test "$status" = succeeded
        """
    )


def lambda_handler(event: dict[str, Any], _context: object) -> dict[str, str]:
    """Launch a worker and return its instance ID and deterministic S3 prefix."""

    request = parse_request(event)
    ami_id = required_environment("AMI_ID")
    bucket = required_environment("OUTPUT_BUCKET")
    prefix = required_environment("OUTPUT_PREFIX")
    repository = required_environment("REPOSITORY_URL")
    ref = required_environment("REPOSITORY_REF")
    script = worker_script(request, bucket, prefix, repository, ref)

    import boto3  # Lambda runtime dependency; delayed so pure validation tests stay local.

    ec2 = boto3.client("ec2")
    response = ec2.run_instances(
        ImageId=ami_id,
        InstanceType=request.instance_type,
        MinCount=1,
        MaxCount=1,
        IamInstanceProfile={"Arn": required_environment("INSTANCE_PROFILE_ARN")},
        InstanceInitiatedShutdownBehavior="terminate",
        MetadataOptions={"HttpEndpoint": "enabled", "HttpTokens": "required", "HttpPutResponseHopLimit": 1},
        NetworkInterfaces=[
            {
                "AssociatePublicIpAddress": True,
                "DeleteOnTermination": True,
                "DeviceIndex": 0,
                "Groups": [required_environment("SECURITY_GROUP_ID")],
                "SubnetId": required_environment("SUBNET_ID"),
            }
        ],
        BlockDeviceMappings=[
            {
                "DeviceName": "/dev/sda1",
                "Ebs": {
                    "DeleteOnTermination": True,
                    "Encrypted": True,
                    "VolumeSize": request.volume_size_gib,
                    "VolumeType": "gp3",
                },
            }
        ],
        TagSpecifications=[
            {
                "ResourceType": "instance",
                "Tags": [
                    {"Key": "Name", "Value": f"mailarchiver-validation-{request.dataset_id}"},
                    {"Key": "mailarchiver:dataset", "Value": request.dataset_id},
                    {"Key": "mailarchiver:run-id", "Value": request.run_id},
                ],
            },
            {
                "ResourceType": "volume",
                "Tags": [
                    {"Key": "mailarchiver:dataset", "Value": request.dataset_id},
                    {"Key": "mailarchiver:run-id", "Value": request.run_id},
                ],
            },
        ],
        UserData=base64.b64encode(script.encode()).decode(),
    )
    instance_id = response["Instances"][0]["InstanceId"]
    output_prefix = f"s3://{bucket}/{prefix.strip('/')}/{request.run_id}/{request.dataset_id}/"
    return LaunchResponse(
        dataset_id=request.dataset_id,
        run_id=request.run_id,
        instance_id=instance_id,
        output_prefix=output_prefix,
    ).model_dump()
