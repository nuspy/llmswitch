"""Blueprint Flask pronto all'uso per un `Registry`.

    from flask import Flask
    from llmswitch import Registry
    from llmswitch.flask import make_blueprint

    app = Flask(__name__)
    app.register_blueprint(make_blueprint(Registry(app_name="mioprogetto")))

Espone, sotto `url_prefix` (default `/llm`):

    GET    /providers                 registro senza segreti + tipi + assegnazioni
    POST   /providers                 aggiunge {type, name?, base_url?, api_key?, activation?, reasoning_level?}
    PUT    /providers/<id>            aggiorna name | base_url | model | api_key | activation | reasoning_level
    DELETE /providers/<id>            rimuove (e libera i consumatori agganciati)
    GET    /providers/<id>/models     modelli, `?refresh=1` salta la cache
    POST   /providers/<id>/test       prova la connessione
    POST   /providers/<id>/activate   accende via SSH il server on-demand (es. Bonsai)
    POST   /providers/<id>/deactivate lo spegne, liberando la VRAM
    GET    /providers/<id>/active     {active: bool} — /health del server
    POST   /assign                    {consumer, provider} — provider null = stacca
    GET    /reasoning-levels          le scelte del menu del ragionamento (`?lang=en`, `?inherit=0`)
    GET    /static/livello-ragionamento.js   il menu a tendina di riferimento, senza dipendenze

Le credenziali non escono mai: di una chiave si sa solo se c'è.
"""
from __future__ import annotations

import logging

from flask import Blueprint, jsonify, request

from . import reasoning
from .providers import Registry

log = logging.getLogger("llmswitch.flask")


def make_blueprint(registry: Registry, name: str = "llmswitch",
                   url_prefix: str = "/llm") -> Blueprint:
    # `static_folder`: il componente di riferimento del menu del ragionamento, servito sotto `<url_prefix>/static/`.
    bp = Blueprint(name, __name__, url_prefix=url_prefix, static_folder="static")

    @bp.get("/providers")
    def elenco():
        return jsonify(registry.public())

    @bp.post("/providers")
    def aggiungi():
        try:
            voce = registry.add(request.get_json(silent=True) or {})
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        log.info("provider aggiunto: %s (%s)", voce["id"], voce["type"])
        return jsonify(registry.public())

    @bp.put("/providers/<pid>")
    def modifica(pid: str):
        try:
            registry.update(pid, request.get_json(silent=True) or {})
        except KeyError:
            return jsonify({"error": f"provider '{pid}' non trovato"}), 404
        except ValueError as e:  # un livello di ragionamento sconosciuto
            return jsonify({"error": str(e)}), 400
        return jsonify(registry.public())

    @bp.delete("/providers/<pid>")
    def elimina(pid: str):
        registry.remove(pid)
        log.info("provider rimosso: %s", pid)
        return jsonify(registry.public())

    @bp.get("/providers/<pid>/models")
    def modelli(pid: str):
        force = request.args.get("refresh") in ("1", "true", "yes")
        try:
            return jsonify({"models": registry.models(pid, force=force)})
        except KeyError:
            return jsonify({"error": f"provider '{pid}' non trovato"}), 404
        except Exception as e:  # noqa: BLE001 - provider giù o chiave errata
            log.warning("modelli di '%s' non letti: %s", pid, e)
            return jsonify({"error": str(e)[:300], "models": []}), 502

    @bp.post("/providers/<pid>/test")
    def prova(pid: str):
        return jsonify(registry.test(pid))

    # --- attivazione on-demand (server via SSH, es. Bonsai) ---------------
    @bp.post("/providers/<pid>/activate")
    def attiva(pid: str):
        try:
            ok, msg = registry.activate(pid)
        except KeyError:
            return jsonify({"error": f"provider '{pid}' non trovato"}), 404
        except ValueError as e:  # provider senza attivazione SSH
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 502)

    @bp.post("/providers/<pid>/deactivate")
    def disattiva(pid: str):
        try:
            ok, msg = registry.deactivate(pid)
        except KeyError:
            return jsonify({"error": f"provider '{pid}' non trovato"}), 404
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify({"ok": ok, "message": msg}), (200 if ok else 502)

    @bp.get("/providers/<pid>/active")
    def stato_attivo(pid: str):
        try:
            return jsonify({"active": registry.is_active(pid)})
        except KeyError:
            return jsonify({"error": f"provider '{pid}' non trovato"}), 404
        except ValueError as e:
            return jsonify({"error": str(e)}), 400

    @bp.post("/assign")
    def assegna():
        body = request.get_json(silent=True) or {}
        try:
            registry.assign((body.get("consumer") or "").strip(),
                            (body.get("provider") or "").strip() or None)
        except KeyError as e:
            return jsonify({"error": f"provider non trovato: {e}"}), 404
        except ValueError as e:
            return jsonify({"error": str(e)}), 400
        return jsonify(registry.public())

    @bp.get("/reasoning-levels")
    def livelli_ragionamento():
        """Le scelte del menu a tendina: `inherit` e i sette livelli, con l'etichetta nella lingua chiesta."""
        lingua = (request.args.get("lang") or "it").strip().lower()
        con_eredita = request.args.get("inherit") not in ("0", "false", "no")
        return jsonify({
            "levels": reasoning.choices(lingua, inherit=con_eredita),
            "default": reasoning.INHERIT if con_eredita else "none",
        })

    return bp
