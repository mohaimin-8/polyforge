"""aws_box.py touches a meter; the parts that can be tested without one are.

Run: python -m pytest scripts/test_aws_box.py -q   (needs boto3, no credentials)
"""

from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

boto3 = pytest.importorskip("boto3")
from botocore.stub import Stubber  # noqa: E402

spec = importlib.util.spec_from_file_location(
    "aws_box", Path(__file__).resolve().parent / "aws_box.py")
ab = importlib.util.module_from_spec(spec)
spec.loader.exec_module(ab)


def test_run_instances_request_cannot_leave_a_billing_volume_behind():
    p = ab.run_instances_params("ami-123", "g6e.4xlarge", "sg-1", 120)
    assert p["InstanceType"] == "g6e.4xlarge" and p["MinCount"] == p["MaxCount"] == 1
    assert p["InstanceInitiatedShutdownBehavior"] == "terminate"
    ebs = p["BlockDeviceMappings"][0]["Ebs"]
    assert ebs == {"VolumeSize": 120, "VolumeType": "gp3", "DeleteOnTermination": True}
    assert p["KeyName"] == ab.KEY_NAME and p["SecurityGroupIds"] == ["sg-1"]
    assert "Placement" not in p
    assert ab.run_instances_params("ami", "t", "sg", 1, az="us-east-1b")["Placement"] == \
        {"AvailabilityZone": "us-east-1b"}
    assert p["MetadataOptions"] == {"HttpTokens": "required"}
    xvda = ab.run_instances_params("ami", "t", "sg", 1, root_device="/dev/xvda")
    assert xvda["BlockDeviceMappings"][0]["DeviceName"] == "/dev/xvda"


def test_only_ssh_is_opened():
    rules = ab.ssh_ingress()
    assert len(rules) == 1 and rules[0]["FromPort"] == rules[0]["ToPort"] == 22


def test_ami_resolution_falls_back_and_then_refuses():
    ssm = boto3.client("ssm", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="y")
    with Stubber(ssm) as stub:
        stub.add_client_error("get_parameter", "ParameterNotFound",
                              expected_params={"Name": ab.AMI_PARAMS[0]})
        stub.add_response("get_parameter", {"Parameter": {"Name": ab.AMI_PARAMS[1],
                                                          "Value": "ami-2404", "Type": "String"}},
                          expected_params={"Name": ab.AMI_PARAMS[1]})
        assert ab.resolve_ami(ssm) == ("ami-2404", ab.AMI_PARAMS[1])
    with Stubber(ssm) as stub:
        for name in ab.AMI_PARAMS:
            stub.add_client_error("get_parameter", "ParameterNotFound",
                                  expected_params={"Name": name})
        with pytest.raises(SystemExit):
            ab.resolve_ami(ssm)


def test_region_precedence(monkeypatch):
    monkeypatch.delenv("AWS_REGION", raising=False)
    assert ab.region_of(type("A", (), {"region": None})()) == "us-east-1"
    monkeypatch.setenv("AWS_REGION", "eu-west-1")
    assert ab.region_of(type("A", (), {"region": None})()) == "eu-west-1"
    assert ab.region_of(type("A", (), {"region": "us-west-2"})()) == "us-west-2"


def test_missing_credentials_are_explained_not_dumped():
    from botocore.exceptions import NoCredentialsError
    with pytest.raises(SystemExit) as exc:
        ab.die(NoCredentialsError(), "launch")
    assert "~/.aws/credentials" in str(exc.value)


def test_wait_stops_on_a_dead_instance(monkeypatch):
    monkeypatch.setattr(ab, "client", lambda *_a, **_k: None)
    monkeypatch.setattr(ab, "_describe", lambda _ec2, _ids: [{
        "InstanceId": "i-1", "State": {"Name": "terminated"}, "InstanceType": "g6e.4xlarge"}])

    class _EC2:
        def describe_instance_status(self, **_kw):
            return {"InstanceStatuses": []}
    monkeypatch.setattr(ab, "client", lambda *_a, **_k: _EC2())
    with pytest.raises(SystemExit) as exc:
        ab.cmd_wait(type("A", (), {"id": "i-1", "region": None})())
    assert "terminated" in str(exc.value)


def test_subnet_is_picked_in_an_az_that_offers_the_type():
    ec2 = boto3.client("ec2", region_name="us-east-1",
                       aws_access_key_id="x", aws_secret_access_key="y")
    offerings = {"InstanceTypeOfferings": [
        {"InstanceType": "g6e.4xlarge", "LocationType": "availability-zone", "Location": "us-east-1d"},
        {"InstanceType": "g6e.4xlarge", "LocationType": "availability-zone", "Location": "us-east-1b"}]}
    subnets = {"Subnets": [
        {"SubnetId": "subnet-a", "AvailabilityZone": "us-east-1a", "VpcId": "vpc-1"},
        {"SubnetId": "subnet-d", "AvailabilityZone": "us-east-1d", "VpcId": "vpc-1"}]}
    with Stubber(ec2) as stub:
        stub.add_response("describe_instance_type_offerings", offerings)
        stub.add_response("describe_subnets", subnets)
        # 1a has a subnet but no offering; 1b an offering but no subnet; 1d both
        assert ab.pick_subnet(ec2, "g6e.4xlarge", None) == ("subnet-d", "us-east-1d")
    with Stubber(ec2) as stub:
        stub.add_response("describe_instance_type_offerings", offerings)
        with pytest.raises(SystemExit) as exc:
            ab.pick_subnet(ec2, "g6e.4xlarge", "us-east-1a")
        assert "not offered in us-east-1a" in str(exc.value)
    p = ab.run_instances_params("ami", "g6e.4xlarge", "sg", 120, az="us-east-1d", subnet_id="subnet-d")
    assert p["SubnetId"] == "subnet-d" and p["Placement"] == {"AvailabilityZone": "us-east-1d"}
