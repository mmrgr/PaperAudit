from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from paperaudit import llm


def main() -> int:
    calls: list[dict] = []
    original = llm._request

    def fake_request(url, headers, payload, timeout):
        calls.append(payload)
        return {"candidates": [{"content": {"parts": [{"text": "OK"}]}}]}

    llm._request = fake_request
    try:
        result = llm.chat(
            {
                "protocol": "gemini",
                "api_key": "test-key",
                "base_url": "https://example.invalid/v1beta",
                "model": "gemini-test",
            },
            [
                {"role": "system", "content": "Use only grounded evidence."},
                {"role": "user", "content": "Reply OK."},
            ],
        )
    finally:
        llm._request = original
    assert result["text"] == "OK"
    assert calls and calls[0]["systemInstruction"]["parts"][0]["text"] == "Use only grounded evidence."
    assert calls[0]["contents"][0]["parts"][0]["text"] == "Reply OK."
    print({"status": "ok", "system_instruction": True})
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
