"""LocalEngines.loaded(): la CLI di LM Studio descrive LM Studio, non l'indirizzo che si è configurato."""

from __future__ import annotations

import llmswitch.local as local
from llmswitch.local import LmStudioConfig, LocalEngines


def _motori(monkeypatch, *, stato, ps, base="http://127.0.0.1:7232/v1"):
    """Motori con un server che risponde ma senza API nativa; la CLI risponde con ``stato``/``ps``."""
    eng = LocalEngines(LmStudioConfig(base_url=base))
    eng.lmstudio_up = lambda *a, **k: True
    eng._meta = lambda: None            # /api/v0/models non risponde (Basalt, llama-server…)
    eng.lms_cli = lambda: "lms"
    chiamate: list[list[str]] = []

    def finto(cli, args, timeout):
        chiamate.append(list(args))
        return stato if args[:2] == ["server", "status"] else ps

    monkeypatch.setattr(local, "_lms_json", finto)
    return eng, chiamate


def test_another_server_is_not_described_by_lm_studio_memory(monkeypatch) -> None:
    """Basalt sulla 7232, LM Studio (spento) sulla 1234: `lms ps` non si chiede, la risposta è «non so»."""
    eng, chiamate = _motori(monkeypatch, stato={"running": False, "port": 1234}, ps=[])
    assert eng.loaded("text") is None, "None = ignoto; [] autorizzerebbe a caricare un modello"
    assert ["ps"] not in chiamate


def test_lm_studio_on_another_port_does_not_describe_this_address(monkeypatch) -> None:
    eng, chiamate = _motori(monkeypatch, stato={"running": True, "port": 1234},
                            ps=[{"identifier": "dentro-lm-studio"}])
    assert eng.loaded("text") is None
    assert ["ps"] not in chiamate


def test_an_old_lm_studio_without_native_api_is_still_read_through_the_cli(monkeypatch) -> None:
    """Stesso indirizzo del server di LM Studio: il ripiego sulla CLI resta, com'era."""
    eng, _ = _motori(monkeypatch, stato={"running": True, "port": 1234},
                     ps=[{"identifier": "vecchio-ma-dentro"}], base="http://127.0.0.1:1234/v1")
    assert eng.loaded("text") == ["vecchio-ma-dentro"]


def test_a_cli_that_cannot_say_keeps_the_old_behaviour(monkeypatch) -> None:
    eng, _ = _motori(monkeypatch, stato=None, ps=[{"identifier": "m"}])
    assert eng.loaded("text") == ["m"]
