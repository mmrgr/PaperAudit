"""Provider configuration must not persist plaintext API keys."""

from __future__ import annotations

import sys
import tempfile
import os
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperrevamper.llm import load_config, public_config, save_config


def main() -> int:
    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "config.json"
        payload = {
            "active_profile": "custom",
            "profiles": [{"id": "custom", "protocol": "openai_compatible", "model": "test", "api_key": "secret-value"}],
        }
        try:
            save_config(path, payload)
        except ValueError:
            # Non-Windows builds intentionally refuse to persist plaintext;
            # those users must provide api_key_env instead.
            assert os.name != "nt"
            assert not path.exists()
            print("llm config smoke checks passed (environment-key guard)")
            return 0
        raw = path.read_text(encoding="utf-8")
        assert "secret-value" not in raw
        assert "dpapi:v1:" in raw
        loaded = load_config(path)
        assert loaded["profiles"][0]["api_key"] == "secret-value"
        assert public_config(loaded)["profiles"][0]["has_api_key"] is True
        assert "api_key_protected" not in public_config(loaded)["profiles"][0]
    print("llm config smoke checks passed")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
