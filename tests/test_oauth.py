"""Test degli abbonamenti: lettura, rinnovo e riscrittura delle credenziali.

Qui non si tocca nessuna credenziale vera: ogni test lavora su un file finto in una cartella
temporanea. È l'unico modo di provare davvero il caso che conta — la riscrittura del file di
uno strumento ufficiale — senza rischiare di far perdere l'accesso a chi esegue i test.
"""

from __future__ import annotations

import base64
import json
import time
from dataclasses import replace
from pathlib import Path

import pytest

from llmswitch import oauth
from llmswitch.oauth import Abbonamento, Credenziali, OAuthError


def _jwt(scade_fra_ore: float) -> str:
    """Un JWT plausibile con la sola scadenza: è l'unico campo che il modulo legge."""
    corpo = json.dumps({"exp": int(time.time() + scade_fra_ore * 3600)}).encode()
    mezzo = base64.urlsafe_b64encode(corpo).rstrip(b"=").decode()
    return f"testa.{mezzo}.firma"


@pytest.fixture()
def piano(tmp_path: Path) -> Abbonamento:
    """Abbonamento finto con le credenziali in cima al documento, come fa Gemini."""
    return Abbonamento(
        id="finto", label="Servizio Finto", file=str(tmp_path / "creds.json"),
        token_url="https://esempio.invalid/token", client_id="cid",
        inference_base_url="https://esempio.invalid/v1", comando="finto",
        login_manuale="finto login",
    )


@pytest.fixture()
def piano_annidato(tmp_path: Path) -> Abbonamento:
    """Abbonamento finto con le credenziali dentro «tokens», come fa Codex."""
    return Abbonamento(
        id="finto2", label="Servizio Annidato", file=str(tmp_path / "auth.json"),
        dentro=("tokens",), token_url="https://esempio.invalid/token", client_id="cid",
        header_account="X-Account-Id", comando="finto2",
    )


def _scrivi_file(piano: Abbonamento, documento: dict) -> None:
    Path(oauth.percorso(piano)).write_text(json.dumps(documento), encoding="utf-8")


# ---- lettura -------------------------------------------------------------------------


def test_missing_file_is_not_an_error(piano: Abbonamento) -> None:
    """Chi non ha mai fatto l'accesso non deve vedere un'eccezione, ma un «non c'è»."""
    assert oauth.leggi(piano) is None


def test_credentials_are_read_from_the_top_level(piano: Abbonamento) -> None:
    _scrivi_file(piano, {
        "access_token": "abc", "refresh_token": "rrr",
        "expiry_date": int((time.time() + 3600) * 1000),  # millisecondi, come Google
    })
    cred = oauth.leggi(piano)
    assert cred is not None
    assert (cred.access_token, cred.refresh_token) == ("abc", "rrr")
    assert 0.9 < cred.scade_fra() < 1.1


def test_credentials_are_read_from_a_nested_object(piano_annidato: Abbonamento) -> None:
    _scrivi_file(piano_annidato, {
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "abc", "refresh_token": "rrr", "account_id": "acct-1"},
    })
    cred = oauth.leggi(piano_annidato)
    assert cred is not None
    assert cred.account_id == "acct-1"


def test_the_expiry_is_taken_from_the_token_when_the_file_omits_it(
    piano_annidato: Abbonamento,
) -> None:
    """Codex non scrive la scadenza: senza guardare dentro il token non si saprebbe.

    È il caso che conta davvero, perché senza questa lettura un token già morto sembrerebbe
    buono e la prima richiesta fallirebbe con un 401 inspiegabile.
    """
    _scrivi_file(piano_annidato, {"tokens": {"access_token": _jwt(5), "refresh_token": "rrr"}})
    cred = oauth.leggi(piano_annidato)
    assert cred is not None
    assert 4.9 < cred.scade_fra() < 5.1


def test_a_token_without_a_declared_expiry_is_tried_anyway(piano: Abbonamento) -> None:
    """Se nessuno dichiara una scadenza si prova lo stesso: lo dirà il servizio."""
    _scrivi_file(piano, {"access_token": "non-un-jwt"})
    cred = oauth.leggi(piano)
    assert cred is not None and cred.valide()


def test_a_token_expiring_within_the_margin_counts_as_expired(piano: Abbonamento) -> None:
    """Un token che scade fra un minuto non va usato per un lavoro che dura ore."""
    _scrivi_file(piano, {"access_token": "abc", "expiry_date": int((time.time() + 60) * 1000)})
    cred = oauth.leggi(piano)
    assert cred is not None
    assert cred.valide(margine_s=30) and not cred.valide(margine_s=120)


# ---- rinnovo e riscrittura ------------------------------------------------------------


class _RispostaFinta:
    def __init__(self, payload: dict, ok: bool = True, status: int = 200) -> None:
        self._payload, self.ok, self.status_code = payload, ok, status

    def json(self) -> dict:
        return self._payload


def _finge_rinnovo(monkeypatch, payload: dict, *, ok: bool = True, status: int = 200) -> list:
    """Sostituisce la chiamata di rete; ritorna la lista dei dati inviati, per ispezionarli."""
    inviati: list = []

    def posta(url, data=None, headers=None, timeout=None):  # noqa: ANN001
        inviati.append({"url": url, "data": data, "headers": headers})
        return _RispostaFinta(payload, ok=ok, status=status)

    import requests

    monkeypatch.setattr(requests, "post", posta)
    return inviati


def test_a_renewal_rewrites_the_file_and_keeps_the_rest(
    piano_annidato: Abbonamento, monkeypatch
) -> None:
    """Il documento contiene anche altro: rinnovare non deve cancellarlo.

    `~/.codex/auth.json` porta una chiave API accanto ai token. Riscriverlo da zero
    significherebbe far sparire roba che non ci riguarda e non sappiamo ricostruire.
    """
    _scrivi_file(piano_annidato, {
        "OPENAI_API_KEY": "sk-da-non-perdere",
        "auth_mode": "chatgpt",
        "tokens": {"access_token": "vecchio", "refresh_token": "rrr", "account_id": "acct-1"},
    })
    _finge_rinnovo(monkeypatch, {"access_token": "nuovo", "expires_in": 3600})

    cred = oauth.rinnova(piano_annidato, oauth.leggi(piano_annidato))
    assert cred.access_token == "nuovo"

    sul_disco = json.loads(Path(oauth.percorso(piano_annidato)).read_text(encoding="utf-8"))
    assert sul_disco["OPENAI_API_KEY"] == "sk-da-non-perdere"
    assert sul_disco["auth_mode"] == "chatgpt"
    assert sul_disco["tokens"]["access_token"] == "nuovo"
    assert sul_disco["tokens"]["refresh_token"] == "rrr"  # non ruotato: si tiene il vecchio
    assert sul_disco["tokens"]["account_id"] == "acct-1"


def test_a_rotated_refresh_token_is_saved(piano: Abbonamento, monkeypatch) -> None:
    """Se il servizio ne manda uno nuovo va scritto, o il prossimo rinnovo fallisce.

    È la ragione per cui si riscrive il file dello strumento ufficiale invece di tenere una
    copia privata: il vecchio refresh token, dopo la rotazione, non vale più per nessuno.
    """
    _scrivi_file(piano, {"access_token": "vecchio", "refresh_token": "primo"})
    _finge_rinnovo(monkeypatch, {"access_token": "nuovo", "refresh_token": "secondo",
                                 "expires_in": 3600})

    oauth.rinnova(piano, oauth.leggi(piano))
    sul_disco = json.loads(Path(oauth.percorso(piano)).read_text(encoding="utf-8"))
    assert sul_disco["refresh_token"] == "secondo"


def test_the_first_renewal_leaves_a_backup(piano: Abbonamento, monkeypatch) -> None:
    _scrivi_file(piano, {"access_token": "vecchio", "refresh_token": "rrr"})
    _finge_rinnovo(monkeypatch, {"access_token": "nuovo", "expires_in": 3600})

    oauth.rinnova(piano, oauth.leggi(piano))
    backup = Path(str(oauth.percorso(piano)) + ".prima-di-llmswitch")
    assert backup.is_file()
    assert json.loads(backup.read_text(encoding="utf-8"))["access_token"] == "vecchio"


def test_the_backup_keeps_the_original_not_the_last_state(
    piano: Abbonamento, monkeypatch
) -> None:
    """Il secondo rinnovo non deve sovrascrivere la copia: serve quella di partenza."""
    _scrivi_file(piano, {"access_token": "originale", "refresh_token": "rrr"})
    _finge_rinnovo(monkeypatch, {"access_token": "primo-nuovo", "expires_in": 3600})
    oauth.rinnova(piano, oauth.leggi(piano))
    _finge_rinnovo(monkeypatch, {"access_token": "secondo-nuovo", "expires_in": 3600})
    oauth.rinnova(piano, oauth.leggi(piano))

    backup = Path(str(oauth.percorso(piano)) + ".prima-di-llmswitch")
    assert json.loads(backup.read_text(encoding="utf-8"))["access_token"] == "originale"


def test_no_leftover_temporary_file(piano: Abbonamento, monkeypatch) -> None:
    """La sostituzione è atomica: non deve restare in giro il file di lavoro."""
    _scrivi_file(piano, {"access_token": "vecchio", "refresh_token": "rrr"})
    _finge_rinnovo(monkeypatch, {"access_token": "nuovo", "expires_in": 3600})
    oauth.rinnova(piano, oauth.leggi(piano))

    cartella = Path(oauth.percorso(piano)).parent
    assert not list(cartella.glob("*.nuovo"))


def test_a_refused_renewal_says_what_to_do_without_leaking_the_token(
    piano: Abbonamento, monkeypatch
) -> None:
    """Il corpo di un errore può contenere ciò che abbiamo mandato: non finisce nel messaggio."""
    _scrivi_file(piano, {"access_token": "vecchio", "refresh_token": "segretissimo"})
    _finge_rinnovo(monkeypatch, {"error": "invalid_grant", "detail": "segretissimo"},
                   ok=False, status=400)

    with pytest.raises(OAuthError) as errore:
        oauth.rinnova(piano, oauth.leggi(piano))
    messaggio = str(errore.value)
    assert "400" in messaggio and "finto login" in messaggio
    assert "segretissimo" not in messaggio


def test_a_renewal_without_a_refresh_token_fails_clearly(piano: Abbonamento) -> None:
    with pytest.raises(OAuthError, match="rifare l'accesso"):
        oauth.rinnova(piano, Credenziali(access_token="solo-questo"))


# ---- la cascata ----------------------------------------------------------------------


def test_a_valid_credential_is_used_as_is(piano: Abbonamento, monkeypatch) -> None:
    """Chi ha già fatto l'accesso non deve rifarlo, e non si deve nemmeno chiamare la rete."""
    _scrivi_file(piano, {"access_token": "buono", "expiry_date": int((time.time() + 7200) * 1000)})
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", piano)

    def vietato(*a, **k):  # noqa: ANN002, ANN003
        raise AssertionError("non si contatta nessuno se la credenziale è ancora buona")

    monkeypatch.setattr(oauth, "rinnova", vietato)
    monkeypatch.setattr(oauth, "login_device", vietato)
    assert oauth.credenziali("finto").access_token == "buono"


def test_an_expired_credential_is_renewed(piano: Abbonamento, monkeypatch) -> None:
    _scrivi_file(piano, {"access_token": "scaduto", "refresh_token": "rrr",
                         "expiry_date": int((time.time() - 3600) * 1000)})
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", piano)
    _finge_rinnovo(monkeypatch, {"access_token": "fresco", "expires_in": 3600})
    assert oauth.credenziali("finto").access_token == "fresco"


def test_without_credentials_and_without_device_login_it_says_the_command(
    piano: Abbonamento, monkeypatch
) -> None:
    """Se non si può accedere da qui, l'errore deve dire cosa lanciare — non «fallito»."""
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", piano)
    with pytest.raises(OAuthError, match="finto login"):
        oauth.credenziali("finto")


def test_login_can_be_forbidden_for_unattended_runs(piano: Abbonamento, monkeypatch) -> None:
    """Un lavoro in coda, di notte, non deve fermarsi ad aspettare che qualcuno confermi."""
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", piano)
    monkeypatch.setattr(oauth, "login_device", lambda *a, **k: pytest.fail("non va chiesto"))
    with pytest.raises(OAuthError, match="nessuna credenziale valida"):
        oauth.credenziali("finto", consenti_login=False)


def test_an_unknown_subscription_is_rejected() -> None:
    with pytest.raises(OAuthError, match="sconosciuto"):
        oauth.credenziali("questo-non-esiste")


# ---- intestazioni e stato -------------------------------------------------------------


def test_the_extra_header_the_service_demands_is_added(
    piano_annidato: Abbonamento, monkeypatch
) -> None:
    """Il backend di Codex pretende l'id dell'account: senza, risponde picche."""
    _scrivi_file(piano_annidato, {
        "tokens": {"access_token": _jwt(5), "refresh_token": "rrr", "account_id": "acct-1"},
    })
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto2", piano_annidato)
    testa = oauth.intestazioni("finto2")
    assert testa["Authorization"] == f"Bearer {oauth.leggi(piano_annidato).access_token}"
    assert testa["X-Account-Id"] == "acct-1"


def test_we_introduce_ourselves_with_our_own_name() -> None:
    """Fingersi il client ufficiale altrui per aggirarne i controlli fa sospendere gli account."""
    assert oauth.USER_AGENT.startswith("llmswitch/")


def test_the_status_never_contains_a_token(piano: Abbonamento, monkeypatch) -> None:
    """Lo stato finisce in un pannello, in un log, in uno screenshot di segnalazione."""
    _scrivi_file(piano, {"access_token": "SEGRETO-DA-NON-MOSTRARE", "refresh_token": "RRR"})
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", piano)
    testo = json.dumps(oauth.stato("finto"))
    assert "SEGRETO-DA-NON-MOSTRARE" not in testo and "RRR" not in testo


def test_the_status_distinguishes_the_three_cases(piano: Abbonamento, monkeypatch) -> None:
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", piano)
    assert oauth.stato("finto")["collegato"] is False

    _scrivi_file(piano, {"access_token": "x", "refresh_token": "r",
                         "expiry_date": int((time.time() - 3600) * 1000)})
    scaduto = oauth.stato("finto")
    assert scaduto["collegato"] is True
    assert scaduto["utilizzabile"] is False
    assert scaduto["rinnovabile"] is True

    _scrivi_file(piano, {"access_token": "x", "expiry_date": int((time.time() + 7200) * 1000)})
    assert oauth.stato("finto")["utilizzabile"] is True


def test_every_subscription_says_how_to_connect() -> None:
    """Nessun abbonamento deve lasciare l'utente senza sapere cosa fare per collegarlo."""
    for nome in oauth.ABBONAMENTI:
        s = oauth.stato(nome)
        assert s["accesso_da_qui"] or s["come_accedere"], nome
        assert s["inference_base_url"], nome


# ---- quando il token è buono ma la porta è chiusa ------------------------------------------


def test_a_blocked_plan_is_not_reported_as_ready(piano: Abbonamento, monkeypatch) -> None:
    """Credenziale valida e servizio che rifiuta: dire «pronto» sarebbe una bugia.

    È il caso di Gemini: `loadCodeAssist` risponde 200 e dentro spiega che il piano dei
    privati non è più ammesso. Chi guarda il pannello deve leggerlo lì, non scoprirlo da un
    errore alla prima richiesta.
    """
    bloccato = replace(piano, impedimento="il fornitore non ammette più questo piano")
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", bloccato)
    _scrivi_file(bloccato, {"access_token": "buono",
                            "expiry_date": int((time.time() + 7200) * 1000)})
    s = oauth.stato("finto")
    assert s["collegato"] is True, "la credenziale c'è ed è valida"
    assert s["utilizzabile"] is False, "ma non porta da nessuna parte"
    assert "non ammette" in s["impedimento"]


def test_without_an_impediment_nothing_changes(piano: Abbonamento, monkeypatch) -> None:
    monkeypatch.setitem(oauth.ABBONAMENTI, "finto", piano)
    _scrivi_file(piano, {"access_token": "buono",
                         "expiry_date": int((time.time() + 7200) * 1000)})
    s = oauth.stato("finto")
    assert s["utilizzabile"] is True and s["impedimento"] == ""


def test_the_gemini_plan_carries_its_real_reason() -> None:
    """L'impedimento è un fatto misurato, non un'ipotesi: va scritto dov'è consultabile."""
    s = oauth.stato("gemini_oauth")
    assert s["utilizzabile"] is False
    assert "UNSUPPORTED_CLIENT" in s["impedimento"]
    assert "antigravity" in s["impedimento"].lower(), "e va detto dove andare invece"
