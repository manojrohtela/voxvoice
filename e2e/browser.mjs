// E2E browser journey (M12.10): a real Chrome, driven over the DevTools protocol, through the real
// console UI served by `e2e/serve.py`. No dependency: Node's built-in WebSocket and fetch.
//
// Why this exists: `chrome --screenshot` cannot sign in and hung after one capture (M12.2–M12.9).
// Driving one long-lived page over CDP signs in through the UI itself (a real HttpOnly cookie),
// captures any number of screenshots, and records console errors and failed requests.
//
//   node e2e/browser.mjs --base http://127.0.0.1:8790 --out <dir>
//
// Exit code 0 only if every check passed. Screenshots and `report.json` are written to --out and
// are NOT committed (the key-creation screenshot contains a throwaway key).

import { mkdirSync, writeFileSync } from "node:fs";
import { join } from "node:path";

import { launch, sleep } from "./cdp.mjs";

const args = Object.fromEntries(process.argv.slice(2).reduce((acc, v, i, a) => (v.startsWith("--") ? [...acc, [v.slice(2), a[i + 1]]] : acc), []));
const BASE = (args.base ?? "http://127.0.0.1:8790").replace(/\/$/, "");
const OUT = args.out ?? "e2e-out";
const CHROME = args.chrome ?? "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const PORT = Number(args["cdp-port"] ?? 9333);
mkdirSync(OUT, { recursive: true });

const checks = [];
const consoleErrors = [];
const serverErrors = [];
const notFound = [];
const requests = [];
function check(name, ok, detail = "") {
  checks.push({ name, ok: Boolean(ok), detail: String(detail).slice(0, 300) });
  console.log(`${ok ? "PASS" : "FAIL"}  ${name}${detail && !ok ? `  — ${String(detail).slice(0, 200)}` : ""}`);
}

// ── Chrome + CDP ──────────────────────────────────────────────────────────────

const browser = await launch({
  chrome: CHROME, port: PORT, profileDir: OUT,
  onEvent(method, params) {
    if (method === "Runtime.exceptionThrown") consoleErrors.push(params.exceptionDetails.exception?.description ?? params.exceptionDetails.text);
    if (method === "Runtime.consoleAPICalled" && params.type === "error") consoleErrors.push(params.args.map((a) => a.value ?? a.description).join(" "));
    // The app's own 4xx are observed through `requests`; Chrome logs every one as a "network" error.
    if (method === "Log.entryAdded" && params.entry.level === "error" && params.entry.source !== "network") consoleErrors.push(`${params.entry.source}: ${params.entry.text} ${params.entry.url ?? ""}`);
    if (method === "Network.responseReceived") {
      const { url, status } = params.response;
      if (url.startsWith(BASE)) requests.push({ url: url.slice(BASE.length), status });
      if (status === 404) notFound.push(url);
      if (status >= 500) serverErrors.push(`${status} ${url}`);
    }
  },
});
const { send, evaluate } = browser;

async function go(path) {
  await browser.navigate(`${BASE}${path}`);
  await installHelpers();
}
async function waitFor(expression, timeoutMs = 10_000) {
  const end = Date.now() + timeoutMs;
  for (;;) {
    try { await installHelpers(); if (await evaluate(expression)) return true; } catch { /* page in transition */ }
    if (Date.now() > end) return false;
    await sleep(150);
  }
}
let shot = 0;
async function screenshot(name) {
  // A tall page (the diagnostics log) can exceed Chrome's capture limit; fall back to the viewport.
  let data;
  try {
    ({ data } = await send("Page.captureScreenshot", { format: "png", captureBeyondViewport: true }));
  } catch {
    ({ data } = await send("Page.captureScreenshot", { format: "png" }));
  }
  const file = `${String(++shot).padStart(2, "0")}-${name}.png`;
  writeFileSync(join(OUT, file), Buffer.from(data, "base64"));
  return file;
}
async function viewport(width, height = 900) {
  await send("Emulation.setDeviceMetricsOverride", { width, height, deviceScaleFactor: 1, mobile: width < 500 });
}

// DOM helpers, installed into the page: find controls by their visible label, buttons/links by text.
async function installHelpers() {
  await evaluate(`window.__e2e || (window.__e2e = {
    norm: (s) => (s || "").replace(/\\s+/g, " ").trim(),
    control(label) {
      for (const l of document.querySelectorAll("label")) {
        const own = [...l.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" ");
        if (this.norm(own) === label || this.norm(l.textContent) === label) {
          return l.control || l.querySelector("input,select,textarea") || (l.htmlFor && document.getElementById(l.htmlFor));
        }
      }
      return null;
    },
    card(heading) {
      return [...document.querySelectorAll("article")].find((a) => this.norm(document.getElementById(a.getAttribute("aria-labelledby"))?.textContent) === heading) || null;
    },
    fill(label, value, within) {
      const scope = within ? this.card(within) : null;
      if (within && !scope) throw new Error("no card " + within);
      const el = scope ? (() => { const labels = [...scope.querySelectorAll("label")]; for (const l of labels) { const own = [...l.childNodes].filter((n) => n.nodeType === 3).map((n) => n.textContent).join(" "); if (this.norm(own) === label || this.norm(l.textContent) === label) return l.control || l.querySelector("input,select,textarea"); } return null; })() : this.control(label);
      if (!el) throw new Error("no control labelled " + label);
      const proto = el.tagName === "SELECT" ? HTMLSelectElement.prototype : el.tagName === "TEXTAREA" ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
      Object.getOwnPropertyDescriptor(proto, "value").set.call(el, value);
      el.dispatchEvent(new Event(el.tagName === "SELECT" ? "change" : "input", { bubbles: true }));
      return true;
    },
    find(text, within) {
      const root = within ? [...document.querySelectorAll("article")].find((a) => a.getAttribute("aria-labelledby") && this.norm(document.getElementById(a.getAttribute("aria-labelledby"))?.textContent) === within) : document;
      if (!root) return null;
      return [...root.querySelectorAll("button,a")].find((e) => this.norm(e.textContent) === text || e.getAttribute("aria-label") === text) || null;
    },
    click(text, within) { const e = this.find(text, within); if (!e) throw new Error("nothing to click: " + text); e.click(); return true; },
    has(text) { return document.body && document.body.innerText.includes(text); },
    h1() { return this.norm(document.querySelector("h1")?.textContent); },
  })`);
}
const has = (text) => evaluate(`__e2e.has(${JSON.stringify(text)})`);
const h1 = () => evaluate("__e2e.h1()");
const fill = (label, value, within) => evaluate(`__e2e.fill(${JSON.stringify(label)}, ${JSON.stringify(value)}, ${JSON.stringify(within ?? null)})`);
const click = (text, within) => evaluate(`__e2e.click(${JSON.stringify(text)}, ${JSON.stringify(within ?? null)})`);
const page = async () => evaluate("location.pathname + location.search");

// Accessibility smoke for the current page: every button/link has a name, every field a label.
async function a11y(name) {
  const problems = await evaluate(`(() => {
    const out = [];
    for (const e of document.querySelectorAll("button,a")) if (!__e2e.norm(e.textContent) && !e.getAttribute("aria-label")) out.push(e.outerHTML.slice(0, 80));
    for (const e of document.querySelectorAll("input,select,textarea")) if (!e.labels?.length && !e.getAttribute("aria-label")) out.push(e.outerHTML.slice(0, 80));
    if (document.documentElement.scrollWidth > window.innerWidth + 1) out.push("horizontal overflow: " + document.documentElement.scrollWidth + " > " + window.innerWidth);
    return out;
  })()`);
  check(`a11y/layout: ${name}`, problems.length === 0, problems.join(" | "));
}

// ── the journey ───────────────────────────────────────────────────────────────

const stamp = Date.now().toString(36);
const A = { email: `ada-${stamp}@example.com`, org: `Analytical ${stamp}`, agent: `Support ${stamp}` };
const PASSWORD = "correct horse battery";
let orgB;

try {
  // 1. Signed out: protected routes redirect to sign-in and show no data.
  for (const path of ["/console/", "/console/agents", "/console/conversations", "/console/analytics", "/console/developers"]) {
    await go(path);
    const ok = await waitFor(`location.pathname === "/console/login"`);
    check(`signed out ${path} → sign-in`, ok, await page());
  }
  check("sign-in page renders", (await h1()) === "Sign in");
  await screenshot("signin");
  await a11y("sign-in");

  // 2. Sign up → Overview (empty organization).
  await go("/console/signup");
  await waitFor(`!!__e2e.control("Email")`);
  await fill("Your name", "Ada"); await fill("Email", A.email); await fill("Password", PASSWORD); await fill("Organization name", A.org);
  await click("Create account");
  check("sign up → Overview", await waitFor(`__e2e.h1() === "Overview" && __e2e.has("Summary")`, 15_000), await page());
  check("empty org: needs attention → create an agent", await has("No agents yet"));
  await screenshot("overview-empty");
  await a11y("overview (empty)");

  // 3. Agents: create → list → edit → persists.
  await go("/console/agents/new");
  await waitFor(`!!__e2e.control("Name")`);
  await fill("Name", A.agent); await fill("Instructions", "You are a helpful support agent.");
  await fill("Greeting", "Hello from the E2E harness.");
  await click("Create agent");
  check("agent created → editor", await waitFor(`/\\/console\\/agents\\/agt_/.test(location.pathname)`, 10_000), await page());
  const agentId = (await page()).split("/").pop();
  await fill("Greeting", "Hello again."); await click("Save changes");
  await sleep(800);
  await go(`/console/agents/${agentId}`);
  check("edited greeting persists after reload", await waitFor(`__e2e.control("Greeting")?.value === "Hello again."`));
  await screenshot("agent-editor");
  await a11y("agent editor");
  await go("/console/agents");
  check("agent in list", await waitFor(`__e2e.has(${JSON.stringify(A.agent)})`));
  await screenshot("agents");

  // 3b. Starter templates (M12.13): gallery → preview → one ordinary agent, editable like any
  // other. The template is product data, so what this proves is that "use" is just a create.
  check("agents page offers the starter templates", await waitFor(`__e2e.has("Starter templates") && __e2e.has("VoxVoice demo assistant")`));
  await evaluate(`[...document.querySelectorAll("a")].find((a) => a.getAttribute("href") === "/console/agents/templates/voxvoice_demo").click()`);
  check("template preview opens without creating anything", await waitFor(`__e2e.h1() === "VoxVoice demo assistant" && __e2e.has("Use this template")`, 10_000), await page());
  check("preview shows the instructions the agent will start with", await evaluate(`(document.querySelector(".template-prompt")?.textContent ?? "").includes("You are a demonstration assistant for VoxVoice")`));
  await screenshot("template-preview");
  await a11y("template preview");
  const agentsBefore = await evaluate(`fetch("/v1/console/agents").then((r) => r.json()).then((b) => b.data.length)`);
  await click("Use this template");
  check("use template → the normal agent editor", await waitFor(`/\\/console\\/agents\\/agt_/.test(location.pathname) && !!__e2e.control("Instructions")`, 10_000), await page());
  const templateAgentId = (await page()).split("/").pop();
  check("it created exactly one agent", (await evaluate(`fetch("/v1/console/agents").then((r) => r.json()).then((b) => b.data.length)`)) === agentsBefore + 1);
  check("the agent carries the template's greeting, and is editable", await waitFor(`__e2e.control("Greeting")?.value.startsWith("Hi, VoxVoice here.")`));
  await fill("Name", "Demo, renamed"); await click("Save changes");
  await sleep(800);
  await go(`/console/agents/${templateAgentId}`);
  check("an agent from a template edits and persists like any other", await waitFor(`__e2e.control("Name")?.value === "Demo, renamed"`));
  // The template is untouched by that edit: the next preview still shows its own values.
  await go("/console/agents/templates/voxvoice_demo");
  check("editing the agent did not change the template", await waitFor(`__e2e.h1() === "VoxVoice demo assistant"`, 10_000) && !(await has("Demo, renamed")));
  await go("/console/agents");
  await screenshot("agents-with-template");

  // 4. Playground: a real browser WebRTC call through the console (deterministic providers).
  await go(`/console/playground?agent=${agentId}`);
  check("playground preselects the agent", await waitFor(`document.getElementById("playground-agent")?.value === ${JSON.stringify(agentId)}`));
  await screenshot("playground-idle");
  await click("Start call");
  const connected = await waitFor(`__e2e.has("Connected")`, 30_000);
  check("browser WebRTC call connects (signalling token only)", connected, await evaluate("document.body.innerText.slice(0, 400)"));
  await sleep(3000);
  await screenshot("playground-connected");
  await a11y("playground (in call)");
  await click("Hang up");
  // M12.14: while the call is up, the diagnostics stream is a view of the durable log.
  check("live diagnostics arrive during the call", await waitFor(`__e2e.has("Diagnostics") && document.querySelectorAll(".diag__row").length > 0`, 20_000), await evaluate(`document.querySelectorAll(".diag__row").length + " rows"`));
  check("runtime state is shown", await waitFor(`!!document.querySelector(".runtime-steps")`));
  // Every rendered row is one of the runtime's own events — no invented label, no "unknown".
  const KNOWN = ["Runtime started","Session started","Caller connected","Audio flowing","User speech detected","User speech stopped","End of speech confirmed","End of speech cancelled","Turn-taking decision","Turn started","Agent started speaking","Agent stopped speaking","Turn completed","Turn cancelled","Possible interruption","Caller interrupted","Interruption withdrawn","Provider ready","Provider unavailable","Turn latency measured","Caller disconnected","Media failed","Runtime error","Session ended","Runtime stopped"];
  const unknown = await evaluate(`(() => { const known = ${JSON.stringify(KNOWN)}; return [...document.querySelectorAll(".diag__message")].map((e) => e.textContent.trim()).filter((t) => !known.includes(t)); })()`);
  check("every diagnostic names a runtime event", Array.isArray(unknown) && unknown.length === 0, JSON.stringify(unknown ?? []).slice(0, 200));
  check("the panel bounds what it renders", await evaluate(`document.querySelectorAll(".diag__row").length <= 300`), await evaluate(`document.querySelectorAll(".diag__row").length + " rendered"`));
  await screenshot("playground-diagnostics");
  await a11y("playground diagnostics");
  const liveRows = await evaluate(`document.querySelectorAll(".diag__row").length`);

  check("hang up → call ended", await waitFor(`__e2e.has("Call ended")`, 15_000));
  const conversationHref = await evaluate(`__e2e.find("View this conversation")?.getAttribute("href")`);
  check("playground links to the conversation", /\/console\/conversations\/ses_/.test(conversationHref ?? ""), conversationHref);
  // After the call the diagnostics remain, and the session's end is in them.
  check("diagnostics remain after the call, and grew", await waitFor(`document.querySelectorAll(".diag__row").length >= ${liveRows}`, 15_000));
  check("the session end is in the diagnostics", await waitFor(`__e2e.has("Session ended")`, 20_000));
  check("no diagnostic exposes a credential", !(await evaluate(`/sk-[A-Za-z0-9]{8,}|vf_[A-Za-z0-9]{8,}|Bearer\\s/.test(document.querySelector(".diag")?.textContent ?? "")`)));
  await evaluate(`[...document.querySelectorAll(".diag__expand")].at(-1)?.click()`);
  check("a diagnostic expands to its structured detail", await waitFor(`!!document.querySelector(".diag__detail")`));
  await screenshot("playground-diagnostics-after");
  const signalling = requests.filter((r) => r.url.startsWith("/v1/webrtc/rendezvous/"));
  check("browser used the signalling endpoints", signalling.length >= 2, JSON.stringify(signalling.slice(0, 3)));
  await click("View this conversation");

  // 5. Conversation detail.
  check("conversation detail opens", await waitFor(`__e2e.has("Transcript") && __e2e.has("Timeline")`, 10_000), await page());
  check("conversation is from the Playground and ended", (await has("Playground")) && (await has("ended")));
  check("conversation recorded runtime events", !(await has("No runtime events were recorded")));
  await screenshot("conversation-detail");
  await a11y("conversation detail");
  await go(`/console/conversations?agent=${agentId}`);
  check("conversations list filtered by agent", await waitFor(`document.querySelectorAll("tbody tr").length === 1`));
  await screenshot("conversations");

  // 6. Analytics and Overview show it, identically.
  await go("/console/analytics?range=today");
  check("analytics counts the conversation", await waitFor(`__e2e.has("Conversations") && [...document.querySelectorAll(".stat")].some((s) => s.innerText.startsWith("Conversations\\n1"))`, 10_000));
  await screenshot("analytics");
  await a11y("analytics");
  const analytics = await evaluate(`[...document.querySelectorAll(".stat")].map((s) => s.innerText.replace(/\\n/g, " | "))`);
  await go("/console/?range=today");
  check("overview loads with data", await waitFor(`[...document.querySelectorAll(".stat")].some((s) => s.innerText.startsWith("Conversations\\n1"))`, 10_000));
  const overview = await evaluate(`[...document.querySelectorAll(".stat")].map((s) => s.innerText.replace(/\\n/g, " | "))`);
  const pick = (list, label) => list.find((s) => s.startsWith(label));
  for (const label of ["Conversations", "Turns", "Avg duration"]) {
    check(`overview ${label} = analytics ${label}`, pick(overview, label)?.split(" | ")[1] === pick(analytics, label)?.split(" | ")[1], `${pick(overview, label)} vs ${pick(analytics, label)}`);
  }
  check("overview recent conversations link to the conversation", await evaluate(`[...document.querySelectorAll("a")].some((a) => a.getAttribute("href") === ${JSON.stringify(conversationHref)})`));
  await screenshot("overview");

  // The transport is the only thing /v1/console/meta proves. This server runs the real WebRTC
  // transport with *deterministic* providers, so a page that claimed "real providers" here would
  // be claiming something false about this very run (M12.12 defect 1).
  check("no page claims real providers", !(await has("real providers")) && !(await has("no real voice")), await evaluate(`document.querySelector(".topbar .pill")?.textContent`));
  check("the runtime card says what the transport is", await has("WebRTC transport"));
  // The same wording in both themes: the label is text, the theme is only tokens.
  for (const scheme of ["dark", "light"]) {
    await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-color-scheme", value: scheme }] });
    await go("/console/?range=today");
    await waitFor(`__e2e.has("WebRTC transport")`, 10_000);
    check(`${scheme} theme: transport label unchanged, no provider claim`, !(await has("real providers")));
    await screenshot(`overview-${scheme}`);
  }
  await send("Emulation.setEmulatedMedia", { features: [] });

  // 7. API keys: create → shown once → Done → gone; docs.
  await go("/console/developers");
  await waitFor(`!!__e2e.control("Label")`);
  await fill("Label", "e2e-backend"); await click("Create key");
  check("key shown once", await waitFor(`!!document.getElementById("new-key-value")?.value?.startsWith("vf_")`, 10_000));
  const key = await evaluate(`document.getElementById("new-key-value").value`);
  await screenshot("api-key-created");
  await click("Done");
  check("key gone from the page after Done", !(await evaluate(`document.body.innerHTML.includes(${JSON.stringify(key)})`)));
  const stored = await evaluate(`JSON.stringify({...localStorage}) + JSON.stringify({...sessionStorage}) + location.href`);
  check("key not in storage or URL", !stored.includes(key));
  await go("/console/developers");
  check("key not recoverable after reload", await waitFor(`__e2e.has("e2e-backend")`) && !(await evaluate(`document.body.innerHTML.includes(${JSON.stringify(key)})`)));
  // The key works on the developer API, and the console cookie does not.
  const devOk = await fetch(`${BASE}/v1/agents`, { headers: { "x-api-key": key } });
  check("created key authenticates the developer API", devOk.status === 200);
  const cookieOnly = await evaluate(`fetch("/v1/agents").then((r) => r.status)`);
  check("console cookie does not authenticate the developer API", cookieOnly === 401, cookieOnly);
  const keyOnConsole = await fetch(`${BASE}/v1/console/agents`, { headers: { "x-api-key": key } });
  check("API key does not open the console API", keyOnConsole.status === 401);
  await go("/console/developers/docs");
  check("developer docs render", await waitFor(`__e2e.h1() === "Developer docs" && __e2e.has("TypeScript SDK")`));
  check("docs reference is generated from the server", await waitFor(`document.querySelectorAll("article#reference tbody tr, #reference tbody tr").length >= 9`, 10_000));
  await screenshot("developer-docs");
  await a11y("developer docs");
  // Revoke in the UI → the key fails at once.
  await go("/console/developers");
  await evaluate(`window.confirm = () => true`);
  await waitFor(`!!__e2e.find("Revoke e2e-backend")`);
  await click("Revoke e2e-backend");
  check("revoked key disappears", await waitFor(`__e2e.has("No API keys yet")`));
  check("revoked key → 401 on the developer API", (await fetch(`${BASE}/v1/agents`, { headers: { "x-api-key": key } })).status === 401);

  // 8. Settings → second organization → switch: nothing of A remains, anywhere.
  await go("/console/settings");
  await waitFor(`!!__e2e.control("Name")`);
  orgB = `Babbage ${stamp}`;
  await fill("Name", orgB, "New organization"); await click("Create", "New organization");
  check("second organization created and switched to", await waitFor(`__e2e.has(${JSON.stringify(orgB)})`, 10_000));
  await screenshot("settings");
  await a11y("settings");
  for (const path of ["/console/", "/console/agents", "/console/conversations", `/console/conversations?agent=${agentId}`, "/console/analytics", "/console/developers"]) {
    await go(path);
    await sleep(900);
    const leaked = await has(A.agent);
    check(`org B ${path} shows nothing of org A`, !leaked);
  }
  // Switch back through the top-bar switcher, from a page showing B's (empty) data.
  await go("/console/agents");
  await waitFor(`!!document.querySelector(".topbar select")`);
  const orgA = await evaluate(`[...document.querySelector(".topbar select").options].find((o) => o.textContent === ${JSON.stringify(A.org)}).value`);
  await evaluate(`(() => { const s = document.querySelector(".topbar select"); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(s, ${JSON.stringify(orgA)}); s.dispatchEvent(new Event("change", { bubbles: true })); return true; })()`);
  check("switch B → A in place reloads A's agents", await waitFor(`__e2e.has(${JSON.stringify(A.agent)})`, 10_000));
  // And A → B in place, with an A-only filter in the URL: it must be dropped.
  await go(`/console/conversations?agent=${agentId}`);
  await waitFor(`document.querySelectorAll("tbody tr").length === 1`);
  const orgBId = await evaluate(`[...document.querySelector(".topbar select").options].find((o) => o.textContent === ${JSON.stringify(orgB)}).value`);
  await evaluate(`(() => { const s = document.querySelector(".topbar select"); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(s, ${JSON.stringify(orgBId)}); s.dispatchEvent(new Event("change", { bubbles: true })); return true; })()`);
  check("switch A → B in place: A's conversation is gone", await waitFor(`!document.querySelector("tbody tr") && location.search === ""`, 10_000), await page());
  await screenshot("org-switched");

  // 9. Responsive: narrow desktop / tablet / phone on the data-heaviest pages (back in A).
  await evaluate(`(() => { const s = document.querySelector(".topbar select"); Object.getOwnPropertyDescriptor(HTMLSelectElement.prototype, "value").set.call(s, ${JSON.stringify(orgA)}); s.dispatchEvent(new Event("change", { bubbles: true })); return true; })()`);
  await sleep(1000);
  // 500 px, the conversation detail and the Playground were added in M12.12A: the sweep used to
  // stop at four pages and three widths, which is how a Turns table that set a 775 px minimum
  // width — and scrolled the whole page between ~500 and ~770 px — went unmeasured (defect 2).
  for (const width of [1024, 768, 500, 390]) {
    await viewport(width);
    for (const path of [
      "/console/?range=today",
      "/console/analytics?range=today",
      `/console/agents/${agentId}`,
      "/console/developers/docs",
      conversationHref ?? "/console/conversations",
      "/console/conversations",
      "/console/playground",
      // M12.13: the template gallery (a card grid) and the preview (a long prompt in a <pre>).
      "/console/agents",
      "/console/agents/templates/sales_representative",
    ]) {
      await go(path);
      await sleep(900);
      await a11y(`${width}px ${path}`);
    }
    await go("/console/?range=today"); await sleep(900);
    await screenshot(`overview-${width}`);
  }
  await viewport(1280);

  // 10. Sign out → protected pages are closed again, and the old cookie is dead.
  await go("/console/settings");
  await waitFor(`!!__e2e.find("Sign out")`);
  await click("Sign out");
  check("sign out → sign-in", await waitFor(`location.pathname === "/console/login"`, 10_000));
  await go("/console/agents");
  check("after sign out, protected route → sign-in", await waitFor(`location.pathname === "/console/login"`));
  const me = await evaluate(`fetch("/v1/console/me").then((r) => r.status)`);
  check("after sign out, /v1/console/me → 401", me === 401, me);
} catch (error) {
  check("journey completed without an exception", false, error.stack ?? error);
  await screenshot("failure").catch(() => {});
}

check("no browser console errors", consoleErrors.length === 0, consoleErrors.join(" | "));
check("no 5xx responses", serverErrors.length === 0, serverErrors.join(" | "));
// Every 404 the journey saw must be one it asked for; none may come from the console itself.
check("no unexpected 404 (assets, favicon, API)", notFound.length === 0, notFound.join(" | "));

const failed = checks.filter((c) => !c.ok);
const byPage = {};
for (const r of requests) { const p = r.url.split("?")[0]; byPage[p] = (byPage[p] ?? 0) + 1; }
writeFileSync(join(OUT, "report.json"), JSON.stringify({ base: BASE, checks, consoleErrors, serverErrors, requestCounts: byPage }, null, 2));
console.log(`\n${checks.length - failed.length}/${checks.length} checks passed; screenshots in ${OUT}`);
browser.close();
process.exit(failed.length ? 1 : 0);
