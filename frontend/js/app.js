const API = "/livrinho-ao-vivo/api/v1";
const SHELF_KEY = "livrinho.shelf";
const GLOBAL_EMOTES = "https://7tv.io/v3/emote-sets/global";
const CHANNEL_EMOTES = "https://7tv.io/v3/emote-sets/01HR3ABJ800007QJQMTQH1J05C";

const STATUS = {
  escrevendo: "Escrevendo…",
  quieto: "A cena está parada.",
  offline: "A live parece offline.",
  conectando: "Conectando na live…",
  limite: "Limite da API. Pausa de um minuto.",
  erro: "A escrita pausou por um erro.",
  pronto: "Lendo a live.",
  sem_chave: "Falta a chave do modelo.",
  orcamento: "O livrinho chegou no limite de US$ 10 deste mês.",
};

const emotes = new Map();
const personasEl = document.getElementById("personas");
const pagesEl = document.getElementById("pages");
const emptyEl = document.getElementById("empty");
const draftEl = document.getElementById("draft");
const statusEl = document.getElementById("status");
const shelfEl = document.getElementById("shelf");
const customForm = document.getElementById("custom-form");
const customPromptEl = document.getElementById("custom-prompt");
const customCountEl = document.getElementById("custom-count");

let personas = [];
let current = null;
let abort = null;
let customOpen = false;

function loadShelf() {
  try {
    const raw = JSON.parse(localStorage.getItem(SHELF_KEY) || "[]");
    return Array.isArray(raw) ? raw : [];
  } catch (_err) {
    return [];
  }
}

function saveShelf(items) {
  localStorage.setItem(SHELF_KEY, JSON.stringify(items.slice(0, 12)));
}

function remember(session, preview) {
  const shelf = loadShelf().filter((item) => item.id !== session.id);
  shelf.unshift({
    id: session.id,
    style: session.style,
    preview: preview || "",
    customPrompt: session.customPrompt || session.custom_prompt || "",
    updated: new Date().toISOString(),
  });
  saveShelf(shelf);
  renderShelf();
}

function personaName(style) {
  const found = personas.find((item) => item.id === style);
  return found ? found.name : style;
}

function setStatus(code, detail) {
  const label = STATUS[code] || detail || "Escolhe uma voz.";
  statusEl.textContent = detail && code !== "pronto" && code !== "escrevendo" ? detail : label;
  if (detail && (code === "offline" || code === "erro" || code === "limite" || code === "sem_chave" || code === "conectando" || code === "quieto")) {
    statusEl.textContent = detail;
  }
}

function stripChatMarks(text) {
  return String(text || "")
    .replace(/\{\{\s*chat\s*\}\}/gi, "")
    .replace(/\{\{\s*\/\s*chat\s*\}\}/gi, "")
    .replace(/\{\{[^}]*$/g, "");
}

function findEmote(part) {
  const direct = emotes.get(part);
  if (direct) return { before: "", name: part, after: "", url: direct };
  const wrapped = part.match(/^(["'“”«»‘’(]*)(.*?)(["'“”«»‘’.,!?;:…)]*)$/);
  if (!wrapped) return null;
  const [, before, name, after] = wrapped;
  if (!name || name === part) return null;
  const url = emotes.get(name);
  if (!url) return null;
  return { before, name, after, url };
}

function appendRich(container, text, chat) {
  if (!text) return;
  const parent = chat ? document.createElement("span") : container;
  if (chat) {
    parent.className = "chat-prose";
    container.appendChild(parent);
  }
  const parts = String(text).split(/(\s+)/);
  parts.forEach((part) => {
    if (/^\s+$/.test(part)) {
      parent.appendChild(document.createTextNode(part));
      return;
    }
    const found = findEmote(part);
    if (!found) {
      parent.appendChild(document.createTextNode(part));
      return;
    }
    if (found.before) parent.appendChild(document.createTextNode(found.before));
    const img = document.createElement("img");
    img.src = found.url;
    img.alt = found.name;
    img.title = found.name;
    img.className = "chat-emote";
    img.loading = "lazy";
    parent.appendChild(img);
    if (found.after) parent.appendChild(document.createTextNode(found.after));
  });
}

function renderMessageWithEmotes(container, text) {
  container.textContent = "";
  let source = String(text || "").replace(/\{\{[^}]*$/g, "");
  const pattern = /\{\{\s*chat\s*\}\}([\s\S]*?)\{\{\s*\/\s*chat\s*\}\}/gi;
  let last = 0;
  for (const match of source.matchAll(pattern)) {
    appendRich(container, source.slice(last, match.index), false);
    appendRich(container, match[1], true);
    last = match.index + match[0].length;
  }
  const rest = source.slice(last);
  const unclosed = rest.search(/\{\{\s*chat\s*\}\}/i);
  if (unclosed === -1) {
    appendRich(container, rest.replace(/\{\{\s*\/\s*chat\s*\}\}/gi, ""), false);
    return;
  }
  const open = rest.slice(unclosed).match(/\{\{\s*chat\s*\}\}/i);
  appendRich(container, rest.slice(0, unclosed), false);
  appendRich(container, rest.slice(unclosed + open[0].length), true);
}

function showParagraphs(items) {
  pagesEl.textContent = "";
  items.forEach((item) => appendParagraph(item, false));
  emptyEl.hidden = items.length > 0;
  if (!items.length) {
    draftEl.hidden = true;
    draftEl.dataset.raw = "";
  }
}

function appendParagraph(item, track) {
  const existing = pagesEl.querySelector(`[data-seq="${item.seq}"]`);
  const node = existing || document.createElement("p");
  node.className = "para";
  node.dataset.seq = String(item.seq);
  node.dataset.raw = item.text;
  renderMessageWithEmotes(node, item.text);
  if (!existing) pagesEl.appendChild(node);
  emptyEl.hidden = true;
  draftEl.hidden = true;
  draftEl.dataset.raw = "";
  if (track !== false && current) remember(current, item.text);
}

function showDraft(text) {
  draftEl.dataset.raw = text || "";
  if (!text) {
    draftEl.hidden = true;
    draftEl.textContent = "";
    return;
  }
  draftEl.hidden = false;
  renderMessageWithEmotes(draftEl, text);
}

function renderPersonas() {
  personasEl.textContent = "";
  personas.forEach((persona) => {
    const button = document.createElement("button");
    button.type = "button";
    const active = current && current.style === persona.id;
    const composing = persona.id === "custom" && customOpen && !active;
    button.className = "persona" + (active ? " active" : "") + (composing ? " composing" : "");
    const name = document.createElement("strong");
    name.textContent = persona.name;
    const blurb = document.createElement("span");
    blurb.textContent = persona.blurb;
    button.append(name, blurb);
    button.addEventListener("click", () => choosePersona(persona.id));
    personasEl.appendChild(button);
  });
}

function trashIcon() {
  const svg = document.createElementNS("http://www.w3.org/2000/svg", "svg");
  svg.setAttribute("viewBox", "0 0 24 24");
  svg.setAttribute("aria-hidden", "true");
  const path = document.createElementNS("http://www.w3.org/2000/svg", "path");
  path.setAttribute("fill", "none");
  path.setAttribute("stroke", "currentColor");
  path.setAttribute("stroke-width", "1.8");
  path.setAttribute("stroke-linecap", "round");
  path.setAttribute("stroke-linejoin", "round");
  path.setAttribute("d", "M4 7h16M9 7V5h6v2M7 7l1 13h8l1-13M10 11v6M14 11v6");
  svg.appendChild(path);
  return svg;
}

async function clearText(item) {
  const ok = window.confirm("Apagar o texto deste capítulo? A escrita recomeça da cena atual.");
  if (!ok) return;
  const response = await fetch(`${API}/sessions/${item.id}/paragraphs`, { method: "DELETE" });
  if (!response.ok) {
    setStatus("erro", "Não deu pra limpar o texto.");
    return;
  }
  const shelf = loadShelf().map((entry) => (
    entry.id === item.id ? { ...entry, preview: "" } : entry
  ));
  saveShelf(shelf);
  renderShelf();
  if (current && current.id === item.id) {
    showParagraphs([]);
    showDraft("");
    setStatus("pronto", "Texto limpo. O capítulo recomeça da cena atual.");
  }
}

function renderShelf() {
  const items = loadShelf();
  shelfEl.textContent = "";
  if (items.length === 0) {
    const li = document.createElement("li");
    li.className = "none";
    li.textContent = "Nenhum capítulo ainda.";
    shelfEl.appendChild(li);
    return;
  }
  items.forEach((item) => {
    const li = document.createElement("li");
    li.className = "chapter" + (current && current.id === item.id ? " current" : "");
    const button = document.createElement("button");
    button.type = "button";
    button.className = "open";
    const title = document.createElement("strong");
    title.textContent = personaName(item.style);
    button.appendChild(title);
    if (item.preview) {
      const small = document.createElement("small");
      small.textContent = stripChatMarks(item.preview).slice(0, 80);
      button.appendChild(small);
    }
    button.addEventListener("click", () => openSession({
      id: item.id,
      style: item.style,
      customPrompt: item.customPrompt || "",
    }));
    const trash = document.createElement("button");
    trash.type = "button";
    trash.className = "trash";
    trash.setAttribute("aria-label", `Limpar o texto de ${personaName(item.style)}`);
    trash.appendChild(trashIcon());
    trash.addEventListener("click", () => clearText(item));
    li.append(button, trash);
    shelfEl.appendChild(li);
  });
}

function showCustom(open) {
  customOpen = open;
  customForm.hidden = !open;
  renderPersonas();
}

function updateCustomCount() {
  customCountEl.textContent = `${customPromptEl.value.length}/800`;
}

async function startCustom(event) {
  event.preventDefault();
  const prompt = customPromptEl.value.trim();
  if (prompt.length < 8) {
    setStatus("erro", "Escreve um pouco mais sobre a voz.");
    return;
  }
  if (current && current.style === "custom" && current.customPrompt === prompt) return;
  const response = await fetch(`${API}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ style: "custom", prompt }),
  });
  if (!response.ok) {
    let detail = "Não deu pra abrir essa voz.";
    try {
      const body = await response.json();
      if (typeof body.detail === "string") detail = body.detail;
    } catch (_err) {
      /* Keep the fallback sentence. */
    }
    setStatus("erro", detail);
    return;
  }
  const session = await response.json();
  remember(session, "");
  await openSession(session);
}

async function choosePersona(style) {
  if (style === "custom") {
    showCustom(true);
    if (current && current.style === "custom" && current.customPrompt) {
      customPromptEl.value = current.customPrompt;
      updateCustomCount();
    }
    customPromptEl.focus();
    return;
  }
  showCustom(false);
  if (current && current.style === style) return;
  const response = await fetch(`${API}/sessions`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ style }),
  });
  if (!response.ok) {
    setStatus("erro", "Não deu pra abrir um capítulo novo.");
    return;
  }
  const session = await response.json();
  remember(session, "");
  await openSession(session);
}

async function openSession(session) {
  current = {
    id: session.id,
    style: session.style,
    customPrompt: session.customPrompt || session.custom_prompt || "",
  };
  if (current.style === "custom") {
    showCustom(true);
    if (current.customPrompt) {
      customPromptEl.value = current.customPrompt;
      updateCustomCount();
    }
  } else {
    showCustom(false);
  }
  renderPersonas();
  renderShelf();
  showParagraphs([]);
  showDraft("");
  setStatus("conectando", "Conectando na live…");
  if (abort) abort.abort();
  abort = new AbortController();
  const signal = abort.signal;

  let response;
  try {
    response = await fetch(`${API}/sessions/${session.id}/events`, {
      headers: { Accept: "text/event-stream" },
      signal,
    });
  } catch (err) {
    if (err.name === "AbortError") return;
    setStatus("erro", "A página perdeu a ligação com o livrinho.");
    return;
  }
  if (response.status === 503) {
    const saved = await fetch(`${API}/sessions/${session.id}`);
    if (saved.ok) {
      const body = await saved.json();
      showParagraphs(body.paragraphs || []);
    }
    setStatus("limite", "O livrinho está lotado. Tenta de novo daqui a pouco.");
    return;
  }
  if (!response.ok || !response.body) {
    setStatus("erro", "Não deu pra acompanhar este capítulo.");
    return;
  }
  await readStream(response.body, signal);
}

async function readStream(body, signal) {
  const reader = body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const blocks = buffer.split("\n\n");
      buffer = blocks.pop() || "";
      blocks.forEach(handleBlock);
    }
  } catch (err) {
    if (err.name !== "AbortError") {
      setStatus("erro", "A página perdeu a ligação com o livrinho.");
    }
  }
}

function handleBlock(block) {
  let event = "message";
  const dataLines = [];
  block.split("\n").forEach((line) => {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) dataLines.push(line.slice(5).trim());
  });
  if (!dataLines.length || event === "ping") return;
  let data;
  try {
    data = JSON.parse(dataLines.join("\n"));
  } catch (_err) {
    return;
  }
  if (event === "hello") {
    if (current && typeof data.custom_prompt === "string") {
      current.customPrompt = data.custom_prompt;
      if (current.style === "custom") {
        customPromptEl.value = data.custom_prompt;
        updateCustomCount();
      }
    }
    showParagraphs(data.paragraphs || []);
    if (data.draft) showDraft(data.draft);
    setStatus(data.status || "pronto", data.detail || "");
    const last = (data.paragraphs || []).slice(-1)[0];
    if (current) remember(current, last ? last.text : current.customPrompt || "");
    return;
  }
  if (event === "status") {
    setStatus(data.status, data.detail);
    return;
  }
  if (event === "delta") {
    showDraft(data.text || "");
    return;
  }
  if (event === "paragraph") appendParagraph(data);
  if (event === "cleared") {
    showParagraphs([]);
    showDraft("");
    if (current) remember(current, "");
  }
}

async function fetch7TVEmotes() {
  for (const url of [GLOBAL_EMOTES, CHANNEL_EMOTES]) {
    try {
      const response = await fetch(url);
      if (!response.ok) continue;
      const payload = await response.json();
      (payload.emotes || []).forEach((emote) => {
        emotes.set(emote.name, `https://cdn.7tv.app/emote/${emote.id}/1x.webp`);
      });
    } catch (_err) {
      /* The prose still reads as text if 7TV is unreachable. */
    }
  }
  document.querySelectorAll("[data-raw]").forEach((node) => {
    if (node.dataset.raw) renderMessageWithEmotes(node, node.dataset.raw);
  });
}

customPromptEl.addEventListener("input", updateCustomCount);
customForm.addEventListener("submit", (event) => {
  startCustom(event);
});

async function boot() {
  const response = await fetch(`${API}/personas`);
  const body = await response.json();
  personas = body.personas || [];
  renderPersonas();
  renderShelf();
  fetch7TVEmotes();
  const shelf = loadShelf();
  if (shelf[0]) openSession(shelf[0]);
}

boot();
