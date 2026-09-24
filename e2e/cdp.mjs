// A minimal Chrome DevTools Protocol session (M12.10). No dependency: Node's built-in WebSocket.
//
// One long-lived headless page with a fake camera/microphone. It avoids `chrome --screenshot`,
// which cannot sign in and hung after one capture in M12.2–M12.9.

import { spawn } from "node:child_process";
import { join } from "node:path";

export const sleep = (ms) => new Promise((r) => setTimeout(r, ms));

export async function launch({ chrome, port, profileDir, onEvent = () => {} }) {
  const proc = spawn(chrome, [
    "--headless=new", `--remote-debugging-port=${port}`, `--user-data-dir=${join(profileDir, ".profile")}`,
    "--no-first-run", "--no-default-browser-check", "--disable-gpu", "--window-size=1280,900",
    "--use-fake-device-for-media-stream", "--use-fake-ui-for-media-stream", "about:blank",
  ], { stdio: "ignore" });

  let target;
  for (let i = 0; i < 100 && !target; i++) {
    try { target = (await (await fetch(`http://127.0.0.1:${port}/json/list`)).json()).find((t) => t.type === "page"); }
    catch { await sleep(100); }
  }
  if (!target) { proc.kill(); throw new Error("Chrome did not start"); }

  const ws = new WebSocket(target.webSocketDebuggerUrl);
  await new Promise((r) => ws.addEventListener("open", r, { once: true }));
  let nextId = 0;
  const pending = new Map();
  const waiters = [];
  ws.addEventListener("message", (msg) => {
    const data = JSON.parse(msg.data);
    if (data.id !== undefined) {
      const p = pending.get(data.id); pending.delete(data.id);
      data.error ? p.reject(new Error(data.error.message)) : p.resolve(data.result);
      return;
    }
    onEvent(data.method, data.params);
    for (const w of [...waiters]) if (w.method === data.method) { waiters.splice(waiters.indexOf(w), 1); w.resolve(data.params); }
  });
  const send = (method, params = {}) => new Promise((resolve, reject) => { const id = ++nextId; pending.set(id, { resolve, reject }); ws.send(JSON.stringify({ id, method, params })); });
  const once = (method) => new Promise((resolve) => waiters.push({ method, resolve }));
  await send("Page.enable"); await send("Runtime.enable"); await send("Log.enable"); await send("Network.enable");

  async function evaluate(expression) {
    const r = await send("Runtime.evaluate", { expression, awaitPromise: true, returnByValue: true });
    if (r.exceptionDetails) throw new Error(r.exceptionDetails.exception?.description ?? r.exceptionDetails.text);
    return r.result.value;
  }
  async function navigate(url) {
    const loaded = once("Page.loadEventFired");
    await send("Page.navigate", { url });
    await loaded;
  }
  function close() { ws.close(); proc.kill(); }
  return { send, once, evaluate, navigate, close };
}
