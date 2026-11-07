"""Read-only AWS access (boto3): current state for the model's context, and post-apply verification.

Nothing here changes AWS. All changes go through Terraform.
"""
import os

import boto3

REGION = os.environ.get("AWS_REGION", "eu-north-1")
NAME = os.environ.get("CHATOPS_NAME", "health-api")
CLUSTER = f"{NAME}-cluster"
SERVICE = f"{NAME}-service"
TARGET_GROUP = f"{NAME}-tg"
LOG_GROUP = f"/ecs/{NAME}"


def _client(name: str):
    return boto3.client(name, region_name=REGION)


def current_config() -> dict:
    """The effective configuration, keyed by Terraform variable name."""
    ecs = _client("ecs")
    svc = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
    td = ecs.describe_task_definition(taskDefinition=svc["taskDefinition"])["taskDefinition"]
    container = td["containerDefinitions"][0]
    dc = svc.get("deploymentConfiguration", {})
    groups = _client("logs").describe_log_groups(logGroupNamePrefix=LOG_GROUP)["logGroups"]
    return {
        "desired_count": svc["desiredCount"],
        "task_cpu": int(td["cpu"]),
        "task_memory": int(td["memory"]),
        "image_tag": container["image"].rsplit(":", 1)[-1],
        "log_retention_days": next((g.get("retentionInDays") for g in groups if g["logGroupName"] == LOG_GROUP), None),
        "health_check_grace_period_seconds": svc.get("healthCheckGracePeriodSeconds", 0),
        "deployment_minimum_healthy_percent": dc.get("minimumHealthyPercent"),
        "deployment_maximum_percent": dc.get("maximumPercent"),
        "environment": {e["name"]: e["value"] for e in container.get("environment", [])},
    }


def service_status() -> dict:
    """Running/healthy counts and the rollout state, for the status panel and post-apply checks."""
    ecs = _client("ecs")
    svc = ecs.describe_services(cluster=CLUSTER, services=[SERVICE])["services"][0]
    elb = _client("elbv2")
    tg_arn = elb.describe_target_groups(Names=[TARGET_GROUP])["TargetGroups"][0]["TargetGroupArn"]
    targets = elb.describe_target_health(TargetGroupArn=tg_arn)["TargetHealthDescriptions"]
    primary = next((d for d in svc["deployments"] if d["status"] == "PRIMARY"), {})
    return {
        "service_status": svc["status"],
        "desired": svc["desiredCount"],
        "running": svc["runningCount"],
        "pending": svc["pendingCount"],
        "rollout_state": primary.get("rolloutState"),
        "deployments": len(svc["deployments"]),
        "targets": [{"ip": t["Target"]["Id"], "state": t["TargetHealth"]["State"]} for t in targets],
        "healthy_targets": sum(1 for t in targets if t["TargetHealth"]["State"] == "healthy"),
    }


def image_tag_exists(tag: str) -> bool:
    ecr = _client("ecr")
    try:
        ecr.describe_images(repositoryName=NAME, imageIds=[{"imageTag": tag}])
        return True
    except ecr.exceptions.ImageNotFoundException:
        return False


def list_image_tags() -> list[str]:
    ecr = _client("ecr")
    tags: list[str] = []
    for page in ecr.get_paginator("describe_images").paginate(repositoryName=NAME):
        for d in page["imageDetails"]:
            tags.extend(d.get("imageTags", []))
    return sorted(tags)
