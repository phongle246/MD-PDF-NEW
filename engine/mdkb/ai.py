"""AI provider abstraction (OpenAI, Gemini). AI never supplies canonical textbook text.
Only small, explicit payloads are sent; callers can preview exactly what would be sent."""
from __future__ import annotations
import json, urllib.request, urllib.error

PROVIDERS = {
    "openai": {"label": "OpenAI", "host": "api.openai.com", "default_model": "gpt-4o-mini"},
    "gemini": {"label": "Google Gemini", "host": "generativelanguage.googleapis.com", "default_model": "gemini-1.5-flash"},
}


class AIError(Exception):
    pass


class Provider:
    def __init__(self, name: str, api_key: str, model: str | None = None):
        if name not in PROVIDERS:
            raise AIError(f"Unknown provider '{name}'")
        if not api_key:
            raise AIError("API key is empty. Enter it in Settings.")
        self.name, self.key, self.model = name, api_key, model or PROVIDERS[name]["default_model"]
        self.requests = 0

    def describe_payload(self, prompt: str) -> dict:
        """What the user sees before anything is sent."""
        return {"provider": PROVIDERS[self.name]["label"], "host": PROVIDERS[self.name]["host"], "model": self.model,
                "characters": len(prompt), "preview": prompt[:400]}

    def complete(self, prompt: str, system: str = "", timeout: int = 60) -> str:
        self.requests += 1
        if self.name == "openai":
            url = "https://api.openai.com/v1/chat/completions"
            body = {"model": self.model, "messages": ([{"role": "system", "content": system}] if system else []) + [{"role": "user", "content": prompt}], "temperature": 0}
            headers = {"Authorization": f"Bearer {self.key}", "Content-Type": "application/json"}
        else:
            url = f"https://generativelanguage.googleapis.com/v1beta/models/{self.model}:generateContent"
            body = {"contents": [{"parts": [{"text": (system + "\n\n" if system else "") + prompt}]}], "generationConfig": {"temperature": 0}}
            headers = {"x-goog-api-key": self.key, "Content-Type": "application/json"}   # key only sent to Google
        req = urllib.request.Request(url, json.dumps(body).encode(), headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:
                data = json.loads(r.read())
        except urllib.error.HTTPError as e:
            msg = {401: "Invalid API key", 403: "API key not permitted", 429: "Rate limit reached - retry later"}.get(e.code, f"HTTP {e.code}")
            raise AIError(msg)
        except Exception as e:
            raise AIError(f"Network error: {e}")
        try:
            if self.name == "openai":
                return data["choices"][0]["message"]["content"]
            return data["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            raise AIError("Unexpected response from provider")

    def test_connection(self) -> str:
        return self.complete("Reply with the single word: ok").strip()


def classify_terms(provider: Provider, terms: list[str]) -> dict:
    """Send ONLY a list of heading terms (no textbook paragraphs). Returns {term: category}."""
    prompt = ("Classify each term as one of: disease, drug, sign_symptom, other. Return JSON object term->category only.\n" + json.dumps(terms[:300]))
    out = provider.complete(prompt, system="You output strict JSON only.")
    out = out.strip().strip("`")
    if out.startswith("json"):
        out = out[4:]
    try:
        return json.loads(out)
    except Exception:
        raise AIError("AI returned invalid JSON")
