from typing import Any

from obs_mcp.config import ObsEnvironment

MAX_OBJECTS = 200
MAX_TEXT_BYTES = 65_536


class ObsQueryError(ValueError):
    pass


def list_configured_buckets(config: ObsEnvironment) -> dict:
    """Expose only the explicitly authorized buckets for this environment."""
    return {"buckets": list(config.buckets)}


def create_client(config: ObsEnvironment) -> Any:
    try:
        import boto3
        from botocore.config import Config
        return boto3.client("s3", endpoint_url=config.endpoint, aws_access_key_id=config.access_key_id,
                            aws_secret_access_key=config.secret_access_key, region_name=config.region,
                            use_ssl=config.use_ssl, config=Config(connect_timeout=config.connect_timeout_seconds,
                            read_timeout=config.read_timeout_seconds, retries={"max_attempts": 2}, s3={"addressing_style": "path"}))
    except Exception as error:
        raise ObsQueryError("OBS 客户端初始化失败") from error


def _allowed(config: ObsEnvironment, bucket: str) -> None:
    if bucket not in config.buckets:
        raise ObsQueryError(f"桶未被授权访问：{bucket}")


def _call(operation: Any) -> Any:
    try:
        return operation()
    except Exception as error:
        raise ObsQueryError(f"OBS 查询失败：{error}") from error


def list_objects(client: Any, config: ObsEnvironment, bucket: str, *, prefix: str | None = None,
                 continuation_token: str | None = None, max_keys: int = 100) -> dict:
    _allowed(config, bucket)
    request: dict[str, Any] = {"Bucket": bucket, "MaxKeys": min(max(max_keys, 1), MAX_OBJECTS)}
    if prefix: request["Prefix"] = prefix
    if continuation_token: request["ContinuationToken"] = continuation_token
    response = _call(lambda: client.list_objects_v2(**request))
    return {"bucket": bucket, "prefix": prefix, "objects": [{"key": item["Key"], "size_bytes": item.get("Size"), "etag": item.get("ETag"), "last_modified": str(item.get("LastModified"))} for item in response.get("Contents", [])], "is_truncated": bool(response.get("IsTruncated")), "next_continuation_token": response.get("NextContinuationToken")}


def get_object_metadata(client: Any, config: ObsEnvironment, bucket: str, key: str) -> dict:
    _allowed(config, bucket)
    response = _call(lambda: client.head_object(Bucket=bucket, Key=key))
    return {"bucket": bucket, "key": key, "size_bytes": response.get("ContentLength"), "content_type": response.get("ContentType"), "etag": response.get("ETag"), "last_modified": str(response.get("LastModified")), "metadata": response.get("Metadata", {})}


def read_text_object(client: Any, config: ObsEnvironment, bucket: str, key: str, *, max_bytes: int = 16_384, encoding: str = "utf-8") -> dict:
    _allowed(config, bucket)
    limit = min(max(max_bytes, 1), MAX_TEXT_BYTES)
    response = _call(lambda: client.get_object(Bucket=bucket, Key=key, Range=f"bytes=0-{limit}"))
    body = response["Body"]
    try:
        raw = body.read(limit + 1)
    finally:
        body.close()
    return {"bucket": bucket, "key": key, "text": raw[:limit].decode(encoding, errors="replace"), "truncated": len(raw) > limit}
