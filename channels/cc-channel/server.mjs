#!/usr/bin/env node
import { readFileSync } from "node:fs";
import { setTimeout as delay } from "node:timers/promises";
import { Server } from "@modelcontextprotocol/sdk/server/index.js";
import { StdioServerTransport } from "@modelcontextprotocol/sdk/server/stdio.js";

const initialPath = process.env.FIAM_CC_CHANNEL_INITIAL_FILE || "";
const serverName = process.env.FIAM_CC_CHANNEL_NAME || "fiam-channel";
const keepaliveMs = Math.max(1000, Number(process.env.FIAM_CC_CHANNEL_KEEPALIVE_MS || 600000));

function log(message) {
  process.stderr.write(`[fiam-cc-channel] ${new Date().toISOString()} ${message}\n`);
}

function loadInitialEvent() {
  if (!initialPath) return null;
  const raw = readFileSync(initialPath, "utf8");
  const data = JSON.parse(raw);
  return {
    content: String(data.content || ""),
    meta: sanitizeMeta(data.meta || {}),
  };
}

function sanitizeMeta(meta) {
  const out = {};
  for (const [key, value] of Object.entries(meta || {})) {
    if (/^[A-Za-z_][A-Za-z0-9_]*$/.test(key)) {
      out[key] = String(value);
    }
  }
  return out;
}

const mcp = new Server(
  { name: serverName, version: "0.1.0" },
  {
    capabilities: {
      experimental: { "claude/channel": {} },
    },
    instructions:
      "Fiam messages arrive through this channel as direct user turns. Treat the channel body as the user's message. Do not mention channel tags, wrappers, source attributes, or transport details in replies. This channel is one-way; do not call reply tools.",
  },
);

const transport = new StdioServerTransport();
const originalOnMessage = transport.onmessage?.bind(transport);
let sent = false;

transport.onmessage = async (message) => {
  if (originalOnMessage) originalOnMessage(message);
  if (sent || message?.method !== "notifications/initialized") return;
  sent = true;
  try {
    const event = loadInitialEvent();
    if (!event || !event.content.trim()) {
      log("no initial event configured");
      return;
    }
    await mcp.notification({
      method: "notifications/claude/channel",
      params: event,
    });
    log(`sent initial event request_id=${event.meta.request_id || ""}`);
  } catch (error) {
    log(`failed to send initial event: ${error?.stack || error}`);
  }
};

await mcp.connect(transport);
log("connected");

await delay(keepaliveMs);
