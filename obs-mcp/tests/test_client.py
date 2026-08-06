from pathlib import Path
import sys

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from obs_mcp.client import get_object_metadata, list_configured_buckets, list_objects, read_text_object
from obs_mcp.config import ObsEnvironment


class FakeS3Client:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict]] = []

    def list_objects_v2(self, **kwargs):
        self.calls.append(("list", kwargs))
        return {"Contents": [{"Key": "logs/a.txt", "Size": 5, "LastModified": "now", "ETag": "etag"}], "NextContinuationToken": "next", "IsTruncated": True}

    def head_object(self, **kwargs):
        self.calls.append(("head", kwargs))
        return {"ContentLength": 5, "ContentType": "text/plain", "ETag": "etag", "LastModified": "now", "Metadata": {"source": "test"}}

    def get_object(self, **kwargs):
        self.calls.append(("get", kwargs))
        return {"Body": FakeBody(b"hello")}


class FakeBody:
    def __init__(self, data: bytes) -> None:
        self.data = data

    def read(self, amount: int) -> bytes:
        return self.data[:amount]

    def close(self) -> None:
        pass


CONFIG = ObsEnvironment("UAT", "http://endpoint", "ak", "sk", "cn-east-3", ("mfg-mes",), False, 5, 5)


def test_list_configured_buckets_returns_only_allowlisted_buckets() -> None:
    assert list_configured_buckets(CONFIG) == {"buckets": ["mfg-mes"]}


def test_list_objects_uses_allowlisted_bucket_and_continuation_token() -> None:
    client = FakeS3Client()
    result = list_objects(client, CONFIG, "mfg-mes", prefix="logs/", continuation_token="token", max_keys=9999)
    assert client.calls == [("list", {"Bucket": "mfg-mes", "Prefix": "logs/", "ContinuationToken": "token", "MaxKeys": 200})]
    assert result["next_continuation_token"] == "next"
    assert result["objects"][0]["key"] == "logs/a.txt"


def test_metadata_and_text_read_are_read_only_and_bounded() -> None:
    client = FakeS3Client()
    metadata = get_object_metadata(client, CONFIG, "mfg-mes", "logs/a.txt")
    content = read_text_object(client, CONFIG, "mfg-mes", "logs/a.txt", max_bytes=2)
    assert metadata["size_bytes"] == 5
    assert content == {"bucket": "mfg-mes", "key": "logs/a.txt", "text": "he", "truncated": True}
    assert client.calls[0][0] == "head"
    assert client.calls[1] == ("get", {"Bucket": "mfg-mes", "Key": "logs/a.txt", "Range": "bytes=0-2"})
