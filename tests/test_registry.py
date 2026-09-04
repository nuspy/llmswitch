"""Test del registro dei provider: assegnazioni ed endpoint.

Il registro vive su file e contiene chiavi API: ogni test usa un file suo in una cartella
temporanea, mai quello vero dell'utente.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from llmswitch import TYPES
from llmswitch.local import LocalEngines, LmStudioConfig, Resolution
from llmswitch.providers import Registry


@pytest.fixture()
def registro(tmp_path: Path) -> Registry:
    return Registry(path=tmp_path / "providers.json")


def test_an_unassigned_consumer_has_no_endpoint(registro: Registry) -> None:
    assert registro.endpoint_for("chat") is None


def test_a_remote_provider_gives_back_what_was_configured(registro: Registry) -> None:
    voce = registro.add({"type": "deepseek", "api_key": "sk-prova", "model": "deepseek-chat"})
    registro.assign("chat", voce["id"])
    base, chiave, modello = registro.endpoint_for("chat")
    assert base == TYPES["deepseek"]["base_url"]
    assert (chiave, modello) == ("sk-prova", "deepseek-chat")


def test_the_embedding_endpoint_uses_its_own_model(registro: Registry) -> None:
    """Chi vettorizza non è chi conversa: usare lo stesso campo manderebbe un LLM a /embeddings."""
    voce = registro.add({"type": "lmstudio", "model": "un-llm", "embed_model": "un-embedder"})
    registro.assign("embed", voce["id"])
    assert registro.endpoint_for("embed", embedding=True)[2] == "un-embedder"


class _MotoriFinti(LocalEngines):
    """Motori che raccontano cosa è stato chiesto loro, senza toccare niente di vero."""

    def __init__(self, caldo: Resolution | None) -> None:
        super().__init__(LmStudioConfig(base_url="http://127.0.0.1:1234/v1"))
        self.caldo, self.chiamate = caldo, []

    def warm(self, prefer: str = "") -> Resolution | None:
        self.chiamate.append("warm")
        return self.caldo

    def resolve(self, prefer: str = "") -> Resolution:
        self.chiamate.append("resolve")
        return self.caldo or Resolution("lmstudio", self.lm.base_url, "caricato-adesso", "caricato")


def test_without_permission_no_model_is_ever_loaded(tmp_path: Path) -> None:
    """`carica=False` guarda e basta: la VRAM è dell'utente, non dell'applicazione.

    È il caso di un programma che macina lavori in coda per ore: se si mettesse a caricare il
    modello che ha scritto in configurazione, farebbe comparire un secondo modello in memoria
    accanto a quello che l'utente aveva scelto, e il primo scivolerebbe nella memoria condivisa.
    """
    motori = _MotoriFinti(caldo=None)
    reg = Registry(path=tmp_path / "p.json", engines=motori)
    reg.assign("chat", reg.add({"type": "lmstudio", "model": "quello-in-configurazione"})["id"])

    base, _, modello = reg.endpoint_for("chat", carica=False)
    assert motori.chiamate == ["warm"], "non si chiama resolve: caricherebbe"
    assert modello == "quello-in-configurazione", "a memoria vuota resta la configurazione"
    assert base


def test_without_permission_a_live_model_still_wins(tmp_path: Path) -> None:
    """Guardare e basta non vuol dire ignorare: se c'è un modello vivo, si parla con lui."""
    motori = _MotoriFinti(Resolution("lmstudio", "http://127.0.0.1:1234/v1", "quello-vivo", ""))
    reg = Registry(path=tmp_path / "p.json", engines=motori)
    reg.assign("chat", reg.add({"type": "lmstudio", "model": "quello-in-configurazione"})["id"])

    assert reg.endpoint_for("chat", carica=False)[2] == "quello-vivo"
    assert motori.chiamate == ["warm"]


def test_by_default_nothing_changes_for_who_already_uses_it(tmp_path: Path) -> None:
    """Il valore predefinito resta quello di prima: gli altri progetti non se ne accorgono."""
    motori = _MotoriFinti(caldo=None)
    reg = Registry(path=tmp_path / "p.json", engines=motori)
    reg.assign("chat", reg.add({"type": "lmstudio", "model": "quello-in-configurazione"})["id"])

    assert reg.endpoint_for("chat")[2] == "caricato-adesso"
    assert motori.chiamate == ["resolve"]


def test_a_removed_provider_leaves_no_consumer_hanging(registro: Registry) -> None:
    voce = registro.add({"type": "deepseek", "api_key": "sk-prova"})
    registro.assign("chat", voce["id"])
    registro.remove(voce["id"])
    assert registro.endpoint_for("chat") is None


def test_the_public_view_hides_the_keys(registro: Registry) -> None:
    """Il registro finisce in un pannello e in una segnalazione di errore: le chiavi no."""
    registro.add({"type": "deepseek", "api_key": "sk-NON-MOSTRARE"})
    import json

    assert "sk-NON-MOSTRARE" not in json.dumps(registro.public())


def test_an_unknown_type_is_refused(registro: Registry) -> None:
    with pytest.raises(ValueError, match="sconosciuto"):
        registro.add({"type": "questo-non-esiste"})


# ---- abbonamenti: il registro e il modulo oauth devono parlarsi ---------------------------


@pytest.fixture()
def abbonamento_finto(monkeypatch):
    """Finge un abbonamento collegato, senza toccare credenziali vere."""
    from llmswitch import oauth

    monkeypatch.setattr(oauth, "access_token",
                        lambda nome, consenti_login=True: "token-dal-cli")
    monkeypatch.setattr(oauth, "intestazioni",
                        lambda nome, consenti_login=True: {
                            "Authorization": "Bearer token-dal-cli",
                            "ChatGPT-Account-Id": "acct-1",
                        })


def test_a_subscription_provider_gets_its_token(tmp_path: Path, abbonamento_finto) -> None:
    """Senza questo la voce esiste nel registro ma la chiave esce vuota: 401 al primo colpo.

    È il caso peggiore da diagnosticare, perché l'utente vede il fornitore configurato e non
    ha modo di sapere che manca la credenziale.
    """
    reg = Registry(path=tmp_path / "p.json")
    reg.assign("chat", reg.add({"type": "codex_oauth"})["id"])
    assert reg.endpoint_for("chat")[1] == "token-dal-cli"


def test_a_token_pasted_by_hand_is_still_honoured(tmp_path: Path, monkeypatch) -> None:
    """Chi aveva incollato un token nel registro non deve vederselo ignorare."""
    from llmswitch import oauth

    monkeypatch.setattr(oauth, "access_token",
                        lambda nome, consenti_login=True: "")  # nessuno strumento collegato
    reg = Registry(path=tmp_path / "p.json")
    voce = reg.add({"type": "codex_oauth"})
    dati = reg.load()
    next(p for p in dati["providers"] if p["id"] == voce["id"])["oauth"] = {
        "access_token": "token-incollato"
    }
    reg.save(dati)
    reg.assign("chat", voce["id"])
    assert reg.endpoint_for("chat")[1] == "token-incollato"


def test_a_subscription_not_connected_does_not_raise(tmp_path: Path, monkeypatch) -> None:
    """Non collegato è un caso normale, non un guasto: si torna una chiave vuota."""
    from llmswitch import oauth

    def non_collegato(nome, consenti_login=True):
        raise oauth.OAuthError("non collegato")

    monkeypatch.setattr(oauth, "access_token", non_collegato)
    reg = Registry(path=tmp_path / "p.json")
    reg.assign("chat", reg.add({"type": "qwen_oauth"})["id"])
    assert reg.endpoint_for("chat")[1] == ""


def test_headers_carry_what_the_service_demands_beyond_the_token(
    tmp_path: Path, abbonamento_finto
) -> None:
    """Il backend di Codex vuole anche l'id dell'account: col solo token risponde picche."""
    reg = Registry(path=tmp_path / "p.json")
    reg.assign("chat", reg.add({"type": "codex_oauth"})["id"])
    testa = reg.headers_for("chat")
    assert testa["Authorization"] == "Bearer token-dal-cli"
    assert testa["ChatGPT-Account-Id"] == "acct-1"


def test_headers_for_an_ordinary_provider_are_just_the_key(tmp_path: Path) -> None:
    reg = Registry(path=tmp_path / "p.json")
    reg.assign("chat", reg.add({"type": "deepseek", "api_key": "sk-prova"})["id"])
    assert reg.headers_for("chat") == {"Authorization": "Bearer sk-prova"}


def test_an_unassigned_consumer_has_no_headers(tmp_path: Path) -> None:
    assert Registry(path=tmp_path / "p.json").headers_for("chat") == {}


def test_the_endpoint_never_stops_to_ask_for_a_login(tmp_path: Path, monkeypatch) -> None:
    """Un lavoro notturno non deve piantarsi in attesa di una conferma nel browser."""
    from llmswitch import oauth

    visto = {}

    def registra(nome, consenti_login=True):
        visto["consenti_login"] = consenti_login
        return "t"

    monkeypatch.setattr(oauth, "access_token", registra)
    reg = Registry(path=tmp_path / "p.json")
    reg.assign("chat", reg.add({"type": "grok_oauth"})["id"])
    reg.endpoint_for("chat")
    assert visto["consenti_login"] is False
