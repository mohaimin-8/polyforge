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
    python scripts/aws_box.py launch [--type g6e.4xlarge] [--disk 120] [--az us-east-1a] [--spot]   # BILLABLE
        # ssh opens to this machine's public IP/32 only (--ssh-cidr to override);
        # refuses while another polyforge-b1 instance exists (--allow-second)
    python scripts/aws_box.py status [ID]            # instances tagged polyforge-b1: state, ip
    python scripts/aws_box.py wait ID                # poll until running + status checks ok, print ip
    python scripts/aws_box.py terminate ID           # stop the meter; confirms the terminal state
    python scripts/aws_box.py budget 30 EMAIL        # monthly cost alert at 80%/100% (free)
"""

from __future__ import annotations

import argparse
import ipaddress
import os
import sys
import time
from pathlib import Path
from urllib.request import urlopen

import boto3
from botocore.exceptions import BotoCoreError, ClientError, NoCredentialsError

DEFAULT_REGION = "us-east-1"
DEFAULT_TYPE = "g6e.2xlarge"    # 8 vCPU: measured sufficient, session 48
STEP_UP_TYPE = "g6e.4xlarge"
DEFAULT_DISK_GIB = 120          # three models (~22 GB) + images + docker layers
KEY_NAME = "polyforge-b1"
SG_NAME = "polyforge-b1-ssh"
TAG = {"Key": "Name", "Value": "polyforge-b1"}
# "Running On-Demand G and VT instances" -- the quota is in vCPUs; a fresh
# account has 0; g6e.2xlarge needs 8, g6e.4xlarge 16.
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
                         root_device: str = "/dev/sda1", subnet_id: str | None = None,
                         spot: bool = False) -> dict:
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
    if spot:
        # One-time Spot request; an interruption TERMINATES (never stops) the
        # instance, so nothing lingers billing. The harness runner resumes
        # banked run_ids after a relaunch; the tier host must be brought up
        # again by hand (runbook section 2).
        params["InstanceMarketOptions"] = {
            "MarketType": "spot",
            "SpotOptions": {"SpotInstanceType": "one-time",
                            "InstanceInterruptionBehavior": "terminate"}}
    return params


def ssh_ingress(cidr: str) -> list[dict]:
    """SSH from `cidr` only. It was 0.0.0.0/0 -- key auth, but a GPU box's
    sshd open to every scanner on the internet (audit 2026-09-26)."""
    return [{"IpProtocol": "tcp", "FromPort": 22, "ToPort": 22,
             "IpRanges": [{"CidrIp": cidr, "Description": "ssh from the operator's address"}]}]


CHECKIP_URL = "https://checkip.amazonaws.com"


def my_public_cidr() -> str:
    """This machine's public IPv4 address as a /32, from AWS's own echo
    service. Anything that is not an address (a captive portal, an outage)
    stops the launch rather than opening the wrong range."""
    try:
        with urlopen(CHECKIP_URL, timeout=10) as resp:
            text = resp.read().decode("ascii", "replace").strip()
    except OSError as e:
        raise SystemExit(f"could not look up this machine's public address ({e}); "
                         "pass --ssh-cidr") from e
    try:
        return f"{ipaddress.IPv4Address(text)}/32"
    except ValueError:
        raise SystemExit(f"{CHECKIP_URL} returned {text[:60]!r}, not an IPv4 address; "
                         "pass --ssh-cidr") from None


# States in which an instance still bills (or will, once it starts).
DEAD_STATES = ("terminated", "shutting-down")


def alive_instances(rows: list[dict]) -> list[dict]:
    return [i for i in rows if i["State"]["Name"] not in DEAD_STATES]


def _cidr(text: str) -> str:
    """argparse type for --ssh-cidr: a valid IPv4 network, normalised."""
    try:
        return str(ipaddress.IPv4Network(text, strict=False))
    except ValueError as e:
        raise argparse.ArgumentTypeError(f"not an IPv4 CIDR: {text!r}") from e


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


def ensure_security_group(ec2, cidr: str) -> str:
    """The SSH group, open to `cidr` alone. An existing group is reconciled
    on every launch: the operator's address changes between sittings, and a
    group created before 2026-09-26 still carries 0.0.0.0/0."""
    found = ec2.describe_security_groups(
        Filters=[{"Name": "group-name", "Values": [SG_NAME]}])["SecurityGroups"]
    if found:
        sg = found[0]
        ssh = [p for p in sg.get("IpPermissions", [])
               if p.get("FromPort") == 22 and p.get("ToPort") == 22]
        current = sorted(r["CidrIp"] for p in ssh for r in p.get("IpRanges", []))
        if current == [cidr] and not any(p.get("Ipv6Ranges") for p in ssh):
            return sg["GroupId"]
        if ssh:
            ec2.revoke_security_group_ingress(GroupId=sg["GroupId"], IpPermissions=ssh)
        ec2.authorize_security_group_ingress(GroupId=sg["GroupId"], IpPermissions=ssh_ingress(cidr))
        return sg["GroupId"]
    vpc = ec2.describe_vpcs(Filters=[{"Name": "isDefault", "Values": ["true"]}])["Vpcs"]
    if not vpc:
        raise SystemExit("no default VPC in this region; create one in the console "
                         "(VPC -> Actions -> Create default VPC) and retry")
    sg = ec2.create_security_group(GroupName=SG_NAME, Description="polyforge B1 ssh",
                                   VpcId=vpc[0]["VpcId"])
    ec2.authorize_security_group_ingress(GroupId=sg["GroupId"], IpPermissions=ssh_ingress(cidr))
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
    need = {DEFAULT_TYPE: 8, STEP_UP_TYPE: 16}
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
    # One instance, ever, unless the operator says otherwise: nothing used to
    # stop a second launch billing beside a forgotten first (audit
    # 2026-09-26). A stopped instance counts -- its EBS still bills.
    try:
        alive = alive_instances(_describe(ec2, None))
    except (ClientError, BotoCoreError, NoCredentialsError) as e:
        die(e, "describe")
    if alive and not a.allow_second:
        raise SystemExit("refusing to launch: a polyforge-b1 instance already exists -- "
                         + "; ".join(instance_row(i) for i in alive)
                         + ". Terminate it, or pass --allow-second.")
    cidr = a.ssh_cidr or my_public_cidr()
    if cidr.endswith("/0"):
        print("WARNING: ssh will be open to the whole internet (--ssh-cidr " + cidr + ")", flush=True)
    try:
        ami, _ = resolve_ami(client("ssm", region))
        sg_id = ensure_security_group(ec2, cidr)
        root = ec2.describe_images(ImageIds=[ami])["Images"][0]["RootDeviceName"]
        subnet, az = pick_subnet(ec2, a.type, a.az)
        params = run_instances_params(ami, a.type, sg_id, a.disk, az=az,
                                      root_device=root, subnet_id=subnet, spot=a.spot)
        print(f"launching {a.type}{' SPOT' if a.spot else ''} in {az} ({subnet}) from {ami}, "
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
    l.add_argument("--spot", action="store_true",
                   help="one-time Spot request (needs the Spot G/VT quota, L-3819A6DF)")
    l.add_argument("--ssh-cidr", default=None, type=_cidr,
                   help="address range allowed to ssh in (default: this machine's public IP/32)")
    l.add_argument("--allow-second", action="store_true",
                   help="launch even though a polyforge-b1 instance already exists")
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
