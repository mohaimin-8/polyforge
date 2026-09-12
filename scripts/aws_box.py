"""Drive the B1 rented host on AWS EC2 through boto3, without a console.

The runbook (docs/WAVE4_RENTED_HOST_RUNBOOK.md) is vendor-neutral; this is the
vendor-specific hand for the box the session-48 decision named: g6e.4xlarge
(1x L40S 48 GB, 16 vCPU, 128 GiB, ~$3.00/h us-east-1), with g6e.8xlarge
(32 vCPU, ~$4.53/h) as the step-up if the concurrency preflight fails at 16.
Every billable action is its own explicit subcommand; nothing here retries a
launch on its own.

Credentials come from ~/.aws/credentials (boto3's default chain) and are never
printed. Region from --region, else $AWS_REGION, else us-east-1.

Usage (from the repo root):
    python scripts/aws_box.py quota                  # G-instance vCPU quota in the region (free)
    python scripts/aws_box.py quota-request 32       # ask for the increase (free)
    python scripts/aws_box.py ami                    # resolve the DL Base OSS NVIDIA GPU AMI (free)
    python scripts/aws_box.py key-add PUBFILE        # import the public key as key pair polyforge-b1 (free)
    python scripts/aws_box.py launch [--type g6e.4xlarge] [--disk 120] [--az us-east-1a]   # BILLABLE
    python scripts/aws_box.py status [ID]            # instances tagged polyforge-b1: state, ip
    python scripts/aws_box.py wait ID                # poll until running + status checks ok, print ip
    python scripts/aws_box.py terminate ID           # stop the meter; confirms the terminal state
    python scripts/aws_box.py budget 30 EMAIL        # monthly cost alert at 80%/100% (free)
"""

from __future__ import annotations

import argparse
import os
import sys
import time
from pathlib import Path

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

DEFAULT_REGION = "us-east-1"
DEFAULT_TYPE = "g6e.4xlarge"
STEP_UP_TYPE = "g6e.8xlarge"
DEFAULT_DISK_GIB = 120          # three models (~22 GB) + images + docker layers
KEY_NAME = "polyforge-b1"
SG_NAME = "polyforge-b1-ssh"
TAG = {"Key": "Name", "Value": "polyforge-b1"}
# "Running On-Demand G and VT instances" -- the quota is in vCPUs; a fresh
# account has 0 and g6e.4xlarge needs 16, g6e.8xlarge 32.
G_QUOTA_CODE = "L-DB2E81BA"
# Deep Learning Base OSS NVIDIA Driver GPU AMI: driver + CUDA + Docker +
# NVIDIA Container Toolkit preinstalled, Ubuntu 22.04 (Python 3.10, which
# every box-side script parses under -- runbook section 1).
AMI_PARAMS = (
    "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-22.04/latest/ami-id",
    "/aws/service/deeplearning/ami/x86_64/base-oss-nvidia-driver-gpu-ubuntu-24.04/latest/ami-id",
)
POLL_SECONDS = 15
WAIT_MAX_SECONDS = 15 * 60
TERMINAL = {"terminated", "shutting-down"}


def region_of(a) -> str:
    return getattr(a, "region", None) or os.environ.get("AWS_REGION") or DEFAULT_REGION


def client(service: str, region: str):
    return boto3.client(service, region_name=region)


def die(err: Exception, what: str) -> None:
    """The exception text is AWS's own message; credentials never appear in it."""
    if isinstance(err, NoCredentialsError):
        raise SystemExit("no AWS credentials: run `aws configure`-style setup by "
                         "writing ~/.aws/credentials ([default] aws_access_key_id / "
                         "aws_secret_access_key)")
    raise SystemExit(f"{what}: {err}")


def run_instances_params(ami: str, instance_type: str, sg_id: str, disk_gib: int,
                         key_name: str = KEY_NAME, az: str | None = None,
                         root_device: str = "/dev/sda1", subnet_id: str | None = None) -> dict:
    """The exact RunInstances request. Shutdown from inside the box terminates
    it (never a stopped instance quietly billing its EBS); the root volume is
    gp3 and deleted with the instance; one instance, ever. `root_device` must
    be the AMI's own RootDeviceName, or the mapping attaches a SECOND volume
    instead of enlarging the root (see cmd_launch)."""
    params = {
        "ImageId": ami, "InstanceType": instance_type, "KeyName": key_name,
        "MinCount": 1, "MaxCount": 1, "SecurityGroupIds": [sg_id],
        "InstanceInitiatedShutdownBehavior": "terminate",
        "BlockDeviceMappings": [{"DeviceName": root_device, "Ebs": {
            "VolumeSize": disk_gib, "VolumeType": "gp3", "DeleteOnTermination": True}}],
        "TagSpecifications": [{"ResourceType": "instance", "Tags": [TAG]},
                              {"ResourceType": "volume", "Tags": [TAG]}],
        "MetadataOptions": {"HttpTokens": "required"},
    }
    if az:
        params["Placement"] = {"AvailabilityZone": az}
    if subnet_id:
        params["SubnetId"] = subnet_id
    return params


def ssh_ingress() -> list[dict]:
    return [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
             "IpRanges": [{"CidrIp": "0.0.0.0/0", "Description": "ssh, key auth only"}]}]


def instance_row(inst: dict) -> str:
    state = inst["State"]["Name"]
    ip = inst.get("PublicIpAddress") or "-"
    return (f"{inst['InstanceId']}  {state:<13} {inst['InstanceType']:<12} "
            f"{inst.get('Placement', {}).get('AvailabilityZone', '?'):<12} ip={ip}")


def resolve_ami(ssm) -> tuple[str, str]:
    for name in AMI_PARAMS:
        try:
            return ssm.get_parameter(Name=name)["Parameter"]["Value"], name
        except ClientError as e:
            if e.response["Error"]["Code"] != "ParameterNotFound":
                raise
    raise SystemExit("no Deep Learning Base OSS NVIDIA GPU AMI parameter resolved; "
                     "check the AMI_PARAMS names against the DLAMI release notes")


def ensure_security_group(ec2) -> str:
    found = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]}])["SecurityGroups"]
    if found:
        return found[0]["GroupId"]
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpc:
        raise SystemExit("no default VPC in this region; create one in the console "
                         "(VPC -> Actions -> Create default VPC) and retry")
    sg = ec2.create_security_group(GroupName=SG_NAME, Description="polyforge B1 ssh",
                                   VpcId=vpc[0]["VpcId"])
    ec2.authorize_security_group_ingress(GroupId=sg["GroupId"], IpPermissions=ssh_ingress())
    return sg["GroupId"]


def offered_azs(ec2, instance_type: str) -> list[str]:
    """AZs in the region that offer the type. g6e is not in every AZ, and a
    launch without a subnet lands in an arbitrary one."""
    offs = ec2.describe_instance_type_offerings(
        LocationType="availability-zone",
        Filters=[{"Name": "instance-type", "Values": [instance_type]}])["InstanceTypeOfferings"]
    return sorted(o["Location"] for o in offs)


def pick_subnet(ec2, instance_type: str, az: str | None) -> tuple[str, str]:
    """(subnet_id, az): the default-VPC subnet in `az` if given, else in the
    first AZ that offers the type. Refuses with the offering list otherwise."""
    azs = offered_azs(ec2, instance_type)
    if az and az not in azs:
        raise SystemExit(f"{instance_type} is not offered in {az}; offered in {azs}")
    subs = ec2.describe_subnets(
        Filters=[{"Name": "default-for-az", "Values": ["true"]}])["Subnets"]
    by_az = {s["AvailabilityZone"]: s["SubnetId"] for s in subs}
    for cand in ([az] if az else azs):
        if cand in by_az:
            return by_az[cand], cand
    raise SystemExit(f"no default subnet in an AZ offering {instance_type}; "
                     f"offered in {azs}, default subnets in {sorted(by_az)}")


def cmd_quota(a) -> None:
    sq = client("service-quotas", region_of(a))
    try:
        q = sq.get_service_quota(ServiceCode="ec2", QuotaCode=G_QUOTA_CODE)["Quota"]
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "get quota")
    v = q["Value"]
    need = {DEFAULT_TYPE: 16, STEP_UP_TYPE: 32}
    print(f"{region_of(a)}  {q['QuotaName']}: {v:g} vCPUs")
    for t, n in need.items():
        print(f"  {t:<12} needs {n:>2}: {'OK' if v >= n else 'BLOCKED -- run: quota-request ' + str(n)}")
    pending = sq.list_requested_service_quota_change_history_by_quota(
        ServiceCode="ec2", QuotaCode=G_QUOTA_CODE)["RequestedQuotas"]
    for r in pending:
        print(f"  request {r['Id'][:8]} -> {r['DesiredValue']:g}: {r['Status']}")


def cmd_quota_request(a) -> None:
    sq = client("service-quotas", region_of(a))
    try:
        r = sq.request_service_quota_increase(ServiceCode="ec2", QuotaCode=G_QUOTA_CODE,
                                              DesiredValue=float(a.vcpus))["RequestedQuota"]
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "request quota")
    print(f"requested {a.vcpus} vCPUs: {r['Status']} (id {r['Id']}); "
          "AWS usually answers within hours, sometimes a day -- check with `quota`")


def cmd_ami(a) -> None:
    try:
        ami, name = resolve_ami(client("ssm", region_of(a)))
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "resolve ami")
    print(f"{ami}  <- {name}")


def cmd_key_add(a) -> None:
    pub = Path(a.pubfile).read_bytes().strip()
    ec2 = client("ec2", region_of(a))
    try:
        ec2.import_key_pair(KeyName=KEY_NAME, PublicKeyMaterial=pub)
        print(f"imported key pair {KEY_NAME}")
    except ClientError as e:
        if e.response["Error"]["Code"] == "InvalidKeyPair.Duplicate":
            print(f"key pair {KEY_NAME} already exists")
        else:
            die(e, "import key")
    except (BotoCoreError, NoCredentialsError) as e:
        die(e, "import key")


def cmd_launch(a) -> None:
    region = region_of(a)
    ec2 = client("ec2", region)
    try:
        ami, _ = resolve_ami(client("ssm", region))
        sg_id = ensure_security_group(ec2)
        root = ec2.describe_images(ImageIds=[ami])["Images"][0]["RootDeviceName"]
        subnet, az = pick_subnet(ec2, a.type, a.az)
        params = run_instances_params(ami, a.type, sg_id, a.disk, az=az,
                                      root_device=root, subnet_id=subnet)
        print(f"launching {a.type} in {az} ({subnet}) from {ami}, "
              f"{a.disk} GiB gp3 -- BILLING STARTS NOW", flush=True)
        inst = ec2.run_instances(**params)["Instances"][0]
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "launch")
    print("instance id:", inst["InstanceId"])


def _describe(ec2, ids: list[str] | None) -> list[dict]:
    kw = {"InstanceIds": ids} if ids else {"Filters": [{"Name": "tag:Name", "Values": [TAG["Value"]]}]}
    return [i for r in ec2.describe_instances(**kw)["Reservations"] for i in r["Instances"]]


def cmd_status(a) -> None:
    ec2 = client("ec2", region_of(a))
    try:
        rows = _describe(ec2, [a.id] if a.id else None)
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "describe")
    print("\n".join(instance_row(i) for i in rows) if rows else "no polyforge-b1 instances")


def cmd_wait(a) -> None:
    ec2 = client("ec2", region_of(a))
    deadline = time.time() + WAIT_MAX_SECONDS
    while True:
        try:
            inst = _describe(ec2, [a.id])[0]
            checks = ec2.describe_instance_status(InstanceIds=[a.id])["InstanceStatuses"]
        except (ClientError, BotoCoreError, NoCredentialsError) as e:
            die(e, "wait")
        state = inst["State"]["Name"]
        ok = bool(checks) and checks[0]["InstanceStatus"]["Status"] == "ok" \
            and checks[0]["SystemStatus"]["Status"] == "ok"
        print(f"{instance_row(inst)}  checks={'ok' if ok else 'pending'}", flush=True)
        if state == "running" and inst.get("PublicIpAddress") and ok:
            print("ip:", inst["PublicIpAddress"])
            return
        if state in TERMINAL or state == "stopped":
            raise SystemExit(f"instance is {state}")
        if time.time() > deadline:
            raise SystemExit("not ready after 15 min; check the console")
        time.sleep(POLL_SECONDS)


def cmd_terminate(a) -> None:
    ec2 = client("ec2", region_of(a))
    try:
        ec2.terminate_instances(InstanceIds=[a.id])
        for _ in range(40):
            state = _describe(ec2, [a.id])[0]["State"]["Name"]
            print(f"{a.id}: {state}", flush=True)
            if state == "terminated":
                return
            time.sleep(POLL_SECONDS)
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "terminate")
    raise SystemExit("still not terminated after 10 min; check the console")


def cmd_budget(a) -> None:
    """A monthly cost budget that emails at 80% and 100% of `usd`. Belt and
    braces beside terminate-on-shutdown: a student's debit card should never
    learn about a leak from the statement."""
    sts = client("sts", "us-east-1")
    budgets = client("budgets", "us-east-1")   # Budgets is a global service
    try:
        account = sts.get_caller_identity()["Account"]
        budgets.create_budget(
            AccountId=account,
            Budget={"BudgetName": "polyforge-b1", "BudgetType": "COST",
                    "TimeUnit": "MONTHLY",
                    "BudgetLimit": {"Amount": str(a.usd), "Unit": "USD"}},
            NotificationsWithSubscribers=[
                {"Notification": {"NotificationType": "ACTUAL", "ComparisonOperator": "GREATER_THAN",
                                  "Threshold": float(pct), "ThresholdType": "PERCENTAGE"},
                 "Subscribers": [{"SubscriptionType": "EMAIL", "Address": a.email}]}
                for pct in (80, 100)])
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "budget")
    print(f"budget polyforge-b1: ${a.usd}/month, alerts at 80% and 100% to {a.email}")


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--region", default=None)
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("quota").set_defaults(fn=cmd_quota)
    qr = sub.add_parser("quota-request")
    qr.add_argument("vcpus", type=int)
    qr.set_defaults(fn=cmd_quota_request)
    sub.add_parser("ami").set_defaults(fn=cmd_ami)
    k = sub.add_parser("key-add")
    k.add_argument("pubfile")
    k.set_defaults(fn=cmd_key_add)
    l = sub.add_parser("launch")
    l.add_argument("--type", default=DEFAULT_TYPE)
    l.add_argument("--disk", type=int, default=DEFAULT_DISK_GIB)
    l.add_argument("--az", default=None)
    l.set_defaults(fn=cmd_launch)
    s = sub.add_parser("status")
    s.add_argument("id", nargs="?")
    s.set_defaults(fn=cmd_status)
    w = sub.add_parser("wait")
    w.add_argument("id")
    w.set_defaults(fn=cmd_wait)
    t = sub.add_parser("terminate")
    t.add_argument("id")
    t.set_defaults(fn=cmd_terminate)
    b = sub.add_parser("budget")
    b.add_argument("usd", type=int)
    b.add_argument("email")
    b.set_defaults(fn=cmd_budget)
    a = ap.parse_args(argv)
    a.fn(a)


if __name__ == "__main__":
    main(sys.argv[1:])
