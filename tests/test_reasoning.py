"""Test del livello di ragionamento: dialetti, niente conflitti, ereditarietà, migrazione dal vecchio booleano,
registro, blueprint e componente di riferimento.

Come gli altri test del registro: un file in una cartella temporanea, mai quello vero dell'utente; nessuna rete.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from llmswitch import reasoning
from llmswitch.providers import Registry


@pytest.fixture()
def registro(tmp_path: Path) -> Registry:
    return Registry(path=tmp_path / "providers.json")


# --- il punto unico: livello → parametri ------------------------------------------------------------------------


def test_i_dialetti_dipendono_dal_tipo() -> None:
    assert reasoning.dialect("nebius", "openai") == reasoning.EFFORT
    assert reasoning.dialect("lmstudio", "lmstudio") == reasoning.EFFORT
    assert reasoning.dialect("nvidia", "openai") == reasoning.THINKING
    assert reasoning.dialect("claude", "anthropic") == reasoning.ANTHROPIC
    assert reasoning.dialect("claude_cli", "cli") == reasoning.NO_DIALECT
    assert reasoning.dialect("gemini_oauth", "cloudcode") == reasoning.NO_DIALECT


def test_effort_manda_solo_reasoning_effort() -> None:
    base = {"reasoning_budget": 16384, "chat_template_kwargs": {"enable_thinking": True, "altro": 1},
            "thinking": {"type": "enabled"}, "top_p": 0.9}
    params = reasoning.params_for("none", reasoning.EFFORT, base)
    assert params == {"reasoning_effort": "none", "chat_template_kwargs": {"altro": 1}, "top_p": 0.9}


def test_thinking_manda_enable_thinking_e_il_budget() -> None:
    acceso = reasoning.params_for("high", reasoning.THINKING, {"reasoning_effort": "low"})
    assert acceso == {"chat_template_kwargs": {"enable_thinking": True}, "reasoning_budget": 8192}
    spento = reasoning.params_for("none", reasoning.THINKING, {"reasoning_budget": 9000})
    assert spento == {"chat_template_kwargs": {"enable_thinking": False}}


def test_anthropic_usa_il_budget_del_thinking() -> None:
    assert reasoning.params_for("minimal", reasoning.ANTHROPIC) == {
        "thinking": {"type": "enabled", "budget_tokens": reasoning.BUDGET_MIN_ANTHROPIC}}
    assert reasoning.params_for("xhigh", reasoning.ANTHROPIC)["thinking"]["budget_tokens"] == 16384
    assert reasoning.params_for("none", reasoning.ANTHROPIC, {"reasoning_effort": "high"}) == {}


@pytest.mark.parametrize("livello", reasoning.LEVELS)
@pytest.mark.parametrize("dialetto", [reasoning.EFFORT, reasoning.THINKING, reasoning.ANTHROPIC])
def test_mai_due_dialetti_insieme(livello: str, dialetto: str) -> None:
    base = {"reasoning_effort": "max", "reasoning_budget": 1, "chat_template_kwargs": {"enable_thinking": True},
            "thinking": {"type": "enabled", "budget_tokens": 1}}
    params = reasoning.params_for(livello, dialetto, base)
    effort = "reasoning_effort" in params
    thinking = "reasoning_budget" in params or "enable_thinking" in (params.get("chat_template_kwargs") or {})
    claude = "thinking" in params
    assert sum([effort, thinking, claude]) <= 1, params


def test_un_livello_sconosciuto_e_un_errore() -> None:
    with pytest.raises(ValueError):
        reasoning.params_for("altissimo", reasoning.EFFORT)
    assert reasoning.normalize("ALTISSIMO") == reasoning.INHERIT
    assert reasoning.normalize(" High ") == "high"


def test_la_richiesta_vince_sul_fornitore_e_inherit_non_manda_niente() -> None:
    assert reasoning.resolve("medium", "inherit") == "medium"
    assert reasoning.resolve("medium", "none") == "none"
    assert reasoning.resolve("inherit", "inherit") is None
    assert reasoning.resolve(None, "") is None
    # Tutto eredita: i parametri di base restano come sono.
    assert reasoning.params_for(None, reasoning.EFFORT, {"reasoning_effort": "low"}) == {"reasoning_effort": "low"}


def test_il_tetto_lascia_spazio_al_ragionamento() -> None:
    assert reasoning.max_tokens_for(500, "none") == 500
    assert reasoning.max_tokens_for(500, None) == 500
    assert reasoning.max_tokens_for(2600, "xhigh") == 2600 + reasoning.MARGIN["xhigh"]
    assert reasoning.max_tokens_for(500, "low", {"reasoning_budget": 9000}) == 9500
    assert reasoning.max_tokens_for(500, "high", margins={"high": 1000}) == 1500


def test_le_scelte_del_menu() -> None:
    scelte = reasoning.choices("it")
    assert [s["value"] for s in scelte] == list(reasoning.CHOICES)
    assert scelte[0] == {"value": "inherit", "label": "Eredita dal fornitore"}
    assert [s["value"] for s in reasoning.choices("en", inherit=False)] == list(reasoning.LEVELS)
    assert reasoning.choices("xx")[1]["label"] == "None"          # lingua sconosciuta: inglese


# --- il registro ------------------------------------------------------------------------------------------------


def test_il_fornitore_ha_un_livello_e_la_richiesta_lo_eredita(registro: Registry) -> None:
    voce = registro.add({"type": "nebius", "api_key": "k", "model": "m", "reasoning_level": "medium"})
    assert voce["reasoning_level"] == "medium"
    registro.assign("chat", voce["id"])
    r = registro.reasoning_for("chat")
    assert r == {"level": "medium", "dialect": "effort", "params": {"reasoning_effort": "medium"}, "max_tokens": None}
    r = registro.reasoning_for("chat", "none", max_tokens=400)
    assert r["params"] == {"reasoning_effort": "none"} and r["max_tokens"] == 400
    r = registro.reasoning_for("chat", "xhigh", max_tokens=2600)
    assert r["max_tokens"] == 2600 + reasoning.MARGIN["xhigh"]


def test_un_fornitore_senza_livello_non_manda_niente(registro: Registry) -> None:
    voce = registro.add({"type": "lmstudio", "model": "m"})
    assert voce["reasoning_level"] == "inherit"
    registro.assign("chat", voce["id"])
    assert registro.reasoning_for("chat", base={"top_p": 1})["params"] == {"top_p": 1}


def test_nim_e_claude_parlano_il_loro_dialetto(registro: Registry) -> None:
    nim = registro.add({"type": "nvidia", "api_key": "k", "reasoning_level": "low"})
    registro.assign("chat", nim["id"])
    assert registro.reasoning_for("chat")["params"] == {
        "chat_template_kwargs": {"enable_thinking": True}, "reasoning_budget": 1024}
    claude = registro.add({"type": "claude", "api_key": "k"})
    registro.assign("chat", claude["id"])
    assert registro.reasoning_for("chat", "high")["params"] == {"thinking": {"type": "enabled", "budget_tokens": 8192}}


def test_un_cli_non_ha_parametri(registro: Registry) -> None:
    voce = registro.add({"type": "claude_cli", "reasoning_level": "high"})
    registro.assign("chat", voce["id"])
    r = registro.reasoning_for("chat")
    assert r["dialect"] == "none" and r["params"] == {}


def test_livelli_sbagliati_rifiutati(registro: Registry) -> None:
    with pytest.raises(ValueError):
        registro.add({"type": "nebius", "reasoning_level": "tantissimo"})
    voce = registro.add({"type": "nebius"})
    with pytest.raises(ValueError):
        registro.update(voce["id"], {"reasoning_level": "tantissimo"})
    registro.assign("chat", voce["id"])
    with pytest.raises(ValueError):
        registro.reasoning_for("chat", "tantissimo")
    assert registro.update(voce["id"], {"reasoning_level": "XHIGH"})["reasoning_level"] == "xhigh"
    assert registro.update(voce["id"], {"reasoning_level": ""})["reasoning_level"] == "inherit"


def test_migrazione_dal_vecchio_booleano(registro: Registry) -> None:
    registro.path.write_text(json.dumps({"version": 1, "assignments": {"chat": "a"}, "providers": [
        {"id": "a", "type": "nebius", "ragiona": True},
        {"id": "b", "type": "nvidia", "reasoning": False},
        {"id": "c", "type": "lmstudio", "thinking": True, "reasoning_level": "low"},   # il livello vince
        {"id": "d", "type": "nebius"},
    ]}), encoding="utf-8")
    voci = {p["id"]: p for p in registro.load()["providers"]}
    assert voci["a"]["reasoning_level"] == "medium" and "ragiona" not in voci["a"]
    assert voci["b"]["reasoning_level"] == "none" and "reasoning" not in voci["b"]
    assert voci["c"]["reasoning_level"] == "low" and "thinking" not in voci["c"]
    assert "reasoning_level" not in voci["d"]
    assert registro.reasoning_for("chat")["params"] == {"reasoning_effort": "medium"}
    # Alla prima scrittura la voce si salva nella forma nuova.
    registro.update("d", {"name": "Nebius"})
    salvato = json.loads(registro.path.read_text(encoding="utf-8"))
    assert all("ragiona" not in p and "reasoning" not in p for p in salvato["providers"])
    # Anche `add` accetta il vecchio booleano.
    assert registro.add({"type": "nebius", "ragiona": False})["reasoning_level"] == "none"


def test_public_espone_livelli_e_dialetti_senza_segreti(registro: Registry) -> None:
    registro.add({"type": "nebius", "api_key": "segreta", "reasoning_level": "high"})
    pub = registro.public()
    assert [l["value"] for l in pub["reasoning_levels"]] == list(reasoning.CHOICES)
    voce = pub["providers"][0]
    assert voce["reasoning_level"] == "high" and voce["reasoning_dialect"] == "effort"
    assert "segreta" not in json.dumps(pub)
    tipi = {t["type"]: t for t in pub["types"]}
    assert tipi["nvidia"]["reasoning_dialect"] == "thinking" and tipi["codex_cli"]["reasoning_dialect"] == "none"


# --- il blueprint e il componente ------------------------------------------------------------------------------


@pytest.fixture()
def client(registro: Registry):
    flask = pytest.importorskip("flask")
    from llmswitch.flask import make_blueprint

    app = flask.Flask(__name__)
    app.register_blueprint(make_blueprint(registro))
    return app.test_client()


def test_il_blueprint_espone_i_livelli(client) -> None:
    r = client.get("/llm/reasoning-levels")
    assert r.status_code == 200
    assert [l["value"] for l in r.get_json()["levels"]] == list(reasoning.CHOICES)
    assert r.get_json()["default"] == "inherit"
    r = client.get("/llm/reasoning-levels?lang=en&inherit=0")
    assert r.get_json()["levels"][0] == {"value": "none", "label": "None"}


def test_il_blueprint_salva_il_livello_del_fornitore(client) -> None:
    pid = client.post("/llm/providers", json={"type": "nebius", "reasoning_level": "low"}).get_json()["providers"][0]["id"]
    assert client.put(f"/llm/providers/{pid}", json={"reasoning_level": "xhigh"}).status_code == 200
    assert client.get("/llm/providers").get_json()["providers"][0]["reasoning_level"] == "xhigh"
    assert client.put(f"/llm/providers/{pid}", json={"reasoning_level": "boh"}).status_code == 400
    assert client.post("/llm/providers", json={"type": "nebius", "reasoning_level": "boh"}).status_code == 400


def test_il_componente_di_riferimento_c_e_ed_e_servito(client) -> None:
    sorgente = Path(__file__).resolve().parents[1] / "llmswitch" / "static" / "livello-ragionamento.js"
    testo = sorgente.read_text(encoding="utf-8")
    assert "export function menuRagionamento" in testo and "label.htmlFor" in testo
    assert "min-height: 48px" in testo and "aria-describedby" in testo
    for scelta in reasoning.CHOICES:
        assert f'value: "{scelta}"' in testo           # le scelte di ripiego coincidono con quelle del server
    r = client.get("/llm/static/livello-ragionamento.js")
    assert r.status_code == 200 and b"menuRagionamento" in r.data
    r.close()
