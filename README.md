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
headers = reg.headers_for("chat")   # con quello che il servizio pretende in più
```

Il file del registro sta **fuori** dalla cartella del progetto
(`%LOCALAPPDATA%\<app>\llm_providers.json`): contiene chiavi API, e i file di
configurazione dei progetti finiscono nei commit. `public()` restituisce il
registro senza segreti, per una GUI.

Chi sceglie il fornitore per conto proprio — nella configurazione di un progetto,
per dire — ma non vuole tenersi le chiavi in casa, usa `api_key_for(tipo)` e
`set_api_key_for(tipo, chiave)`: la scelta di *cosa* usare resta sua, qui ci sono
solo le credenziali.

`endpoint_for(consumer, carica=False)` fa la stessa cosa **senza mai caricare un
modello**: la regola della VRAM si limita a guardare cosa c'è in memoria. Serve ai
programmi che non devono prendersi la VRAM di iniziativa propria — quella
decisione è dell'utente, non dell'applicazione.

## Provider supportati

| famiglia | tipi | autenticazione |
|---|---|---|
| locali | `lmstudio`, `vllm`, `llamacpp` | nessuna |
| API a consumo | `openai`, `claude`, `gemini`, `grok`, `qwen`, `zai`, `zai_coding`, `nvidia`, `deepseek`, `kimi`, `nebius`, `unsloth`, `openai_compat` | API key (Gemini e Grok anche OAuth) |
| abbonamento via HTTP | `codex_oauth`, `qwen_oauth`, `grok_oauth`, `gemini_oauth` | token dello strumento ufficiale |
| abbonamento via CLI | `codex_cli`, `claude_cli`, `antigravity` | login del CLI |

## Abbonamenti

Chi ha ChatGPT Plus, SuperGrok o un piano Qwen sta già pagando delle richieste.
`llmswitch.oauth` le riusa **senza far rifare il login**: legge le credenziali
dello strumento ufficiale già autenticato, e le rinnova quando scadono.

```python
from llmswitch import panoramica, intestazioni

for s in panoramica():
    print(s["label"], "pronto" if s["utilizzabile"] else s["impedimento"] or "da collegare")

testa = intestazioni("codex_oauth")   # Authorization + ChatGPT-Account-Id
```

La cascata, per ogni abbonamento:

1. le credenziali dello strumento ufficiale ci sono e sono buone → si usano;
2. ci sono ma sono scadute → si rinnovano col `refresh_token`;
3. non ci sono → si fa il login da qui, dove il provider espone il flusso a codice;
4. dove non lo espone, l'errore dice quale comando lanciare.

**Il file dello strumento ufficiale viene riscritto** dopo un rinnovo. Non è una
comodità: molti servizi ruotano anche il `refresh_token`, e tenendo il nuovo solo
per noi il `codex` o il `gemini` dell'utente si ritroverebbe con una credenziale
morta. La riscrittura è atomica e la prima volta lascia una copia accanto
all'originale.

Ci si presenta sempre come `llmswitch/<versione>`. Fingersi il client ufficiale
altrui per aggirarne i controlli è ciò che fa sospendere gli account.

## Agenti CLI

`llmswitch.agents` esegue gli strumenti che non hanno un endpoint:

```python
from llmswitch import genera, installati

print(installati())                      # ['claude_cli', 'codex_cli', 'antigravity']
testo = genera("claude_cli", "Traduci in inglese: ...")
```

Ogni agente gira in una **cartella vuota**, non in quella di chi lo chiama:
leggono i file intorno a sé per farsi un'idea del contesto, e per scrivere un
testo non serve. Con `cartella=` glieli si dà apposta.

### Sull'autenticazione (verificato ad agosto 2026)

- **Solo API key**: Anthropic vieta dal 4 aprile 2026 i token OAuth delle
  subscription fuori dai propri client.
- **Gemini col piano individuale: chiuso.** Le credenziali di `gemini-cli` si
  rinnovano ancora, ma `loadCodeAssist` risponde `UNSUPPORTED_CLIENT` per il
  `free-tier` e rimanda ad Antigravity; resta solo lo `standard-tier`, a pagamento
  e con un progetto Google Cloud. Un adattatore non servirebbe: è il server a
  rifiutare, non il formato. Per questo `gemini_oauth` porta un `impedimento` e
  non risulta mai `utilizzabile`.
- **Abbonamento via CLI**: è l'unico modo conforme di far pagare le chiamate a
  un abbonamento — il client ufficiale è autorizzato, un'app terza con le stesse
  credenziali no.

I provider CLI sono **agenti**, non completion. Misurato su una macchina reale,
`"rispondi solo ok"`:

| provider | tempo |
|---|---|
| LM Studio (locale) | 0,47 s |
| Claude Code (`claude -p`) | 4,8 s |
| Antigravity (`agy -p`) | 7,7 s |
| Codex (`codex exec`) | 15,5 s — 14.237 token per due lettere |

Per questo sono marcati `realtime: false`: vanno bene in differita, non per la
voce in tempo reale né per un lavoro che ne fa centinaia di seguito.

Due dettagli che sembrano cavilli e non lo sono: `agy -p` vuole il testo come
**argomento** (da stdin stampa l'aiuto ed esce con codice zero — un fallimento
silenzioso), e la risposta di `codex` si legge con `-o <file>`, perché su stdout
mescola ganci e contatori al testo.

## Installazione

```bash
pip install git+https://github.com/nuspy/llmswitch.git
pip install "llmswitch[flask]"   # con il blueprint pronto
```

Dipendenza obbligatoria: `requests`. Flask solo se usi `llmswitch.flask`.

## Licenza

MIT.
