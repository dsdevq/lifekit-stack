/**
 * notify-relay — HTTP webhook that turns devclaw's notify_url POST into a
 * Telegram message via the gateway's openclaw CLI.
 *
 * Endpoints:
 *   GET  /health     — liveness probe, returns {ok:true}
 *   POST /devclaw    — body = devclaw task row JSON; formats + sends as
 *                      Telegram message to LIFEKIT_TELEGRAM_CHAT
 *
 * Send path: `docker exec <gateway> openclaw message send --channel telegram
 *   --target <chat_id> --message "..."`. Requires the docker socket mounted
 *   read-only and the gateway container to be the named one.
 */

import { createServer } from "node:http";
import { spawn } from "node:child_process";

const PORT = Number(process.env.NOTIFY_RELAY_PORT ?? 8090);
const CHAT = process.env.LIFEKIT_TELEGRAM_CHAT ?? "";
const GATEWAY = process.env.GATEWAY_CONTAINER ?? "compose-openclaw-gateway-1";
const MAX_MSG_CHARS = 3500; // Telegram is 4096; leave headroom

if (!CHAT) {
  process.stderr.write("LIFEKIT_TELEGRAM_CHAT env var is required\n");
  process.exit(1);
}

const STATUS_ICON = {
  done: "✅", // ✅
  failed: "❌", // ❌
};

function formatMessage(row) {
  const status = String(row?.status ?? "unknown");
  const icon = STATUS_ICON[status] ?? "ℹ️"; // ℹ️
  const kind = String(row?.kind ?? "task");
  const goal = String(row?.goal ?? "").slice(0, 240);
  const taskId = String(row?.task_id ?? "?");

  const lines = [`${icon} devclaw ${kind} — ${status}`];
  if (goal) lines.push(`> ${goal}`);
  lines.push(`task_id: ${taskId.slice(0, 8)}…`);

  if (status === "failed" && row?.error) {
    lines.push("");
    lines.push(`error: ${String(row.error).slice(0, 600)}`);
  } else if (status === "done" && row?.result_json) {
    try {
      const parsed =
        typeof row.result_json === "string"
          ? JSON.parse(row.result_json)
          : row.result_json;
      if (parsed?.message) {
        lines.push("");
        lines.push(String(parsed.message).slice(0, 400));
      }
    } catch {
      // result_json wasn't JSON; skip
    }
  }

  let msg = lines.join("\n");
  if (msg.length > MAX_MSG_CHARS) {
    msg = msg.slice(0, MAX_MSG_CHARS - 14) + "\n… [truncated]";
  }
  return msg;
}

function sendTelegram(text) {
  return new Promise((resolve, reject) => {
    const args = [
      "exec",
      GATEWAY,
      "openclaw",
      "message",
      "send",
      "--channel",
      "telegram",
      "--target",
      CHAT,
      "--message",
      text,
      "--json",
    ];
    const child = spawn("docker", args, {
      stdio: ["ignore", "pipe", "pipe"],
    });
    let stdout = "";
    let stderr = "";
    child.stdout.on("data", (c) => (stdout += c));
    child.stderr.on("data", (c) => (stderr += c));
    child.on("error", (err) =>
      reject(new Error(`spawn docker failed: ${err.message}`)),
    );
    child.on("close", (code) => {
      if (code === 0) resolve(stdout.trim());
      else
        reject(
          new Error(
            `docker exec exited ${code}: ${stderr.trim() || stdout.trim()}`,
          ),
        );
    });
  });
}

async function readBody(req) {
  let body = "";
  for await (const chunk of req) body += chunk;
  return body;
}

const server = createServer(async (req, res) => {
  const log = (msg) =>
    process.stderr.write(`[${new Date().toISOString()}] ${msg}\n`);

  if (req.url === "/health") {
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true, name: "notify-relay" }));
    return;
  }

  if (req.method !== "POST" || !req.url?.startsWith("/devclaw")) {
    res.writeHead(404, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "not found" }));
    return;
  }

  let body;
  try {
    body = await readBody(req);
  } catch (err) {
    log(`read-body error: ${err.message}`);
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "could not read body" }));
    return;
  }

  let payload;
  try {
    payload = JSON.parse(body);
  } catch {
    log(`invalid json (first 200 chars): ${body.slice(0, 200)}`);
    res.writeHead(400, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: "invalid json body" }));
    return;
  }

  const taskId = payload?.task_id ?? "?";
  const status = payload?.status ?? "?";
  log(`POST /devclaw task=${taskId} status=${status}`);

  const message = formatMessage(payload);
  try {
    const result = await sendTelegram(message);
    log(`delivered task=${taskId}: ${result}`);
    res.writeHead(200, { "content-type": "application/json" });
    res.end(JSON.stringify({ ok: true }));
  } catch (err) {
    log(`send failed task=${taskId}: ${err.message}`);
    res.writeHead(502, { "content-type": "application/json" });
    res.end(JSON.stringify({ error: err.message }));
  }
});

server.listen(PORT, "0.0.0.0", () => {
  process.stderr.write(
    `notify-relay listening on 0.0.0.0:${PORT}, gateway=${GATEWAY}, chat=${CHAT}\n`,
  );
});

const shutdown = (sig) => {
  process.stderr.write(`received ${sig}, shutting down\n`);
  server.close(() => process.exit(0));
};
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
