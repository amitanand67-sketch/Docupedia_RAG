// DocupediaAI chat UI: streams answers over Server-Sent Events and renders citations.

const $ = (sel) => document.querySelector(sel);
const state = {
  // The conversation ID survives a page refresh in this tab, so the chat (and its memory) continues.
  sessionId: storageGet(sessionStorage, "sessionId") || newSession(),
  user: storageGet(localStorage, "demoUser"),
  busy: false,
};

function storageGet(store, key) {
  try { return store.getItem(key); } catch { return null; }
}
function storageSet(store, key, value) {
  try { store.setItem(key, value); } catch { /* private mode */ }
}
function newSession() {
  const id = crypto.randomUUID();
  storageSet(sessionStorage, "sessionId", id);
  return id;
}

function headers() {
  const h = { "Content-Type": "application/json" };
  if (state.user) h["X-Demo-User"] = state.user;
  return h;
}

function escapeHtml(text) {
  return text.replace(/[&<>"']/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]));
}

// Small Markdown subset: paragraphs, bullet/numbered lists, bold, inline code, and [n] citations.
function renderMarkdown(text) {
  const inline = (s) => escapeHtml(s)
    .replace(/\*\*(.+?)\*\*/g, "<strong>$1</strong>")
    .replace(/`([^`]+)`/g, "<code>$1</code>")
    .replace(/\[(\d{1,2})\]/g, '<button class="cite" data-n="$1">$1</button>');
  const out = [];
  let list = null;
  for (const line of text.split("\n")) {
    const bullet = line.match(/^\s*[-*]\s+(.*)/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)/);
    if (bullet || numbered) {
      const tag = bullet ? "ul" : "ol";
      if (list !== tag) { if (list) out.push(`</${list}>`); out.push(`<${tag}>`); list = tag; }
      out.push(`<li>${inline((bullet || numbered)[1])}</li>`);
      continue;
    }
    if (list) { out.push(`</${list}>`); list = null; }
    if (line.trim()) out.push(`<p>${inline(line.replace(/^#+\s*/, ""))}</p>`);
  }
  if (list) out.push(`</${list}>`);
  return out.join("");
}

function fileLink(source) {
  if (!source.url) return "#";
  if (!source.url.startsWith("/files/")) return source.url;
  const as = state.user ? `?as=${encodeURIComponent(state.user)}` : "";
  return source.url + as + (source.page ? `#page=${source.page}` : "");
}

function addUserMessage(text) {
  const el = document.createElement("div");
  el.className = "msg user";
  el.innerHTML = `<div class="bubble">${escapeHtml(text)}</div>`;
  $("#messages").appendChild(el);
}

function addAssistantMessage() {
  const el = document.createElement("div");
  el.className = "msg assistant";
  el.innerHTML = `<div class="rewrite hidden"></div><div class="bubble cursor"></div>
    <div class="sources"></div><div class="footer hidden"></div>`;
  $("#messages").appendChild(el);
  el.addEventListener("click", (e) => {
    const n = e.target.closest(".cite")?.dataset.n;
    const card = n && el.querySelector(`.source[data-n="${n}"]`);
    if (card) { card.classList.add("flash"); card.scrollIntoView({ block: "nearest", behavior: "smooth" }); setTimeout(() => card.classList.remove("flash"), 1200); }
  });
  return el;
}

function renderSources(el, event, question) {
  if (event.query && event.query !== question) {
    const r = el.querySelector(".rewrite");
    r.textContent = `Searched for: "${event.query}"`;
    r.classList.remove("hidden");
  }
  el.querySelector(".sources").innerHTML = event.sources.map((s) => `
    <a class="source" data-n="${s.n}" href="${fileLink(s)}" target="_blank" rel="noopener" title="${escapeHtml(s.snippet)}">
      <span class="n">${s.n}</span>
      <span><span>${escapeHtml(s.title)}</span>${s.kind === "image" ? '<span class="badge">image</span>' : ""}
      <span class="meta">${s.section ? `${escapeHtml(s.section)} · ` : ""}${s.page ? `page ${s.page} · ` : ""}${escapeHtml(s.source)}</span></span>
    </a>`).join("");
}

function renderFooter(el, event) {
  const f = el.querySelector(".footer");
  const tokens = event.prompt_tokens + event.completion_tokens;
  f.innerHTML = `<span>${(event.latency_ms / 1000).toFixed(1)} s · ${tokens.toLocaleString()} tokens</span>
    <button data-rating="1" title="Helpful">👍</button><button data-rating="-1" title="Not helpful">👎</button>`;
  f.classList.remove("hidden");
  f.querySelectorAll("button").forEach((b) => b.addEventListener("click", async () => {
    f.querySelectorAll("button").forEach((x) => x.classList.remove("chosen"));
    b.classList.add("chosen");
    await fetch("/api/feedback", {
      method: "POST", headers: headers(),
      body: JSON.stringify({ session_id: state.sessionId, message_id: event.message_id, rating: Number(b.dataset.rating) }),
    });
  }));
}

async function ask(question) {
  if (state.busy || !question.trim()) return;
  state.busy = true;
  $("#send").disabled = true;
  $("#empty")?.remove();
  addUserMessage(question);
  const el = addAssistantMessage();
  const bubble = el.querySelector(".bubble");
  let answer = "";
  const scroll = () => { $("#messages").scrollTop = $("#messages").scrollHeight; };
  scroll();

  try {
    const resp = await fetch("/api/chat", {
      method: "POST", headers: headers(), body: JSON.stringify({ question, session_id: state.sessionId }),
    });
    if (!resp.ok) {
      let detail = "";
      try { detail = (await resp.json()).detail; } catch { /* not JSON */ }
      throw new Error(detail || `HTTP ${resp.status}`);
    }
    const reader = resp.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      const parts = buffer.split("\n\n");
      buffer = parts.pop();
      for (const part of parts) {
        if (!part.startsWith("data: ")) continue;
        const event = JSON.parse(part.slice(6));
        if (event.type === "sources") renderSources(el, event, question);
        if (event.type === "token") { answer += event.text; bubble.innerHTML = renderMarkdown(answer); }
        if (event.type === "done") renderFooter(el, event);
        if (event.type === "error") throw new Error(event.message);
        scroll();
      }
    }
  } catch (err) {
    bubble.innerHTML += `<p class="error">Something went wrong: ${escapeHtml(String(err.message || err))}</p>`;
  } finally {
    bubble.classList.remove("cursor");
    state.busy = false;
    $("#send").disabled = false;
    $("#question").focus();
  }
}

async function restoreConversation() {
  const resp = await fetch(`/api/history?session_id=${encodeURIComponent(state.sessionId)}`, { headers: headers() });
  if (!resp.ok) return;
  const { messages } = await resp.json();
  if (!messages.length) return;
  $("#empty")?.remove();
  for (const m of messages) {
    if (m.role === "user") { addUserMessage(m.content); continue; }
    const el = addAssistantMessage();
    const bubble = el.querySelector(".bubble");
    bubble.classList.remove("cursor");
    bubble.innerHTML = renderMarkdown(m.content);
  }
  $("#messages").scrollTop = $("#messages").scrollHeight;
}

async function loadUser() {
  const me = await (await fetch("/api/me", { headers: headers() })).json();
  const select = $("#user-select");
  if (me.auth_mode === "demo") {
    select.innerHTML = me.demo_users.map((u) => `<option ${u === me.name ? "selected" : ""}>${escapeHtml(u)}</option>`).join("");
    select.classList.remove("hidden");
    $("#user-label").textContent = "Demo user";
  } else {
    $("#user-name").textContent = me.name;
  }
  $("#analytics-tab").classList.toggle("hidden", !me.is_admin);
  if (!me.is_admin) showView("chat");
}

function showView(view) {
  document.querySelectorAll(".tab").forEach((t) => t.classList.toggle("active", t.dataset.view === view));
  $("#chat-view").classList.toggle("hidden", view !== "chat");
  $("#analytics-view").classList.toggle("hidden", view !== "analytics");
  if (view === "analytics") loadAnalytics();
}

async function loadAnalytics() {
  const resp = await fetch("/api/analytics", { headers: headers() });
  if (!resp.ok) { $("#tiles").innerHTML = `<p class="error">${resp.status}: admins only</p>`; return; }
  const a = await resp.json();
  const tile = (label, value) => `<div class="tile"><div class="label">${label}</div><div class="value">${value}</div></div>`;
  $("#tiles").innerHTML = [
    tile("Questions", a.questions),
    tile("Users", a.users),
    tile("Avg latency", `${(a.avg_latency_ms / 1000).toFixed(1)} s`),
    tile("p95 latency", `${(a.p95_latency_ms / 1000).toFixed(1)} s`),
    tile("Avg tokens / answer", a.avg_tokens.toLocaleString()),
    tile("Satisfaction", a.satisfaction === null ? "n/a" : `${a.satisfaction}%`),
    tile("No sources found", a.unanswered),
  ].join("");
  $("#top-sources").innerHTML = a.top_sources.map(([s, n]) => `<li>${escapeHtml(s)} <span class="muted">(${n})</span></li>`).join("")
    || '<li class="muted">No questions yet</li>';
  $("#recent").innerHTML = a.recent.map((r) => `<tr>
      <td>${new Date(r.ts * 1000).toLocaleTimeString()}</td><td>${escapeHtml(r.user)}</td>
      <td>${escapeHtml(r.question)}</td><td>${(r.latency_ms / 1000).toFixed(1)} s</td>
      <td>${r.rating === 1 ? "👍" : r.rating === -1 ? "👎" : ""}</td></tr>`).join("");
}

$("#composer").addEventListener("submit", (e) => {
  e.preventDefault();
  const q = $("#question").value;
  $("#question").value = "";
  ask(q);
});
$("#question").addEventListener("keydown", (e) => {
  if (e.key === "Enter" && !e.shiftKey) { e.preventDefault(); $("#composer").requestSubmit(); }
});
$("#question").addEventListener("input", (e) => {
  e.target.style.height = "auto";
  e.target.style.height = `${e.target.scrollHeight}px`;
});
document.querySelectorAll(".suggestions button").forEach((b) => b.addEventListener("click", () => ask(b.textContent)));
document.querySelectorAll(".tab").forEach((t) => t.addEventListener("click", () => showView(t.dataset.view)));
$("#new-chat").addEventListener("click", () => { newSession(); location.reload(); });
$("#user-select").addEventListener("change", (e) => {
  state.user = e.target.value;
  storageSet(localStorage, "demoUser", state.user);
  state.sessionId = newSession();
  $("#messages").innerHTML = "";
  loadUser().then(restoreConversation);
});

loadUser().then(restoreConversation);
