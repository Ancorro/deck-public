// Deck's link to an OpenClaw Gateway, for chat_bridge.py's OpenClaw adapter: one Gateway connection, with JSON lines
// both ways on stdin/stdout.
//   in:  {"id", "method", "params"}                       a Gateway request (chat.send, chat.history, chat.abort, ...)
//   out: {"t": "res", "id", "ok", "payload" | "error"}    its answer
//        {"t": "event", "event", "payload"}               every Gateway event (chat, agent, session.message, ...)
//        {"t": "hello", "server", "scopes", "http"}       connected (again, after a drop); http: the Gateway's web origin
//        {"t": "pairing", "requestId"}                    this device waits for approval on the Gateway host
//        {"t": "upgrade", "requestId"}                    it asked for the questions scope: approval on the Gateway host
//        {"t": "error", "message"}                        a connect or config problem worth showing
// Exit code 3: the questions scope was approved (chat_bridge.py starts it again, and it connects with it).
// The Gateway's address and shared token come from the openclaw CLI's own config (~/.openclaw/openclaw.json:
// gateway.remote.url/token when it is a client, else the local Gateway). This device's identity (an Ed25519 key, as
// OpenClaw's own clients use) and the device token the Gateway issues once it is approved live in
// ~/.config/deck/openclaw-device.json, so one approval per host lasts.
import crypto from "node:crypto";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import readline from "node:readline";
import { GatewayClient } from "@openclaw/gateway-client";

const out = (obj) => process.stdout.write(JSON.stringify(obj) + "\n");
const home = os.homedir();
const deckData = process.env.DECK_DATA || path.join(home, ".config", "deck");
const idFile = path.join(deckData, "openclaw-device.json");

function gatewayTarget() {
  const cfgPath = process.env.OPENCLAW_CONFIG_PATH || path.join(process.env.OPENCLAW_STATE_DIR || path.join(home, ".openclaw"), "openclaw.json");
  let gw = {};
  try {
    gw = JSON.parse(fs.readFileSync(cfgPath, "utf8")).gateway || {};
  } catch (e) {
    throw new Error(`can't read OpenClaw's config (${cfgPath}): ${e.message}`);
  }
  if (gw.mode === "remote" && gw.remote?.url) return { url: gw.remote.url, token: gw.remote.token, password: gw.remote.password };
  return { url: `ws://127.0.0.1:${gw.port || 18789}`, token: gw.auth?.token, password: gw.auth?.password };
}

// ---- this device: its key and the Gateway's token for it ----
const ED25519_SPKI_PREFIX = Buffer.from("302a300506032b6570032100", "hex");
const rawPublicKey = (pem) => crypto.createPublicKey(pem).export({ type: "spki", format: "der" }).subarray(ED25519_SPKI_PREFIX.length);

function loadDevice() {
  try {
    return JSON.parse(fs.readFileSync(idFile, "utf8"));
  } catch {
    const { publicKey, privateKey } = crypto.generateKeyPairSync("ed25519");
    const publicKeyPem = publicKey.export({ type: "spki", format: "pem" });
    const dev = {
      deviceId: crypto.createHash("sha256").update(rawPublicKey(publicKeyPem)).digest("hex"),
      publicKeyPem,
      privateKeyPem: privateKey.export({ type: "pkcs8", format: "pem" }),
      tokens: {},
    };
    saveDevice(dev);
    return dev;
  }
}

function saveDevice(dev) {
  fs.mkdirSync(deckData, { recursive: true });
  fs.writeFileSync(idFile + ".tmp", JSON.stringify(dev, null, 1), { mode: 0o600 });
  fs.renameSync(idFile + ".tmp", idFile);
}

const dev = loadDevice();
const tokenKey = (p) => `${p.deviceId}:${p.role}`;
const hostDeps = {
  signDevicePayload: (privateKeyPem, payload) => crypto.sign(null, Buffer.from(payload, "utf8"), crypto.createPrivateKey(privateKeyPem)).toString("base64url"),
  publicKeyRawBase64UrlFromPem: (pem) => rawPublicKey(pem).toString("base64url"),
  loadDeviceAuthToken: (p) => dev.tokens?.[tokenKey(p)] || null,
  storeDeviceAuthToken: (p) => {
    dev.tokens = { ...dev.tokens, [tokenKey(p)]: { token: p.token, scopes: p.scopes } };
    saveDevice(dev);
  },
  clearDeviceAuthToken: (p) => {
    if (dev.tokens?.[tokenKey(p)]) {
      delete dev.tokens[tokenKey(p)];
      saveDevice(dev);
    }
  },
  logError: (m) => process.stderr.write(m + "\n"),
};

let target;
try {
  target = gatewayTarget();
} catch (e) {
  out({ t: "error", message: e.message });
  process.exit(2);
}

// Scopes: reading and chatting, and answering the agent's questions (ask_user). A device approved before Deck asked for
// questions holds a token without that scope: it connects with what it has, asks the Gateway once for the rest
// (device.scopes.requestUpgrade, approved on the Gateway host like a new device) and, once approved, exits to be
// started again with it (a second GatewayClient in this process, after the first one's stop(), was seen connected with
// the scope but passing no events on). A rejected request isn't made again.
const BASE = ["operator.read", "operator.write"], QUESTIONS = "operator.questions";
const granted = () => dev.tokens?.[tokenKey({ deviceId: dev.deviceId, role: "operator" })]?.scopes || [];
let ready; // requests wait for the first hello (a later drop: the client reconnects and requests fail till then)
const connected = new Promise((resolve) => (ready = resolve));
let client, upgrading = false;
function connect() {
  client = new GatewayClient({
    url: target.url,
    ...(target.token ? { token: target.token } : {}),
    ...(target.password ? { password: target.password } : {}),
    clientName: "openclaw-tui",
    clientDisplayName: `Deck (${os.hostname()})`,
    mode: "ui",
    role: "operator",
    scopes: granted().includes(QUESTIONS) ? [...BASE, QUESTIONS] : BASE,
    caps: ["tool-events", "chat-only-assistant-text"],
    platform: process.platform,
    deviceIdentity: { deviceId: dev.deviceId, publicKeyPem: dev.publicKeyPem, privateKeyPem: dev.privateKeyPem },
    hostDeps,
    onHelloOk: (hello) => {
      const scopes = hello.auth?.scopes || [];
      out({ t: "hello", server: hello.server?.version, scopes, http: target.url.replace(/^ws/, "http") });
      ready();
      if (!scopes.includes(QUESTIONS) && !dev.questionsRefused) upgrade(scopes);
    },
    onEvent: (evt) => out({ t: "event", event: evt.event, payload: evt.payload }),
    onConnectError: (err) => {
      const d = err?.details || err?.error?.details || {};
      const requestId = d.requestId || String(err?.message || "").match(/requestId: ([0-9a-f-]{36})/)?.[1];
      if (/pairing/i.test(String(err?.message)) || d.code === "PAIRING_REQUIRED") out({ t: "pairing", requestId: requestId || "" });
      else out({ t: "error", message: String(err?.message || err) });
    },
    onReconnectPaused: (info) => out({ t: "error", message: `Gateway connection paused: ${info.reason || info.detailCode || info.code}` }),
  });
  client.start();
}

async function upgrade(have) {
  if (upgrading) return;
  upgrading = true;
  try {
    const { requestId } = await client.request("device.scopes.requestUpgrade", { scopes: [...new Set([...have, QUESTIONS])] });
    out({ t: "upgrade", requestId });
    const r = await client.request("device.scopes.waitUpgrade", { requestId }, { timeoutMs: null });
    if (r?.status === "rejected") {
      dev.questionsRefused = true;
      saveDevice(dev);
    }
    if (r?.status !== "approved" || !r.deviceToken) return;
    hostDeps.storeDeviceAuthToken({ deviceId: dev.deviceId, role: "operator", token: r.deviceToken, scopes: r.scopes });
    client.stop();
    process.exit(3);
  } catch (e) { // an older Gateway without scope upgrades, or the connection dropped (the next hello asks again)
    process.stderr.write(`scope upgrade: ${e?.message || e}\n`);
  } finally {
    upgrading = false;
  }
}
connect();

readline.createInterface({ input: process.stdin }).on("line", async (line) => {
  let req;
  try {
    req = JSON.parse(line);
  } catch {
    return;
  }
  await connected;
  try {
    out({ t: "res", id: req.id, ok: true, payload: await client.request(req.method, req.params || {}) });
  } catch (e) {
    out({ t: "res", id: req.id, ok: false, error: String(e?.message || e) });
  }
}).on("close", () => {
  client.stop();
  process.exit(0);
});
