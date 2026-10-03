"""Local provider profiles and lightweight chat clients.

Profiles are deliberately plain JSON so the desktop build remains portable.
API keys may be kept in the profile for a private local machine or referenced
through an environment variable.  Coding Agent / Codex remains an explicit
host route and never needs an API key.
"""

from __future__ import annotations

import json
import os
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any


DEFAULT_PROFILES = [
    {"id": "host_agent", "label": "WorkBuddy / Codex Coding Agent", "protocol": "host_agent", "base_url": "", "model": "", "api_key_env": "", "enabled": True},
    {"id": "openai", "label": "OpenAI", "protocol": "openai_compatible", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY", "enabled": True},
    {"id": "deepseek", "label": "DeepSeek", "protocol": "openai_compatible", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY", "enabled": True},
    {"id": "qwen", "label": "通义千问", "protocol": "openai_compatible", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "api_key_env": "DASHSCOPE_API_KEY", "enabled": True},
    {"id": "zhipu", "label": "智谱 GLM", "protocol": "openai_compatible", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash", "api_key_env": "ZHIPUAI_API_KEY", "enabled": True},
    {"id": "moonshot", "label": "Moonshot", "protocol": "openai_compatible", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "api_key_env": "MOONSHOT_API_KEY", "enabled": True},
    {"id": "anthropic", "label": "Anthropic Claude", "protocol": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-3-5-haiku-latest", "api_key_env": "ANTHROPIC_API_KEY", "enabled": True},
    {"id": "gemini", "label": "Google Gemini", "protocol": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-2.0-flash", "api_key_env": "GEMINI_API_KEY", "enabled": True},
    {"id": "custom_openai", "label": "自定义 OpenAI 兼容 API", "protocol": "openai_compatible", "base_url": "http://127.0.0.1:8000/v1", "model": "", "api_key_env": "", "enabled": False},
]


def _default_config() -> dict[str, Any]:
    return {"version": 1, "active_profile": "host_agent", "profiles": [dict(profile) for profile in DEFAULT_PROFILES]}


def _normalise_profile(raw: dict[str, Any]) -> dict[str, Any]:
    profile = {
        "id": str(raw.get("id", "custom")),
        "label": str(raw.get("label", raw.get("id", "自定义模型"))),
        "protocol": str(raw.get("protocol", "openai_compatible")),
        "base_url": str(raw.get("base_url", "")).strip(),
        "model": str(raw.get("model", "")).strip(),
        "api_key_env": str(raw.get("api_key_env", "")).strip(),
        "api_key": str(raw.get("api_key", "")),
        "enabled": raw.get("enabled", True) is not False,
    }
    if profile["protocol"] not in {"host_agent", "openai_compatible", "anthropic", "gemini"}:
        profile["protocol"] = "openai_compatible"
    return profile


def load_config(path: str | Path) -> dict[str, Any]:
    target = Path(path)
    if not target.exists():
        return _default_config()
    try:
        raw = json.loads(target.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return _default_config()
    profiles = raw.get("profiles", []) if isinstance(raw, dict) else []
    if isinstance(profiles, dict):
        profiles = [dict(value, id=key) for key, value in profiles.items() if isinstance(value, dict)]
    normalised = [_normalise_profile(item) for item in profiles if isinstance(item, dict)]
    if not normalised:
        normalised = _default_config()["profiles"]
    return {"version": 1, "active_profile": str(raw.get("active_profile", "host_agent")), "profiles": normalised}


def save_config(path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    current = load_config(path)
    incoming = payload.get("profiles", []) if isinstance(payload, dict) else []
    profiles = []
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        profile = _normalise_profile(raw)
        old = next((item for item in current["profiles"] if item["id"] == profile["id"]), None)
        if not profile["api_key"] and old and old.get("api_key"):
            profile["api_key"] = old["api_key"]
        profiles.append(profile)
    if not profiles:
        profiles = current["profiles"]
    config = {"version": 1, "active_profile": str(payload.get("active_profile", current.get("active_profile", "host_agent"))), "profiles": profiles}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(config, ensure_ascii=False, indent=2), encoding="utf-8")
    return config


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    profiles = []
    for raw in config.get("profiles", []):
        profile = dict(raw)
        profile.pop("api_key", None)
        profile["has_api_key"] = bool(raw.get("api_key") or raw.get("api_key_env") and os.getenv(str(raw.get("api_key_env"))))
        profiles.append(profile)
    return {"version": 1, "active_profile": config.get("active_profile", "host_agent"), "profiles": profiles}


def select_profile(config: dict[str, Any], profile_id: str | None = None) -> dict[str, Any]:
    wanted = profile_id or str(config.get("active_profile", "host_agent"))
    for profile in config.get("profiles", []):
        if str(profile.get("id")) == wanted:
            return dict(profile)
    raise ValueError(f"找不到模型配置：{wanted}")


def chat(profile: dict[str, Any], messages: list[dict[str, str]], timeout: int = 120) -> dict[str, Any]:
    protocol = str(profile.get("protocol", "openai_compatible"))
    if protocol == "host_agent":
        return {"status": "delegated", "message": "保留 WorkBuddy / Codex Coding Agent 通道，由宿主直接执行。"}
    key = str(profile.get("api_key", "")) or os.getenv(str(profile.get("api_key_env", "")), "")
    if not key:
        raise ValueError("未找到 API Key：请填写密钥或配置 api_key_env 环境变量")
    model = str(profile.get("model", "")).strip()
    if not model:
        raise ValueError("模型名称不能为空")
    if protocol == "openai_compatible":
        return _openai_compatible(profile, key, model, messages, timeout)
    if protocol == "anthropic":
        return _anthropic(profile, key, model, messages, timeout)
    if protocol == "gemini":
        return _gemini(profile, key, model, messages, timeout)
    raise ValueError(f"暂不支持的协议：{protocol}")


def _request(url: str, headers: dict[str, str], payload: dict[str, Any], timeout: int) -> dict[str, Any]:
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"模型 API 返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接模型 API：{exc.reason}") from exc


def _openai_compatible(profile: dict[str, Any], key: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    base = str(profile.get("base_url", "")).rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    data = _request(url, {"Authorization": f"Bearer {key}"}, {"model": model, "messages": messages, "temperature": 0.1}, timeout)
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {"status": "ok", "provider_response": data, "text": str(message.get("content", "")), "model": model}


def _anthropic(profile: dict[str, Any], key: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
    user_messages = [item for item in messages if item.get("role") != "system"]
    payload: dict[str, Any] = {"model": model, "max_tokens": 256, "messages": user_messages}
    if system:
        payload["system"] = system
    data = _request(f"{str(profile.get('base_url', 'https://api.anthropic.com').rstrip('/'))}/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, payload, timeout)
    text = "".join(str(item.get("text", "")) for item in data.get("content", []) if item.get("type") == "text")
    return {"status": "ok", "provider_response": data, "text": text, "model": model}


def _gemini(profile: dict[str, Any], key: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    contents = [{"role": "model" if item.get("role") == "assistant" else "user", "parts": [{"text": str(item.get("content", ""))}]} for item in messages if item.get("role") != "system"]
    base = str(profile.get("base_url", "https://generativelanguage.googleapis.com/v1beta").rstrip("/"))
    data = _request(f"{base}/models/{model}:generateContent?key={key}", {}, {"contents": contents}, timeout)
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(str(part.get("text", "")) for part in parts)
    return {"status": "ok", "provider_response": data, "text": text, "model": model}
