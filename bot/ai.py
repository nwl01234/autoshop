"""AI-консультант: любой OpenAI-совместимый API (OpenAI, ollama, прокси vLLM)."""
import httpx

from . import config as cfg


async def chat(messages: list, temperature: float = 0.5) -> str | None:
    if not cfg.OPENAI_API_KEY:
        return None
    try:
        async with httpx.AsyncClient(timeout=40) as c:
            r = await c.post(f"{cfg.OPENAI_BASE_URL}/chat/completions",
                             headers={"Authorization": f"Bearer {cfg.OPENAI_API_KEY}"},
                             json={"model": cfg.OPENAI_MODEL, "temperature": temperature, "messages": messages})
            r.raise_for_status()
            return r.json()["choices"][0]["message"]["content"].strip()
    except Exception as e:
        print(f"[ai] {e.__class__.__name__} — отвечаю без AI")
        return None
