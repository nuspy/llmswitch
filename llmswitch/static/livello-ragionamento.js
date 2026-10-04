/* Il menu a tendina del livello di ragionamento: il componente di riferimento di llmswitch, senza dipendenze.
 *
 * llmswitch non ha un'interfaccia: espone i livelli (`GET <prefisso>/reasoning-levels`, oppure `reasoning_levels` in
 * `GET <prefisso>/providers`) e questo modulo disegna il menu. Un `<select>` vero, con la sua `<label>`: si usa con
 * tastiera, lettore di schermo e dito senza nulla di speciale. Corpo 16 px (iOS non ingrandisce la pagina al tocco),
 * almeno 48 px d'altezza, anello di focus visibile.
 *
 * Uso come modulo:
 *
 *   import { caricaLivelli, menuRagionamento } from "/llm/static/livello-ragionamento.js";
 *   const livelli = await caricaLivelli("/llm", "it");
 *   const menu = menuRagionamento({
 *     livelli, valore: "inherit", etichetta: "Ragionamento",
 *     descrizione: "Quanto pensa il modello prima di rispondere.",
 *     onChange: (valore) => salva(valore),
 *   });
 *   document.querySelector("#impostazioni").append(menu);
 *
 * Uso senza scrivere JavaScript: un elemento con `data-llmswitch-ragionamento` diventa un menu, e a ogni scelta manda
 * l'evento `llmswitch:ragionamento` (`event.detail.valore`), che risale il documento.
 *
 *   <div data-llmswitch-ragionamento data-base="/llm" data-lang="it" data-value="medium"
 *        data-label="Ragionamento" data-name="reasoning_level"></div>
 *   <script type="module" src="/llm/static/livello-ragionamento.js"></script>
 *
 * Valori: `inherit` (eredita dal fornitore; per un fornitore: non dire niente al server), `none`, `minimal`, `low`,
 * `medium`, `high`, `xhigh`, `max`. Chi salva il valore lo manda così com'è (`PUT <prefisso>/providers/<id>` con
 * `reasoning_level`, o come livello di una richiesta).
 */

/** Le scelte di ripiego, se il server non risponde: le stesse di `llmswitch.reasoning.choices("it")`. */
export const LIVELLI_PREDEFINITI = [
  { value: "inherit", label: "Eredita dal fornitore" },
  { value: "none", label: "Nessuno" },
  { value: "minimal", label: "Minimo" },
  { value: "low", label: "Basso" },
  { value: "medium", label: "Medio" },
  { value: "high", label: "Alto" },
  { value: "xhigh", label: "Molto alto" },
  { value: "max", label: "Massimo" },
];

const STILE = `
.llms-ragionamento { display: grid; gap: .35rem; max-width: 100%; font: inherit; }
.llms-ragionamento label { font-weight: 600; }
.llms-ragionamento select {
  min-height: 48px; max-width: 100%; padding: .5rem 2.25rem .5rem .75rem; font: inherit; font-size: 16px;
  line-height: 1.5; color: inherit; background-color: transparent; border: 1px solid currentColor; border-radius: 6px;
}
.llms-ragionamento select:focus-visible { outline: 3px solid Highlight; outline-offset: 2px; }
.llms-ragionamento select:disabled { opacity: .6; cursor: not-allowed; }
.llms-ragionamento .llms-descrizione { margin: 0; font-size: .9em; line-height: 1.5; opacity: .85; }
`;

let progressivo = 0;

function stileUnaVolta(documento) {
  if (documento.getElementById("llms-ragionamento-stile")) return;
  const stile = documento.createElement("style");
  stile.id = "llms-ragionamento-stile";
  stile.textContent = STILE;
  documento.head.append(stile);
}

/** Le scelte dal server (`GET <base>/reasoning-levels`); se non risponde, quelle predefinite. */
export async function caricaLivelli(base = "/llm", lang = "it", { eredita = true } = {}) {
  try {
    const parametri = new URLSearchParams({ lang, inherit: eredita ? "1" : "0" });
    const risposta = await fetch(`${base.replace(/\/$/, "")}/reasoning-levels?${parametri}`, {
      headers: { Accept: "application/json" }, credentials: "same-origin",
    });
    if (!risposta.ok) throw new Error(`HTTP ${risposta.status}`);
    const dati = await risposta.json();
    if (Array.isArray(dati.levels) && dati.levels.length) return dati.levels;
  } catch (errore) {
    console.warn("llmswitch: livelli di ragionamento non letti, uso quelli predefiniti", errore);
  }
  return eredita ? LIVELLI_PREDEFINITI : LIVELLI_PREDEFINITI.filter((l) => l.value !== "inherit");
}

/**
 * Il menu: un contenitore con `<label>`, `<select>` e, se c'è, la descrizione collegata con `aria-describedby`.
 *
 * @param {object} opzioni
 * @param {{value: string, label: string}[]} [opzioni.livelli] le scelte (predefinite: `LIVELLI_PREDEFINITI`)
 * @param {string} [opzioni.valore] la scelta iniziale; una sconosciuta diventa `inherit` (o la prima)
 * @param {string} [opzioni.etichetta] il testo della label
 * @param {string} [opzioni.descrizione] una riga di aiuto sotto il menu
 * @param {string} [opzioni.nome] l'attributo `name` del select, per i form classici
 * @param {string} [opzioni.id] l'id del select (generato se manca)
 * @param {boolean} [opzioni.disabilitato] per un fornitore che non ha parametri di ragionamento (dialetto `none`)
 * @param {(valore: string) => void} [opzioni.onChange] chiamata a ogni scelta
 * @param {Document} [opzioni.documento]
 * @returns {HTMLDivElement}
 */
export function menuRagionamento({
  livelli = LIVELLI_PREDEFINITI, valore = "inherit", etichetta = "Ragionamento", descrizione = "", nome = "",
  id = "", disabilitato = false, onChange, documento = globalThis.document,
} = {}) {
  stileUnaVolta(documento);
  const idSelect = id || `llms-ragionamento-${++progressivo}`;
  const contenitore = documento.createElement("div");
  contenitore.className = "llms-ragionamento";

  const label = documento.createElement("label");
  label.htmlFor = idSelect;
  label.textContent = etichetta;

  const select = documento.createElement("select");
  select.id = idSelect;
  if (nome) select.name = nome;
  select.disabled = Boolean(disabilitato);
  for (const livello of livelli) {
    const opzione = documento.createElement("option");
    opzione.value = livello.value;
    opzione.textContent = livello.label;
    select.append(opzione);
  }
  const valori = livelli.map((l) => l.value);
  select.value = valori.includes(valore) ? valore : (valori.includes("inherit") ? "inherit" : valori[0] ?? "");

  contenitore.append(label, select);
  if (descrizione) {
    const aiuto = documento.createElement("p");
    aiuto.className = "llms-descrizione";
    aiuto.id = `${idSelect}-descrizione`;
    aiuto.textContent = descrizione;
    select.setAttribute("aria-describedby", aiuto.id);
    contenitore.append(aiuto);
  }

  select.addEventListener("change", () => {
    onChange?.(select.value);
    contenitore.dispatchEvent(new CustomEvent("llmswitch:ragionamento", {
      bubbles: true, detail: { valore: select.value },
    }));
  });
  return contenitore;
}

/** Trasforma in menu ogni elemento con `data-llmswitch-ragionamento` dentro `radice` (una volta sola per elemento). */
export async function collegaMenu(radice = globalThis.document) {
  const segnaposti = [...radice.querySelectorAll("[data-llmswitch-ragionamento]:not([data-llms-pronto])")];
  for (const segnaposto of segnaposti) {
    segnaposto.setAttribute("data-llms-pronto", "");
    const d = segnaposto.dataset;
    const livelli = await caricaLivelli(d.base || "/llm", d.lang || "it", { eredita: d.inherit !== "0" });
    segnaposto.replaceChildren(menuRagionamento({
      livelli, valore: d.value || "inherit", etichetta: d.label || "Ragionamento", descrizione: d.description || "",
      nome: d.name || "", disabilitato: d.disabled === "true", documento: radice.ownerDocument || radice,
    }));
  }
}

if (globalThis.document?.querySelector) {
  const avvia = () => { void collegaMenu(); };
  if (document.readyState === "loading") document.addEventListener("DOMContentLoaded", avvia);
  else avvia();
}
