"""Test dei CLI-agenti.

Nessun test lancia gli strumenti veri: sono lenti (dai 4 ai 16 secondi a risposta) e ogni
esecuzione consuma l'abbonamento di chi esegue la suite. Si finge il sottoprocesso e si
verifica COME viene invocato, che è la parte dove si sbaglia — un'opzione fuori posto e
Antigravity stampa l'aiuto invece di rispondere, senza che nessuno se ne accorga.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from llmswitch import agents
from llmswitch.agents import AgentError


def _stream_json_ok(response: str = "da stream") -> str:
    """Uno stream NDJSON come quello vero di Antigravity: init, un turno, il `result`."""
    righe = [
        {"event": "init", "conversation_id": "x"},
        {"event": "step_update", "step_update": {"text_delta": response}},
        {"event": "result", "result": {"status": "SUCCESS", "response": response}},
    ]
    return "\n".join(json.dumps(r) for r in righe) + "\n"


@pytest.fixture()
def finto(monkeypatch):
    """Sostituisce il sottoprocesso; raccoglie come è stato chiamato e cosa gli è arrivato."""
    visto: dict = {}

    def esegui(argomenti, input=None, capture_output=None, text=None, encoding=None,  # noqa: A002
               errors=None, timeout=None, cwd=None, creationflags=None):
        visto.update(argomenti=list(argomenti), input=input, cwd=cwd, timeout=timeout)
        # Antigravity risponde con uno stream NDJSON; la risposta sta nell'evento `result`.
        if "stream-json" in argomenti:
            return subprocess.CompletedProcess(argomenti, 0, stdout=_stream_json_ok(), stderr="")
        # Chi risponde su file lo trova nell'argomento dopo l'opzione di uscita.
        if "-o" in argomenti:
            Path(argomenti[argomenti.index("-o") + 1]).write_text("dal file", encoding="utf-8")
        return subprocess.CompletedProcess(argomenti, 0, stdout="da stdout", stderr="")

    monkeypatch.setattr(agents.subprocess, "run", esegui)
    monkeypatch.setattr(agents.shutil, "which", lambda x: f"/finto/{x}")
    return visto


def test_an_unknown_agent_is_refused() -> None:
    with pytest.raises(AgentError, match="sconosciuto"):
        agents.genera("questo-non-esiste", "ciao")


def test_a_missing_tool_says_what_to_install(monkeypatch) -> None:
    monkeypatch.setattr(agents.shutil, "which", lambda x: None)
    with pytest.raises(AgentError, match="non è installato"):
        agents.genera("claude_cli", "ciao")
    assert agents.disponibile("claude_cli") is False


def test_an_empty_prompt_is_not_sent(finto) -> None:
    with pytest.raises(AgentError, match="niente da chiedere"):
        agents.genera("claude_cli", "   ")


def test_claude_receives_the_prompt_on_standard_input(finto) -> None:
    assert agents.genera("claude_cli", "traduci questo") == "da stdout"
    assert finto["argomenti"] == ["claude", "-p"]
    assert finto["input"] == "traduci questo"


def test_antigravity_receives_the_prompt_on_stdin_as_stream_json(finto) -> None:
    """Il prompt viaggia come NDJSON su stdin, non come argomento.

    Passato come argomento di `-p`, un testo lungo (una trascrizione d'intervista) sforava la
    command line di Windows e il lancio moriva con [WinError 206]. Lo stdin non ha quel limite.
    """
    assert agents.genera("antigravity", "traduci questo") == "da stream"
    assert finto["argomenti"] == [
        "agy", "--input-format", "stream-json", "--output-format", "stream-json",
    ]
    assert "traduci questo" not in finto["argomenti"]  # niente prompt fra gli argomenti
    messaggio = json.loads(finto["input"])
    assert messaggio["event"] == "user"
    assert messaggio["message"]["content"][0]["text"] == "traduci questo"


def test_antigravity_reports_a_failed_turn_from_the_result_event(finto, monkeypatch) -> None:
    """Un turno fallito porta il suo motivo nel `result`, non un codice di uscita da leggere."""
    fallito = json.dumps({"event": "result",
                          "result": {"status": "ERROR", "error": "sessione scaduta"}}) + "\n"
    monkeypatch.setattr(
        agents.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=fallito, stderr=""),
    )
    with pytest.raises(AgentError, match="sessione scaduta"):
        agents.genera("antigravity", "ciao")


def test_codex_answer_is_read_from_the_file_not_from_the_noise(finto) -> None:
    """Su stdout Codex mescola ganci e contatori alla risposta: si legge l'ultimo messaggio."""
    assert agents.genera("codex_cli", "traduci questo") == "dal file"
    assert "-o" in finto["argomenti"]
    assert finto["input"] == "traduci questo"


def test_the_chosen_model_is_passed_along(finto) -> None:
    agents.genera("antigravity", "ciao", model="gemini-3.7-flash-high")
    # Il modello è un'opzione di sessione; il prompt non è più fra gli argomenti ma su stdin.
    assert finto["argomenti"][-2:] == ["--model", "gemini-3.7-flash-high"]


def test_the_placeholder_model_is_not_sent_as_a_real_one(finto) -> None:
    """`default` è il segnaposto di un provider senza modello scelto: `agy` lo rifiuterebbe.

    Va trattato come «nessun modello» — niente `--model`, e l'agente usa il default del suo
    account. È il difetto che bloccava un'intervista: «model default is not recognized».
    """
    agents.genera("antigravity", "ciao", model=agents.MODELLO_SEGNAPOSTO)
    assert "--model" not in finto["argomenti"]


def test_a_blank_model_leaves_the_option_off(finto) -> None:
    agents.genera("antigravity", "ciao", model="   ")
    assert "--model" not in finto["argomenti"]


def test_by_default_the_agent_runs_in_an_empty_folder(finto, tmp_path, monkeypatch) -> None:
    """Questi agenti guardano i file intorno a sé: per scrivere un testo non serve.

    Eseguirli nella cartella di chi li chiama significherebbe dare in pasto il progetto a un
    servizio esterno senza che nessuno l'abbia chiesto.
    """
    monkeypatch.chdir(tmp_path)
    (tmp_path / "segreto.txt").write_text("roba mia", encoding="utf-8")
    agents.genera("claude_cli", "ciao")
    assert Path(finto["cwd"]).resolve() != tmp_path.resolve()
    assert not list(Path(finto["cwd"]).glob("segreto.txt"))


def test_a_folder_can_be_given_on_purpose(finto, tmp_path) -> None:
    """Quando si VUOLE che l'agente veda dei file, lo si dice."""
    agents.genera("claude_cli", "ciao", cartella=tmp_path)
    assert Path(finto["cwd"]).resolve() == tmp_path.resolve()


def test_a_stuck_agent_is_reported_as_such(monkeypatch) -> None:
    def piantato(*a, **k):
        raise subprocess.TimeoutExpired(cmd="claude", timeout=1)

    monkeypatch.setattr(agents.shutil, "which", lambda x: f"/finto/{x}")
    monkeypatch.setattr(agents.subprocess, "run", piantato)
    with pytest.raises(AgentError, match="nessuna risposta in"):
        agents.genera("claude_cli", "ciao", timeout_s=1)


def test_an_empty_answer_carries_the_reason(monkeypatch) -> None:
    """Il codice di uscita da solo non spiega niente: si riporta anche cosa ha detto."""
    monkeypatch.setattr(agents.shutil, "which", lambda x: f"/finto/{x}")
    monkeypatch.setattr(
        agents.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess([], 1, stdout="", stderr="sessione scaduta"),
    )
    with pytest.raises(AgentError, match="sessione scaduta"):
        agents.genera("claude_cli", "ciao")


def test_models_are_parsed_from_the_tab_separated_listing(monkeypatch) -> None:
    uscita = ("Fetching available models...\n"
              "gemini-3.7-flash-high\tGemini 3.7 Flash (High)\n"
              "gpt-oss-120b\tGPT-OSS 120B\n")
    monkeypatch.setattr(agents.shutil, "which", lambda x: f"/finto/{x}")
    monkeypatch.setattr(
        agents.subprocess, "run",
        lambda *a, **k: subprocess.CompletedProcess([], 0, stdout=uscita, stderr=""),
    )
    trovati = agents.modelli("antigravity")
    assert [m["id"] for m in trovati] == ["gemini-3.7-flash-high", "gpt-oss-120b"]
    assert trovati[0]["label"] == "Gemini 3.7 Flash (High)"


def test_an_agent_that_cannot_list_models_says_nothing_instead_of_guessing(finto) -> None:
    assert agents.modelli("claude_cli") == []


def test_every_agent_matches_a_provider_in_the_registry() -> None:
    """Gli id sono gli stessi: un agente senza voce nel registro non si potrebbe scegliere."""
    from llmswitch import TYPES

    for nome, a in agents.AGENTI.items():
        assert nome in TYPES, nome
        assert TYPES[nome]["api"] == "cli", nome
        # Il comando dichiarato nel registro e quello che si esegue devono coincidere: sono
        # due copie della stessa cosa, e quando divergono l'agente smette di funzionare.
        assert TYPES[nome]["command"].split() == list(a.comando), nome
