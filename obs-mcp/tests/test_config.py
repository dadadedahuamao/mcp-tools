from pathlib import Path
import sys

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from obs_mcp.config import ObsConfigurationError, load_environment


def test_load_environment_reads_s3_compatible_obs_settings(tmp_path: Path) -> None:
    env_file = tmp_path / "env.yaml"
    env_file.write_text(
        """env:
  - env_name: UAT
    obs:
      endpoint: http://uat-mes-obs.cloudpond.obs.cn-east-3.myhuaweicloud.com
      access_key_id: test-ak
      secret_access_key: test-sk
      region: cn-east-3
      buckets: [mfg-mes, mfg-mes-public]
""",
        encoding="utf-8",
    )

    result = load_environment(env_file, "UAT")

    assert result.endpoint == "http://uat-mes-obs.cloudpond.obs.cn-east-3.myhuaweicloud.com"
    assert result.buckets == ("mfg-mes", "mfg-mes-public")
    assert result.use_ssl is False


def test_load_environment_rejects_missing_bucket_allowlist(tmp_path: Path) -> None:
    env_file = tmp_path / "env.yaml"
    env_file.write_text("env:\n  - env_name: UAT\n    obs: {endpoint: https://example.com, access_key_id: ak, secret_access_key: sk, region: cn-east-3}\n", encoding="utf-8")

    with pytest.raises(ObsConfigurationError, match="buckets"):
        load_environment(env_file, "UAT")
