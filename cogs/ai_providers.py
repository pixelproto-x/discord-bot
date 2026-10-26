"""Multi-provider text generation fallback for the Discord bot.

All cloud credentials are read from the project-root .env/environment.
Provider adapters return an empty string on failure so the existing caller can
continue its fallback chain without exposing provider errors to Discord users.
"""
from __future__ import annotations

import asyncio
import os
from typing import Any

import aiohttp


def _env(name: str, default: str = "") -> str:
    return os.getenv(name, default).strip()


def _openrouter_model() -> str:
    model = _env("OPENROUTER_MODEL", "openrouter/free")
    if model != "openrouter/free" and not model.endswith(":free"):
        print("[OpenRouter] model rejected: only openrouter/free or a :free model is allowed")
        return ""
    return model


def _provider_specs() -> list[dict[str, Any]]:
    specs: list[dict[str, Any]] = []

    def openai_compatible(label: str, key_name: str, base_url: str, model_name: str,
                          default_model: str, *, model_transform=None):
        key = _env(key_name)
        model = _env(model_name, default_model)
        if model_transform:
            model = model_transform()
        if key and model:
            specs.append({
                "name": label, "key": key, "url": base_url.rstrip("/") + "/chat/completions",
                "model": model, "kind": "openai",
            })

    openai_compatible("Groq", "GROQ_API_KEY", "https://api.groq.com/openai/v1",
                      "GROQ_MODEL", "openai/gpt-oss-120b")
    openai_compatible("OpenRouter", "OPENROUTER_API_KEY", "https://openrouter.ai/api/v1",
                      "OPENROUTER_MODEL", "openrouter/free", model_transform=_openrouter_model)
    cf_account = _env("CLOUDFLARE_ACCOUNT_ID")
    cf_token = _env("CLOUDFLARE_API_TOKEN")
    cf_model = _env("CLOUDFLARE_MODEL", "@cf/meta/llama-3.1-8b-instruct")
    if cf_account and cf_token and cf_model:
        specs.append({
            "name": "Cloudflare Workers AI", "key": cf_token,
            "url": f"https://api.cloudflare.com/client/v4/accounts/{cf_account}/ai/v1/chat/completions",
            "model": cf_model, "kind": "openai",
        })
    openai_compatible("GitHub Models", "GITHUB_MODELS_TOKEN", "https://models.github.ai/inference",
                      "GITHUB_MODELS_MODEL", "openai/gpt-4.1-mini")
    openai_compatible("SiliconFlow", "SILICONFLOW_API_KEY", "https://api.siliconflow.com/v1",
                      "SILICONFLOW_MODEL", "Qwen/Qwen3-8B")
    openai_compatible("Zhipu AI", "ZHIPU_API_KEY", "https://open.bigmodel.cn/api/paas/v4",
                      "ZHIPU_MODEL", "glm-4.5-flash")
    openai_compatible("SambaNova Cloud", "SAMBANOVA_API_KEY", "https://api.sambanova.ai/v1",
                      "SAMBANOVA_MODEL", "Meta-Llama-3.3-70B-Instruct")
    openai_compatible("Cerebras Cloud", "CEREBRAS_API_KEY", "https://api.cerebras.ai/v1",
                      "CEREBRAS_MODEL", "gpt-oss-120b")
    cohere_key = _env("COHERE_API_KEY")
    if cohere_key:
        specs.append({"name": "Cohere", "key": cohere_key,
                      "url": "https://api.cohere.com/v2/chat", "model": _env("COHERE_MODEL", "command-a-03-2025"),
                      "kind": "cohere"})
    openai_compatible("Mistral AI", "MISTRAL_API_KEY", "https://api.mistral.ai/v1",
                      "MISTRAL_MODEL", "mistral-small-latest")
    hf_token = _env("HUGGINGFACE_API_KEY")
    hf_model = _env("HUGGINGFACE_MODEL", "meta-llama/Llama-3.3-70B-Instruct")
    if hf_token and hf_model:
        specs.append({"name": "Hugging Face", "key": hf_token,
                      "url": "https://router.huggingface.co/v1/chat/completions",
                      "model": hf_model, "kind": "openai"})
    openai_compatible("NVIDIA NIM", "NVIDIA_NIM_API_KEY", "https://integrate.api.nvidia.com/v1",
                      "NVIDIA_NIM_MODEL", "meta/llama-3.3-70b-instruct")
    openai_compatible("OpenAI", "OPENAI_API_KEY", "https://api.openai.com/v1",
                      "OPENAI_MODEL", "gpt-4.1-mini")
    return specs


async def _request_provider(spec: dict[str, Any], prompt: str, system_instruction: str,
                            *, max_tokens: int, temperature: float,
                            timeout_seconds: float) -> str:
    headers = {
        "Authorization": f"Bearer {spec['key']}",
        "Content-Type": "application/json",
    }
    if spec["name"] == "OpenRouter":
        headers["HTTP-Referer"] = _env("OPENROUTER_SITE_URL", "https://github.com/pixelproto-x/discord-bot")
        headers["X-Title"] = _env("OPENROUTER_APP_NAME", "Discord Bot")
    messages = [
        {"role": "system", "content": system_instruction or "用自然的繁體中文回答"},
        {"role": "user", "content": prompt},
    ]
    if spec["kind"] == "cohere":
        payload = {
            "model": spec["model"],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }
    else:
        payload = {
            "model": spec["model"],
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
        }

    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(spec["url"], headers=headers, json=payload) as response:
            if response.status < 200 or response.status >= 300:
                # Log only provider name and HTTP status; never print credentials or response body.
                print(f"[AI fallback] provider={spec['name']} http_status={response.status}")
                return ""
            data = await response.json(content_type=None)

    if spec["kind"] == "cohere":
        message = data.get("message") or {}
        content = message.get("content") or []
        for part in content:
            if isinstance(part, dict) and part.get("type") == "text" and part.get("text"):
                return str(part["text"]).strip()
        return ""
    choices = data.get("choices") or []
    if not choices:
        return ""
    message = choices[0].get("message") or {}
    content = message.get("content")
    if isinstance(content, list):
        content = "".join(
            str(part.get("text") or "") for part in content if isinstance(part, dict)
        )
    return str(content or "").strip()


async def _request_gemini(prompt: str, system_instruction: str, *, max_tokens: int,
                          temperature: float, timeout_seconds: float) -> str:
    key = _env("GEMINI_API_KEY") or _env("GOOGLE_API_KEY")
    if not key:
        return ""
    model = _env("GEMINI_MODEL", "gemini-3.8-flash")
    payload = {
        "system_instruction": {"parts": [{"text": system_instruction or "用自然的繁體中文回答"}]},
        "contents": [{"role": "user", "parts": [{"text": prompt}]}],
        "generationConfig": {"maxOutputTokens": max_tokens, "temperature": temperature},
    }
    timeout = aiohttp.ClientTimeout(total=timeout_seconds)
    url = f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent"
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.post(url, params={"key": key}, json=payload) as response:
            if response.status < 200 or response.status >= 300:
                print(f"[AI fallback] provider=Gemini http_status={response.status}")
                return ""
            data = await response.json(content_type=None)
    candidates = data.get("candidates") or []
    if not candidates:
        return ""
    parts = (candidates[0].get("content") or {}).get("parts") or []
    return "\n".join(str(part.get("text") or "") for part in parts if isinstance(part, dict)).strip()


async def generate_fallback(prompt: str, system_instruction: str, *, label: str = "AI fallback",
                            max_tokens: int = 320, temperature: float = 0.75,
                            top_p: float | None = None) -> str:
    """Try configured cloud providers in a predictable order."""
    # Gemini is the primary SDK provider in cogs/media.py; avoid retrying it here
    # after its primary attempt has already failed or hit a quota limit.
    providers = list((spec["name"], spec) for spec in _provider_specs())
    seen = set()
    loop = asyncio.get_running_loop()
    try:
        chain_timeout = max(10, min(120, int(_env("AI_PROVIDER_CHAIN_TIMEOUT", "45"))))
    except ValueError:
        chain_timeout = 45
    deadline = loop.time() + chain_timeout

    for name, provider in providers:
        if name in seen:
            continue
        seen.add(name)
        remaining = deadline - loop.time()
        if remaining < 2:
            print(f"[{label}] provider chain time budget exhausted")
            break
        provider_timeout = min(timeout_seconds_for(name), max(1, int(remaining) - 1))
        try:
            if callable(provider):
                request = provider(
                    prompt, system_instruction, max_tokens=max_tokens,
                    temperature=temperature, timeout_seconds=provider_timeout,
                )
            else:
                request = _request_provider(
                    provider, prompt, system_instruction, max_tokens=max_tokens,
                    temperature=temperature, timeout_seconds=provider_timeout,
                )
            answer = await asyncio.wait_for(request, timeout=provider_timeout + 1)
            if answer:
                model = provider.get("model", "configured") if isinstance(provider, dict) else "configured"
                print(f"[{label}] provider={name} model={model} chars={len(answer)}")
                return answer
        except Exception as exc:
            print(f"[{label}] provider={name} error={type(exc).__name__}")
    return ""


def timeout_seconds_for(name: str) -> int:
    raw = _env("AI_PROVIDER_TIMEOUT", "8")
    try:
        return max(5, min(15, int(raw)))
    except ValueError:
        return 8
