/**
 * notify-relay — HTTP webhook that turns devclaw's notify_url POST into a
 * Telegram message via Telegram's Bot API.
 *
 * Endpoints:
 *   GET  /health   — liveness probe → {ok:true}
 *   POST /devclaw  — body = devclaw task row JSON; formats + sends as
 *                    Telegram message to $LIFEKIT_TELEGRAM_CHAT
 *
 * Required env:
 *   TELEGRAM_BOT_TOKEN     — bot token (from @BotFather)
 *   LIFEKIT_TELEGRAM_CHAT  — chat id to send to
 *
 * No docker socket, no exec, no extra deps. ~150 lines of Node.
 */

import { createServer } from "node:http";

const PORT = Number(process.env.NOTIFY_RELAY_PORT ?? 8090);
const TOKEN = process.env.TELEGRAM_BOT_TOKEN ?? "";
const CHAT = process.env.LIFEKIT_TELEGRAM_CHAT ?? "";
const MAX_MSG_CHARS = 3500; // Telegram is 4096; leave headroom

if (!TOKEN) {
  process.stderr.write("TELEGRAM_BOT_TOKEN env var is required\n");
  process.exit(1);
}
if (!CHAT) {
  process.stderr.write("LIFEKIT_TELEGRAM_CHAT env var is required\n");
  process.exit(1);
}

const STATUS_ICON = {
  done: "✅",
  failed: "❌",
};

function formatMessage(row) {
  const status = String(row?.status ?? "unknown");
  const icon = STATUS_ICON[status] ?? "ℹ️";
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

async function sendTelegram(text) {
  const url = `https://api.telegram.org/bot${TOKEN}/sendMessage`;
  const res = await fetch(url, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      chat_id: CHAT,
      text,
      disable_web_page_preview: true,
    }),
  });
  const body = await res.json().catch(() => ({}));
  if (!res.ok || body.ok !== true) {
    throw new Error(
      `Telegram API ${res.status}: ${body.description ?? "unknown error"}`,
    );
  }
  return body.result;
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

  // Generic passthrough — POST /text {text} sends the text verbatim to Telegram.
  // Used by goalclaw (and any non-devclaw producer): the sender owns formatting,
  // so messages aren't forced through devclaw's task-row template.
  if (req.method === "POST" && req.url?.startsWith("/text")) {
    let raw;
    try {
      raw = JSON.parse(await readBody(req));
    } catch {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "invalid json body" }));
      return;
    }
    const text = String(raw?.text ?? "").slice(0, MAX_MSG_CHARS);
    if (!text) {
      res.writeHead(400, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: "missing 'text'" }));
      return;
    }
    log(`POST /text (${text.length} chars)`);
    try {
      await sendTelegram(text);
      res.writeHead(200, { "content-type": "application/json" });
      res.end(JSON.stringify({ ok: true }));
    } catch (err) {
      log(`/text send failed: ${err.message}`);
      res.writeHead(502, { "content-type": "application/json" });
      res.end(JSON.stringify({ error: err.message }));
    }
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
    log(`delivered task=${taskId} message_id=${result?.message_id ?? "?"}`);
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
    `notify-relay listening on 0.0.0.0:${PORT}, chat=${CHAT}\n`,
  );
});

const shutdown = (sig) => {
  process.stderr.write(`received ${sig}, shutting down\n`);
  server.close(() => process.exit(0));
};
process.on("SIGTERM", () => shutdown("SIGTERM"));
process.on("SIGINT", () => shutdown("SIGINT"));
