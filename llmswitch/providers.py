"""Registro dei provider LLM: locali (LM Studio, vLLM), remoti a chiave
(OpenAI, Claude, Gemini, Grok, Z.AI, NVIDIA, generici OpenAI-compatibili) e ad
abbonamento tramite il CLI ufficiale (Codex, Claude Code, Antigravity).

Un `Registry` è un file JSON con i provider configurati e a chi sono assegnati:

    reg = Registry(app_name="mioprogetto")
    reg.add({"type": "zai_coding", "api_key": "..."})
    reg.assign("chat", "zai_coding")
    base, key, model = reg.endpoint_for("chat")

Il file sta FUORI dalla cartella del progetto (in `%LOCALAPPDATA%` o
`~/.config`): contiene chiavi API, e i file di configurazione dei progetti
finiscono nei commit.

Sull'autenticazione, verificato ad agosto 2026:

- **OAuth utente**: xAI/Grok (OAuth 2.0 + PKCE su accounts.x.ai) e Google
  (authorization code, access token 24h + refresh token).
- **Solo API key**: Anthropic vieta dal 4 aprile 2026 i token OAuth delle
  subscription fuori dai propri client; la "Sign in with ChatGPT" di OpenAI non
  fa pagare le chiamate all'abbonamento dell'utente.
- **Abbonamento via CLI ufficiale**: è l'unico modo conforme di far pagare le
  chiamate a un abbonamento — il client ufficiale è autorizzato, un'app terza
  con le stesse credenziali no. Sono però AGENTI, non completion: misurato,
  "rispondi solo ok" costa ~6s a Claude Code, ~11s ad Antigravity e ~14s
  (14k token) a Codex, contro ~0,5s di un modello locale. Vanno bene in
  differita, non per la voce in tempo reale — vedi `realtime`.
"""
from __future__ import annotations

import json
import logging
import os
import shutil
import threading
import time
from pathlib import Path

from .local import LmStudioConfig, LocalEngines, VllmConfig

log = logging.getLogger("llmswitch.providers")

TYPES: dict[str, dict] = {
    "lmstudio": {
        "label": "LM Studio", "auth": "none", "api": "lmstudio", "local": True,
        "base_url": "http://127.0.0.1:1234/v1",
        "note": "Server locale. Il modello già in memoria ha la precedenza.",
    },
    "vllm": {
        "label": "vLLM", "auth": "none", "api": "vllm", "local": True,
        "base_url": "",
        "note": "Istanze del manager vLLM: una viva si riusa com'è.",
    },
    "openai": {
        "label": "OpenAI", "auth": "api_key", "api": "openai", "local": False,
        "base_url": "https://api.openai.com/v1",
        "note": "API key (pay-per-use): l'abbonamento ChatGPT non copre le "
                "chiamate di app terze.",
    },
    "claude": {
        "label": "Claude (Anthropic)", "auth": "api_key", "api": "anthropic",
        "local": False, "base_url": "https://api.anthropic.com/v1",
        "chat_base_url": "https://api.anthropic.com/v1",
        "note": "Solo API key: dal 4 aprile 2026 i token OAuth delle "
                "subscription non valgono fuori dai client Anthropic.",
    },
    "gemini": {
        "label": "Google Gemini", "auth": "either", "api": "gemini",
        "local": False,
        "base_url": "https://generativelanguage.googleapis.com/v1beta",
        "chat_base_url": "https://generativelanguage.googleapis.com/v1beta/openai",
        "note": "API key oppure OAuth Google. Da settembre 2026 le API key "
                "\"standard\" vengono rifiutate: servono le auth key.",
    },
    "grok": {
        "label": "Grok (xAI)", "auth": "either", "api": "openai", "local": False,
        "base_url": "https://api.x.ai/v1",
        "note": "API key, o OAuth 2.0 PKCE per usare l'abbonamento SuperGrok / "
                "X Premium+.",
    },
    # Z.AI ha DUE endpoint con piani e quote separati: stessa chiave non vale
    # su entrambi, quindi sono due provider e non un'opzione dello stesso.
    "zai": {
        "label": "Z.AI (piano API)", "auth": "api_key", "api": "openai",
        "local": False, "base_url": "https://api.z.ai/api/paas/v4",
        "note": "Endpoint generale (GLM), OpenAI-compatibile.",
    },
    "zai_coding": {
        "label": "Z.AI (piano coding)", "auth": "api_key", "api": "openai",
        "local": False, "base_url": "https://api.z.ai/api/coding/paas/v4",
        "note": "Endpoint del piano coding (GLM-5.2 / GLM-5.1): quota separata "
                "da quella del piano API.",
    },
    "nvidia": {
        "label": "NVIDIA NIM", "auth": "api_key", "api": "openai", "local": False,
        "base_url": "https://integrate.api.nvidia.com/v1",
        "note": "Modelli di build.nvidia.com, OpenAI-compatibile.",
    },
    "openai_compat": {
        "label": "Generico (OpenAI-compatibile)", "auth": "api_key",
        "api": "openai", "local": False, "base_url": "",
        "note": "Qualunque server che espone /v1/chat/completions.",
    },
    "codex_cli": {
        "label": "OpenAI via Codex CLI (abbonamento)", "auth": "cli",
        "api": "cli", "local": True, "base_url": "",
        "command": "codex exec --skip-git-repo-check",
        "note": "Abbonamento ChatGPT tramite il CLI ufficiale. ~14s a "
                "risposta: non adatto alla voce.",
    },
    "claude_cli": {
        "label": "Claude via Claude Code (abbonamento)", "auth": "cli",
        "api": "cli", "local": True, "base_url": "", "command": "claude -p",
        "note": "Abbonamento Claude Pro/Max tramite il CLI ufficiale. ~6s.",
    },
    "antigravity": {
        "label": "Gemini via Antigravity CLI (abbonamento)", "auth": "cli",
        "api": "cli", "local": True, "base_url": "", "command": "agy -p",
        "note": "Sostituisce il `gemini` CLI, chiuso agli account individuali "
                "(IneligibleTierError). ~11s a risposta.",
    },
}

#: consumatori predefiniti; ogni progetto può passare i propri
DEFAULT_CONSUMERS = ("chat", "copilot", "embed")


def default_registry_path(app_name: str = "llmswitch") -> Path:
    """File del registro, fuori dalla cartella del progetto."""
    base = os.environ.get("LOCALAPPDATA") or os.environ.get("XDG_CONFIG_HOME") \
        or os.path.join(os.path.expanduser("~"), ".config")
    return Path(base) / app_name / "llm_providers.json"


class Registry:
    """Provider configurati e loro assegnazioni, su file JSON."""

    def __init__(self, path: str | os.PathLike | None = None,
                 app_name: str = "llmswitch",
                 consumers: tuple[str, ...] = DEFAULT_CONSUMERS,
                 engines: LocalEngines | None = None,
                 models_ttl: float = 300.0) -> None:
        self.path = Path(path) if path else default_registry_path(app_name)
        self.consumers = tuple(consumers)
        #: motori locali; se assente se ne crea uno dai default al bisogno
        self.engines = engines
        self._lock = threading.Lock()
        self._models_cache: dict[str, tuple[float, list[dict]]] = {}
        self._ttl = models_ttl

    # --- persistenza -------------------------------------------------------

    def _empty(self) -> dict:
        return {"version": 1, "providers": [], "assignments": {}}

    def load(self) -> dict:
        try:
            data = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return self._empty()
        if not isinstance(data, dict):
            return self._empty()
        data.setdefault("providers", [])
        data.setdefault("assignments", {})
        return data

    def save(self, data: dict) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(json.dumps(data, indent=2, ensure_ascii=False),
                             encoding="utf-8")

    def _find(self, data: dict, pid: str) -> dict | None:
        return next((x for x in data.get("providers", []) if x.get("id") == pid), None)

    # --- motori locali -----------------------------------------------------

    def _engines_for(self, prov: dict) -> LocalEngines:
        """`LocalEngines` configurato con l'endpoint/modello di questo provider."""
        if self.engines is not None:
            return self.engines
        spec = TYPES.get(prov.get("type") or "", {})
        base = (prov.get("base_url") or spec.get("base_url") or "").strip()
        model = (prov.get("model") or "").strip()
        if prov.get("type") == "vllm":
            return LocalEngines(vllm=VllmConfig(manager_url=base or
                                                VllmConfig.manager_url, model=model),
                                prefer="vllm")
        return LocalEngines(LmStudioConfig(base_url=base or
                                           LmStudioConfig.base_url, model=model))

    # --- elenco modelli ----------------------------------------------------

    def models(self, pid: str, force: bool = False) -> list[dict]:
        """Modelli del provider, chiesti al momento (cache per i remoti)."""
        data = self.load()
        prov = self._find(data, pid)
        if prov is None:
            raise KeyError(f"provider sconosciuto: {pid}")
        spec = TYPES.get(prov.get("type") or "", {})
        api = spec.get("api")
        if api == "cli":
            return _cli_models(prov, spec)
        if spec.get("local"):
            eng = self._engines_for(prov)
            return eng.menu("vllm" if prov.get("type") == "vllm" else "lmstudio")
        hit = self._models_cache.get(pid)
        if hit and not force and time.monotonic() - hit[0] < self._ttl:
            return hit[1]
        base = (prov.get("base_url") or spec.get("base_url") or "").strip()
        key = (prov.get("api_key") or "").strip()
        token = ((prov.get("oauth") or {}).get("access_token") or "").strip()
        if api == "anthropic":
            out = _anthropic_models(base, key)
        elif api == "gemini":
            out = _gemini_models(base, key, token)
        else:
            out = _openai_models(base, token or key)
        self._models_cache[pid] = (time.monotonic(), out)
        return out

    def test(self, pid: str) -> dict:
        try:
            found = self.models(pid, force=True)
            return {"ok": True, "models": len(found),
                    "sample": [m["id"] for m in found[:5]]}
        except Exception as e:  # noqa: BLE001 - qualunque errore è un esito
            return {"ok": False, "error": str(e)[:300]}

    # --- uso ---------------------------------------------------------------

    def provider_for(self, consumer: str) -> dict | None:
        data = self.load()
        pid = (data.get("assignments") or {}).get(consumer) or ""
        return self._find(data, pid) if pid else None

    def endpoint_for(self, consumer: str, embedding: bool = False
                     ) -> tuple[str, str, str] | None:
        """(base_url, api_key, modello) per un client OpenAI-compatibile.

        Con `embedding=True` torna `embed_model` invece di `model`: chi
        vettorizza non è chi conversa, e usare lo stesso campo manderebbe un
        LLM alla /embeddings.

        Sui provider LOCALI il modello di chat lo decide `LocalEngines.resolve`,
        con la regola della VRAM: qui torna già risolto."""
        prov = self.provider_for(consumer)
        if prov is None:
            return None
        spec = TYPES.get(prov.get("type") or "", {})
        if embedding:
            modello = (prov.get("embed_model") or "").strip()
            if not modello:
                return None
            base = ((prov.get("base_url") or "").strip()
                    or spec.get("base_url") or "").strip()
            return base, (prov.get("api_key") or "local").strip(), modello
        if spec.get("local") and spec.get("api") != "cli":
            eng = self._engines_for(prov)
            r = eng.resolve("vllm" if prov.get("type") == "vllm" else "lmstudio")
            return r.base_url, (prov.get("api_key") or "local").strip(), r.model
        # Per chattare serve l'endpoint OpenAI-compatibile, che su Gemini e
        # Anthropic non è quello che elenca i modelli.
        base = ((prov.get("base_url") or "").strip()
                or spec.get("chat_base_url") or spec.get("base_url") or "").strip()
        key = ((prov.get("oauth") or {}).get("access_token")
               or prov.get("api_key") or "").strip()
        return base, key, (prov.get("model") or "").strip()

    # --- modifica ----------------------------------------------------------

    def add(self, prov: dict) -> dict:
        tipo = (prov.get("type") or "").strip()
        if tipo not in TYPES:
            raise ValueError(f"tipo sconosciuto: {tipo}")
        with self._lock:
            data = self.load()
            pid = (prov.get("id") or "").strip() or self._new_id(data, tipo)
            if self._find(data, pid) is not None:
                raise ValueError(f"esiste già un provider con id '{pid}'")
            voce = {"id": pid, "type": tipo,
                    "name": (prov.get("name") or TYPES[tipo]["label"]).strip(),
                    "base_url": (prov.get("base_url") or "").strip(),
                    "model": (prov.get("model") or "").strip(),
                    # Il modello che VETTORIZZA è un'altra cosa da quello che
                    # conversa: nello stesso campo, assegnare un provider sia
                    # alla chat sia al RAG manderebbe un LLM alla /embeddings.
                    "embed_model": (prov.get("embed_model") or "").strip(),
                    "api_key": (prov.get("api_key") or "").strip()}
            data["providers"].append(voce)
            self.save(data)
            return voce

    @staticmethod
    def _new_id(data: dict, tipo: str) -> str:
        esistenti = {p.get("id") for p in data.get("providers", [])}
        if tipo not in esistenti:
            return tipo
        n = 2
        while f"{tipo}-{n}" in esistenti:
            n += 1
        return f"{tipo}-{n}"

    def update(self, pid: str, campi: dict) -> dict:
        with self._lock:
            data = self.load()
            prov = self._find(data, pid)
            if prov is None:
                raise KeyError(pid)
            for k in ("name", "base_url", "model", "embed_model", "api_key"):
                if k in campi:
                    prov[k] = (campi[k] or "").strip()
            self.save(data)
            self._models_cache.pop(pid, None)
            return prov

    def remove(self, pid: str) -> None:
        with self._lock:
            data = self.load()
            data["providers"] = [p for p in data.get("providers", [])
                                 if p.get("id") != pid]
            # Nessun consumatore deve restare agganciato a un provider sparito.
            data["assignments"] = {k: v for k, v in (data.get("assignments") or {}).items()
                                   if v != pid}
            self.save(data)
            self._models_cache.pop(pid, None)

    def assign(self, consumer: str, pid: str | None) -> dict:
        if consumer not in self.consumers:
            raise ValueError(f"consumatore sconosciuto: {consumer}")
        with self._lock:
            data = self.load()
            if pid:
                if self._find(data, pid) is None:
                    raise KeyError(pid)
                data.setdefault("assignments", {})[consumer] = pid
            else:
                data.get("assignments", {}).pop(consumer, None)
            self.save(data)
            return data.get("assignments", {})

    # --- per la GUI --------------------------------------------------------

    def public(self) -> dict:
        """Registro SENZA segreti: le chiavi diventano un booleano."""
        data = self.load()
        fuori = []
        for p in data.get("providers", []):
            spec = TYPES.get(p.get("type") or "", {})
            oauth = p.get("oauth") or {}
            fuori.append({
                "id": p.get("id"), "type": p.get("type"), "name": p.get("name"),
                "base_url": p.get("base_url") or spec.get("base_url", ""),
                "model": p.get("model", ""),
                "embed_model": p.get("embed_model", ""),
                "has_key": bool((p.get("api_key") or "").strip()),
                "oauth_account": oauth.get("account", ""),
                "oauth_connected": bool(oauth.get("access_token")),
                "local": bool(spec.get("local")),
                "auth": spec.get("auth", "api_key"),
                "label": spec.get("label", p.get("type")),
                "note": spec.get("note", ""),
                "realtime": spec.get("api") != "cli",
                "command": spec.get("command", ""),
            })
        return {"providers": fuori, "assignments": data.get("assignments", {}),
                "types": [{"type": k, "realtime": v.get("api") != "cli",
                           **{kk: vv for kk, vv in v.items()
                              if kk in ("label", "auth", "local", "base_url", "note")}}
                          for k, v in TYPES.items()],
                "consumers": list(self.consumers)}


# --- elenchi modelli per famiglia di API --------------------------------------

def _openai_models(base_url: str, api_key: str, extra: dict | None = None) -> list[dict]:
    import requests
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    headers.update(extra or {})
    r = requests.get(base_url.rstrip("/") + "/models", headers=headers, timeout=20)
    r.raise_for_status()
    return [{"id": str(m["id"]), "label": str(m["id"])}
            for m in (r.json() or {}).get("data") or [] if m.get("id")]


def _anthropic_models(base_url: str, api_key: str) -> list[dict]:
    import requests
    r = requests.get(base_url.rstrip("/") + "/models", timeout=20, headers={
        "x-api-key": api_key, "anthropic-version": "2023-06-01"})
    r.raise_for_status()
    return [{"id": str(m["id"]), "label": str(m.get("display_name") or m["id"])}
            for m in (r.json() or {}).get("data") or [] if m.get("id")]


def _gemini_models(base_url: str, api_key: str, token: str = "") -> list[dict]:
    import requests
    headers = {"Authorization": f"Bearer {token}"} if token else {}
    params = {} if token else {"key": api_key}
    r = requests.get(base_url.rstrip("/") + "/models", headers=headers,
                     params=params, timeout=20)
    r.raise_for_status()
    out = []
    for m in (r.json() or {}).get("models") or []:
        name = str(m.get("name") or "").split("/")[-1]
        metodi = m.get("supportedGenerationMethods") or []
        # Solo chi sa generare testo: l'elenco include embedding e altro, che
        # in un menu "modello di chat" sono rumore.
        if name and (not metodi or "generateContent" in metodi):
            out.append({"id": name, "label": str(m.get("displayName") or name)})
    return out


def _cli_models(prov: dict, spec: dict) -> list[dict]:
    """Provider "CLI ufficiale": nessun endpoint da interrogare, si verifica
    solo che il comando esista."""
    cmd = (prov.get("base_url") or spec.get("command") or "").split()
    exe = cmd[0] if cmd else ""
    if not exe or shutil.which(exe) is None:
        raise RuntimeError(f"comando '{exe or '?'}' non trovato nel PATH")
    scelto = (prov.get("model") or "").strip()
    return [{"id": scelto or "default",
             "label": scelto or "(quello configurato nel CLI)"}]
