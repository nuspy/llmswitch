# llmswitch

Provider LLM locali e remoti, con la VRAM presa sul serio.

Nasce da un problema concreto: su una macchina con una GPU sola, **in memoria ci
sta un modello grande per volta**. Caricarne un secondo accanto al primo non
"raddoppia l'occupazione" — fallisce, e porta giù la conversazione in corso. Da
qui una regola:

> Un modello già caricato si usa e basta. Se non c'è nulla in memoria, si carica
> quello scelto.

Il resto della libreria esiste per rendere quella regola affidabile.

## Perché non basta chiamare `/v1/models`

Trappole vere, già disinnescate qui dentro:

| trappola | cosa succede senza |
|---|---|
| `GET /v1/models` di LM Studio elenca il catalogo su **disco**, non la memoria (16 voci contro 1) | il modello scelto risulta "già caldo", viene passato all'API e caricato JIT accanto a quello vero → VRAM esaurita |
| durante il caricamento lo stato è `loading`, non `loaded` | letto come "memoria vuota", parte un **secondo** caricamento accanto a quello in arrivo |
| gli id hanno il suffisso di quantizzazione (`nome@iq4_nl`), le GUI mostrano il nome nudo | `lms load` prende "the first one" e l'API risolve a un'altra variante, caricandola |
| `lms ps` / `lms ls` **avviano LM Studio** se è spento (sono client dell'app) | aprire un menu accende l'applicazione |

La fonte giusta per lo stato è `/api/v0/models`, che per ogni voce dà `state`.

## Uso

```python
from llmswitch import LocalEngines, LmStudioConfig

eng = LocalEngines(LmStudioConfig(model="qwen3.6-35b-a3b"))
r = eng.resolve()
print(r.backend, r.model, r.reason)
# lmstudio qwen3.6-35b-a3b-uncensored-hauhaucs-aggressive@iq4_nl  modello già in memoria

from openai import OpenAI
client = OpenAI(base_url=r.base_url, api_key="local")
client.chat.completions.create(model=r.model, messages=[...])
```

`resolve()` non carica mai niente di nascosto:

- un modello caldo (in **qualunque** backend, LM Studio o vLLM) viene usato così com'è;
- se un caricamento è in corso, lo aspetta invece di affiancargliene un altro;
- prima di caricare verifica che il modello **entri** nella VRAM libera;
- dopo il caricamento rilegge cosa c'è davvero in memoria e restituisce quell'id.

Lo scarico avviene solo se lo chiedi tu, con `unload()` o `swap()`.

### Menu per una GUI

```python
for m in eng.menu():
    print(m["label"], "← in memoria" if m["loaded"] else "")
# qwen3.6-35b-a3b-…@iq4_nl · IQ4_NL · 19,3 GB ← in memoria
# gemma-4-12b-obliterated · Q8_0 · 11,8 GB
```

Quantizzazione e peso servono: lo stesso modello sta su disco in più varianti,
e col solo nome non si sa quale si sta scegliendo né quanta VRAM chiede.

### Registro di provider

```python
from llmswitch import Registry

reg = Registry(app_name="mioprogetto")
reg.add({"type": "zai_coding", "api_key": "..."})
reg.assign("chat", "zai_coding")
base, key, model = reg.endpoint_for("chat")
```

Il file del registro sta **fuori** dalla cartella del progetto
(`%LOCALAPPDATA%\<app>\llm_providers.json`): contiene chiavi API, e i file di
configurazione dei progetti finiscono nei commit. `public()` restituisce il
registro senza segreti, per una GUI.

## Provider supportati

| famiglia | tipi | autenticazione |
|---|---|---|
| locali | `lmstudio`, `vllm` | nessuna |
| API a consumo | `openai`, `claude`, `gemini`, `grok`, `zai`, `zai_coding`, `nvidia`, `openai_compat` | API key (Gemini e Grok anche OAuth) |
| abbonamento via CLI ufficiale | `codex_cli`, `claude_cli`, `antigravity` | login del CLI |

### Sull'autenticazione (verificato ad agosto 2026)

- **OAuth utente**: xAI/Grok (OAuth 2.0 + PKCE) e Google.
- **Solo API key**: Anthropic vieta dal 4 aprile 2026 i token OAuth delle
  subscription fuori dai propri client; la "Sign in with ChatGPT" di OpenAI non
  fa pagare le chiamate all'abbonamento dell'utente.
- **Abbonamento via CLI**: è l'unico modo conforme di far pagare le chiamate a
  un abbonamento — il client ufficiale è autorizzato, un'app terza con le stesse
  credenziali no.

I provider CLI sono **agenti**, non completion. Misurato su una macchina reale,
`"rispondi solo ok"`:

| provider | tempo |
|---|---|
| LM Studio (locale) | 0,47 s |
| Claude Code CLI | 5,7 s |
| Antigravity (`agy`) | 11,0 s |
| Codex CLI | 14,2 s (14.161 token) |

Per questo sono marcati `realtime: false`: vanno bene in differita, non per la
voce in tempo reale.

## Installazione

```bash
pip install git+https://github.com/nuspy/llmswitch.git
pip install "llmswitch[flask]"   # con il blueprint pronto
```

Dipendenza obbligatoria: `requests`. Flask solo se usi `llmswitch.flask`.

## Licenza

MIT.
