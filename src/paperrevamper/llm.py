"""Local provider profiles and lightweight chat clients.

Configuration is JSON, but provider secrets are DPAPI-protected on Windows or
resolved from environment variables.  Coding Agent / Codex remains an
explicit host route and never needs an API key.
"""

from __future__ import annotations

import json
import ipaddress
import os
import socket
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any
from urllib.parse import urlparse

from paperrevamper.secret_store import SecretProtectionError, is_protected, protect_secret, unprotect_secret


DEFAULT_PROFILES = [
    {"id": "host_agent", "label": "WorkBuddy / Codex Coding Agent", "protocol": "host_agent", "base_url": "", "model": "", "api_key_env": "", "enabled": True},
    {"id": "openai", "label": "OpenAI", "protocol": "openai_compatible", "base_url": "https://api.openai.com/v1", "model": "gpt-4.1-mini", "api_key_env": "OPENAI_API_KEY", "enabled": True},
    {"id": "deepseek", "label": "DeepSeek", "protocol": "openai_compatible", "base_url": "https://api.deepseek.com/v1", "model": "deepseek-chat", "api_key_env": "DEEPSEEK_API_KEY", "enabled": True},
    {"id": "qwen", "label": "通义千问", "protocol": "openai_compatible", "base_url": "https://dashscope.aliyuncs.com/compatible-mode/v1", "model": "qwen-plus", "api_key_env": "DASHSCOPE_API_KEY", "enabled": True},
    {"id": "zhipu", "label": "智谱 GLM", "protocol": "openai_compatible", "base_url": "https://open.bigmodel.cn/api/paas/v4", "model": "glm-4-flash", "api_key_env": "ZHIPUAI_API_KEY", "enabled": True},
    {"id": "moonshot", "label": "Moonshot", "protocol": "openai_compatible", "base_url": "https://api.moonshot.cn/v1", "model": "moonshot-v1-8k", "api_key_env": "MOONSHOT_API_KEY", "enabled": True},
    {"id": "anthropic", "label": "Anthropic Claude", "protocol": "anthropic", "base_url": "https://api.anthropic.com", "model": "claude-3-5-haiku-latest", "api_key_env": "ANTHROPIC_API_KEY", "enabled": True},
    {"id": "gemini", "label": "Google Gemini", "protocol": "gemini", "base_url": "https://generativelanguage.googleapis.com/v1beta", "model": "gemini-2.0-flash", "api_key_env": "GEMINI_API_KEY", "enabled": True},
    {"id": "custom_openai", "label": "自定义 OpenAI 兼容 API", "protocol": "openai_compatible", "base_url": "http://127.0.0.1:8000/v1", "model": "", "api_key_env": "", "enabled": False, "max_output_tokens": 4096},
]

_DEFAULT_MAX_OUTPUT_TOKENS = 4096
_MAX_RESPONSE_BYTES = 4_000_000
_OUTPUT_TOKEN_FIELDS = {"auto", "max_tokens", "max_completion_tokens", "none"}


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Keep provider requests on the endpoint that passed validation."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):  # noqa: N802
        raise urllib.error.HTTPError(req.full_url, code, msg, headers, fp)


def _safe_urlopen(request: urllib.request.Request, *, timeout: float):
    """Open one already-validated request without following redirects."""

    return urllib.request.build_opener(_NoRedirectHandler).open(request, timeout=timeout)


def _default_config() -> dict[str, Any]:
    return {"version": 1, "active_profile": "host_agent", "profiles": [dict(profile) for profile in DEFAULT_PROFILES]}


def _normalise_max_output(value: Any) -> int:
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        parsed = _DEFAULT_MAX_OUTPUT_TOKENS
    return max(256, min(16_384, parsed))


def _normalise_output_token_field(value: Any) -> str:
    field = str(value or "auto").strip().casefold()
    return field if field in _OUTPUT_TOKEN_FIELDS else "auto"


def _normalise_profile(raw: dict[str, Any]) -> dict[str, Any]:
    profile = {
        "id": str(raw.get("id", "custom")),
        "label": str(raw.get("label", raw.get("id", "自定义模型"))),
        "protocol": str(raw.get("protocol", "openai_compatible")),
        "base_url": str(raw.get("base_url", "")).strip(),
        "model": str(raw.get("model", "")).strip(),
        "api_key_env": str(raw.get("api_key_env", "")).strip(),
        "api_key": str(raw.get("api_key", "")),
        "api_key_protected": str(raw.get("api_key_protected", "")),
        "api_key_status": str(raw.get("api_key_status", "")),
        "enabled": raw.get("enabled", True) is not False,
        "max_output_tokens": _normalise_max_output(raw.get("max_output_tokens", _DEFAULT_MAX_OUTPUT_TOKENS)),
        "output_token_field": _normalise_output_token_field(raw.get("output_token_field", "auto")),
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
    normalised = []
    for item in profiles:
        if not isinstance(item, dict):
            continue
        profile = _normalise_profile(item)
        protected = profile["api_key_protected"]
        if protected:
            if is_protected(protected):
                try:
                    profile["api_key"] = unprotect_secret(protected)
                    profile["api_key_status"] = "dpapi"
                except SecretProtectionError:
                    profile["api_key"] = ""
                    profile["api_key_status"] = "protected-unavailable"
            else:
                profile["api_key"] = ""
                profile["api_key_status"] = "protected-unavailable"
        elif profile["api_key"]:
            # Backward compatibility: old config files may still hold a plain
            # key. It is migrated to DPAPI the next time the profile is saved.
            profile["api_key_status"] = "legacy-plaintext"
        elif profile["api_key_env"] and os.getenv(profile["api_key_env"]):
            profile["api_key_status"] = "environment"
        else:
            profile["api_key_status"] = "missing"
        normalised.append(profile)
    if not normalised:
        normalised = _default_config()["profiles"]
    return {"version": 1, "active_profile": str(raw.get("active_profile", "host_agent")), "profiles": normalised}


def save_config(path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    current = load_config(path)
    incoming = payload.get("profiles", []) if isinstance(payload, dict) else []
    profiles = []
    persisted_profiles = []
    for raw in incoming:
        if not isinstance(raw, dict):
            continue
        profile = _normalise_profile(raw)
        old = next((item for item in current["profiles"] if item["id"] == profile["id"]), None)
        entered_key = str(raw.get("api_key", ""))
        if entered_key:
            profile["api_key"] = entered_key
            try:
                profile["api_key_protected"] = protect_secret(entered_key)
            except SecretProtectionError as exc:
                raise ValueError(str(exc)) from exc
            profile["api_key_status"] = "dpapi"
        elif old and old.get("api_key"):
            profile["api_key"] = old["api_key"]
            try:
                profile["api_key_protected"] = protect_secret(profile["api_key"])
            except SecretProtectionError as exc:
                raise ValueError(
                    "现有 API Key 仍是旧式明文配置且无法安全迁移；请设置对应环境变量后清除本地密钥。"
                ) from exc
            profile["api_key_status"] = "dpapi"
        elif old and old.get("api_key_protected"):
            profile["api_key_protected"] = old["api_key_protected"]
            profile["api_key_status"] = old.get("api_key_status", "protected-unavailable")
        elif raw.get("api_key_protected") and is_protected(str(raw["api_key_protected"])):
            profile["api_key_protected"] = str(raw["api_key_protected"])
            profile["api_key_status"] = "protected-unavailable"
        else:
            profile["api_key"] = ""
            profile["api_key_protected"] = ""
            profile["api_key_status"] = "environment" if profile["api_key_env"] and os.getenv(profile["api_key_env"]) else "missing"
        profiles.append(profile)
        persisted = {key: value for key, value in profile.items() if key not in {"api_key", "api_key_status"}}
        persisted_profiles.append(persisted)
    if not profiles:
        profiles = current["profiles"]
        persisted_profiles = [
            {key: value for key, value in profile.items() if key not in {"api_key", "api_key_status"}}
            for profile in profiles
        ]
    config = {"version": 1, "active_profile": str(payload.get("active_profile", current.get("active_profile", "host_agent"))), "profiles": profiles}
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    persisted = {"version": 1, "active_profile": config["active_profile"], "profiles": persisted_profiles}
    temporary = target.with_name(f".{target.name}.tmp")
    temporary.write_text(json.dumps(persisted, ensure_ascii=False, indent=2), encoding="utf-8")
    temporary.replace(target)
    return config


def public_config(config: dict[str, Any]) -> dict[str, Any]:
    profiles = []
    for raw in config.get("profiles", []):
        profile = dict(raw)
        profile.pop("api_key", None)
        profile.pop("api_key_protected", None)
        profile["has_api_key"] = bool(raw.get("api_key") or raw.get("api_key_env") and os.getenv(str(raw.get("api_key_env"))))
        profile["api_key_status"] = str(raw.get("api_key_status", "missing"))
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
    _validate_endpoint(url)
    request = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"), headers={"Content-Type": "application/json", **headers}, method="POST")
    try:
        with _safe_urlopen(request, timeout=timeout) as response:
            body = response.read(_MAX_RESPONSE_BYTES + 1)
            if len(body) > _MAX_RESPONSE_BYTES:
                raise RuntimeError(f"模型 API 响应超过 {_MAX_RESPONSE_BYTES} 字节上限")
            return json.loads(body.decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read(_MAX_RESPONSE_BYTES + 1)
        if len(detail) > _MAX_RESPONSE_BYTES:
            detail = detail[:_MAX_RESPONSE_BYTES]
        detail = detail.decode("utf-8", errors="replace")[:1000]
        raise RuntimeError(f"模型 API 返回 HTTP {exc.code}: {detail}") from exc
    except urllib.error.URLError as exc:
        raise RuntimeError(f"无法连接模型 API：{exc.reason}") from exc


def _openai_compatible(profile: dict[str, Any], key: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    base = str(profile.get("base_url", "")).rstrip("/")
    url = base if base.endswith("/chat/completions") else f"{base}/chat/completions"
    payload: dict[str, Any] = {"model": model, "messages": messages, "temperature": 0.1}
    output_field = _normalise_output_token_field(profile.get("output_token_field", "auto"))
    if output_field != "none":
        payload["max_tokens" if output_field == "auto" else output_field] = _max_output_tokens(profile)
    data = _request(url, {"Authorization": f"Bearer {key}"}, payload, timeout)
    choice = (data.get("choices") or [{}])[0]
    message = choice.get("message") or {}
    return {"status": "ok", "provider_response": data, "text": str(message.get("content", "")), "model": model}


def _anthropic(profile: dict[str, Any], key: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
    user_messages = [item for item in messages if item.get("role") != "system"]
    payload: dict[str, Any] = {"model": model, "max_tokens": _max_output_tokens(profile), "messages": user_messages}
    if system:
        payload["system"] = system
    data = _request(f"{str(profile.get('base_url', 'https://api.anthropic.com').rstrip('/'))}/v1/messages", {"x-api-key": key, "anthropic-version": "2023-06-01"}, payload, timeout)
    text = "".join(str(item.get("text", "")) for item in data.get("content", []) if item.get("type") == "text")
    return {"status": "ok", "provider_response": data, "text": text, "model": model}


def _max_output_tokens(profile: dict[str, Any]) -> int:
    try:
        value = int(profile.get("max_output_tokens", _DEFAULT_MAX_OUTPUT_TOKENS))
    except (TypeError, ValueError):
        value = _DEFAULT_MAX_OUTPUT_TOKENS
    return max(256, min(16_384, value))


def _validate_endpoint(url: str, *, allow_loopback: bool = True) -> None:
    """Reject unsafe endpoint forms before handing them to ``urlopen``.

    HTTPS and explicit loopback HTTP are supported.  Literal or DNS-resolved
    private, link-local, multicast, and unspecified addresses are rejected so
    a remote panel client cannot turn a saved provider or GROBID URL into an
    internal network probe.
    """

    parsed = urlparse(str(url or ""))
    if parsed.scheme not in {"http", "https"} or not parsed.hostname:
        raise ValueError("endpoint 必须是带主机名的 http(s) URL")
    if parsed.username or parsed.password or parsed.fragment:
        raise ValueError("endpoint 不允许携带用户信息或 fragment")
    host = parsed.hostname.strip().casefold()
    try:
        port = parsed.port or (443 if parsed.scheme == "https" else 80)
    except ValueError as exc:
        raise ValueError("endpoint 端口无效") from exc
    try:
        addresses = {info[4][0] for info in socket.getaddrinfo(host, port, type=socket.SOCK_STREAM)}
    except OSError as exc:
        raise ValueError(f"endpoint 主机无法解析：{host}") from exc
    has_non_loopback = False
    for raw in addresses:
        try:
            address = ipaddress.ip_address(raw.split("%", 1)[0])
        except ValueError:
            continue
        if address.is_loopback:
            if not allow_loopback:
                raise ValueError("远程面板禁止访问 loopback endpoint")
            continue
        has_non_loopback = True
        if address.is_private or address.is_link_local or address.is_multicast or address.is_reserved or address.is_unspecified:
            raise ValueError("endpoint 禁止访问本机或私有网络地址")
    if parsed.scheme == "http" and has_non_loopback:
        raise ValueError("公网 endpoint 必须使用 HTTPS；仅允许 loopback 使用 HTTP")


def _gemini(profile: dict[str, Any], key: str, model: str, messages: list[dict[str, str]], timeout: int) -> dict[str, Any]:
    system = "\n".join(str(item.get("content", "")) for item in messages if item.get("role") == "system")
    contents = [{"role": "model" if item.get("role") == "assistant" else "user", "parts": [{"text": str(item.get("content", ""))}]} for item in messages if item.get("role") != "system"]
    base = str(profile.get("base_url", "https://generativelanguage.googleapis.com/v1beta").rstrip("/"))
    payload: dict[str, Any] = {"contents": contents}
    if system:
        # Gemini uses a top-level systemInstruction field rather than an
        # ordinary message role.  Dropping the system prompt would remove the
        # evidence-gate and privacy constraints from direct Gemini runs.
        payload["systemInstruction"] = {"parts": [{"text": system}]}
    if _normalise_output_token_field(profile.get("output_token_field", "auto")) != "none":
        payload["generationConfig"] = {"maxOutputTokens": _max_output_tokens(profile)}
    # Keep the API key out of the request URL so proxies and access logs do not
    # record it as a query parameter.
    data = _request(f"{base}/models/{model}:generateContent", {"x-goog-api-key": key}, payload, timeout)
    parts = (data.get("candidates") or [{}])[0].get("content", {}).get("parts", [])
    text = "".join(str(part.get("text", "")) for part in parts)
    return {"status": "ok", "provider_response": data, "text": text, "model": model}
