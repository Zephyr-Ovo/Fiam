// Self-test favilla web preview via Chrome DevTools Protocol over WebSocket.
// Drives headless Edge to: load home, screenshot; open settings, screenshot;
// open chat, click cut, screenshot; type into composer to raise virtual
// keyboard, screenshot to verify confirm modal stays centered.
//
// Usage: node scripts/self_test_favilla.mjs http://127.0.0.1:5173
import { spawn } from "node:child_process"
import fs from "node:fs"
import path from "node:path"
import os from "node:os"
import http from "node:http"
// Node 22+ has a global WebSocket — no `ws` package needed.

const URL_ROOT = process.argv[2] || "http://127.0.0.1:5173"
const OUT = path.resolve("logs")
fs.mkdirSync(OUT, { recursive: true })

const EDGE = "C:\\Program Files (x86)\\Microsoft\\Edge\\Application\\msedge.exe"
const PROFILE = path.join(os.tmpdir(), "fav-edge-cdp")
fs.rmSync(PROFILE, { recursive: true, force: true })
const PORT = 9223

const edge = spawn(
  EDGE,
  [
    "--headless=new",
    "--disable-gpu",
    `--remote-debugging-port=${PORT}`,
    `--user-data-dir=${PROFILE}`,
    "--window-size=412,915",
    "--hide-scrollbars",
    "about:blank",
  ],
  { stdio: "ignore", detached: false },
)
process.on("exit", () => { try { edge.kill("SIGKILL") } catch {} })

function sleep(ms) { return new Promise(r => setTimeout(r, ms)) }

async function getJson(url) {
  return new Promise((res, rej) => {
    http.get(url, r => {
      let d = ""
      r.on("data", c => (d += c))
      r.on("end", () => res(JSON.parse(d)))
    }).on("error", rej)
  })
}

let _id = 0
async function send(ws, method, params = {}) {
  return new Promise(res => {
    const id = ++_id
    function onMsg(ev) {
      const msg = JSON.parse(ev.data)
      if (msg.id === id) { ws.removeEventListener("message", onMsg); res(msg.result) }
    }
    ws.addEventListener("message", onMsg)
    ws.send(JSON.stringify({ id, method, params }))
  })
}

async function shot(ws, name) {
  const r = await send(ws, "Page.captureScreenshot", { format: "png" })
  const file = path.join(OUT, `selftest-${name}.png`)
  fs.writeFileSync(file, Buffer.from(r.data, "base64"))
  console.log(`[shot] ${file}`)
}

async function tap(ws, x, y) {
  await send(ws, "Input.dispatchMouseEvent", {
    type: "mousePressed", x, y, button: "left", clickCount: 1,
  })
  await send(ws, "Input.dispatchMouseEvent", {
    type: "mouseReleased", x, y, button: "left", clickCount: 1,
  })
}

async function evalJs(ws, expr) {
  const r = await send(ws, "Runtime.evaluate", { expression: expr, returnByValue: true })
  return r.result?.value
}

;(async () => {
  // wait for cdp
  let tabs
  for (let i = 0; i < 30; i++) {
    try { tabs = await getJson(`http://127.0.0.1:${PORT}/json/list`); if (tabs.length) break } catch {}
    await sleep(200)
  }
  const tab = tabs.find(t => t.type === "page") || tabs[0]
  const ws = new WebSocket(tab.webSocketDebuggerUrl)
  await new Promise(r => ws.addEventListener("open", r, { once: true }))
  await send(ws, "Page.enable")
  await send(ws, "Runtime.enable")
  await send(ws, "Network.enable")
  await send(ws, "Emulation.setDeviceMetricsOverride", {
    width: 412, height: 915, deviceScaleFactor: 2, mobile: true,
  })

  // 1) Home
  await send(ws, "Page.navigate", { url: URL_ROOT })
  await sleep(2500)
  await shot(ws, "01-home")

  // 2) Open settings — settings sticker is top-right small; check JS to find it
  const settingsBox = await evalJs(ws, `(() => {
    const b = document.querySelector('[aria-label="Open settings"]') || document.querySelector('img[src*="setting"]')?.closest('button')
    if (!b) return null
    const r = b.getBoundingClientRect()
    return { x: r.x + r.width/2, y: r.y + r.height/2 }
  })()`)
  console.log("settings:", settingsBox)
  if (settingsBox) {
    await tap(ws, settingsBox.x, settingsBox.y)
    await sleep(700)
    await shot(ws, "02-settings-open")
    // close — tap top-left dim area
    await tap(ws, 30, 30)
    await sleep(500)
  }

  // 3) Go to chat
  const chatBox = await evalJs(ws, `(() => {
    const btns = [...document.querySelectorAll('button')]
    const b = btns.find(b => /open chat/i.test(b.getAttribute('aria-label') || ''))
    if (!b) return null
    const r = b.getBoundingClientRect()
    return { x: r.x + r.width/2, y: r.y + r.height/2 }
  })()`)
  console.log("chat:", chatBox)
  if (chatBox) {
    await tap(ws, chatBox.x, chatBox.y)
    await sleep(800)
    await shot(ws, "03-chat")

    // 4) Click cut (scissor) — top-right of chat header
    const cutBox = await evalJs(ws, `(() => {
      const b = document.querySelector('[aria-label="Cut"]')
      if (!b) return null
      const r = b.getBoundingClientRect()
      return { x: r.x + r.width/2, y: r.y + r.height/2 }
    })()`)
    console.log("cut:", cutBox)
    if (cutBox) {
      await tap(ws, cutBox.x, cutBox.y)
      await sleep(500)
      await shot(ws, "04-cut-confirm")
      // dismiss
      const cancel = await evalJs(ws, `(() => {
        const btns = [...document.querySelectorAll('button')]
        const b = btns.find(b => /cancel/i.test(b.textContent || ''))
        if (!b) return null
        const r = b.getBoundingClientRect()
        return { x: r.x + r.width/2, y: r.y + r.height/2 }
      })()`)
      if (cancel) { await tap(ws, cancel.x, cancel.y); await sleep(300) }
    }

    // 5) Long-press hourglass to trigger process confirm
    const hgBox = await evalJs(ws, `(() => {
      const b = document.querySelector('[data-testid="hourglass"]') || document.querySelector('[aria-label*="recall" i]')
      if (!b) return null
      const r = b.getBoundingClientRect()
      return { x: r.x + r.width/2, y: r.y + r.height/2 }
    })()`)
    console.log("hg:", hgBox)
    if (hgBox) {
      await send(ws, "Input.dispatchMouseEvent", { type: "mousePressed", x: hgBox.x, y: hgBox.y, button: "left", clickCount: 1 })
      await sleep(1500)
      await send(ws, "Input.dispatchMouseEvent", { type: "mouseReleased", x: hgBox.x, y: hgBox.y, button: "left", clickCount: 1 })
      await sleep(500)
      await shot(ws, "05-process-confirm")
    }
  }

  ws.close()
  edge.kill()
  process.exit(0)
})().catch(e => { console.error(e); edge.kill(); process.exit(1) })
