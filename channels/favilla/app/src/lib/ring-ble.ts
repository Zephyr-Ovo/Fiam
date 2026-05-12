// Colmi R02 ring BLE sync — reads current HR + today's steps and POSTs to /ring/sync.
// Protocol reference: btw/colmi_r02_client (Python)

import { BleClient, numbersToDataView } from '@capacitor-community/bluetooth-le'
import { appConfig } from '../config'

// ── UUIDs ────────────────────────────────────────────────────────────────────
const UART_SERVICE = '6E40FFF0-B5A3-F393-E0A9-E50E24DCCA9E'
const UART_RX = '6E400002-B5A3-F393-E0A9-E50E24DCCA9E' // write (send command to ring)
const UART_TX = '6E400003-B5A3-F393-E0A9-E50E24DCCA9E' // notify (receive data from ring)

// ── Command bytes ─────────────────────────────────────────────────────────────
const CMD_START_REAL_TIME = 105 // 0x69
const CMD_STOP_REAL_TIME = 106  // 0x6A
const CMD_GET_STEP_SOMEDAY = 67 // 0x43

// ── Packet helpers ────────────────────────────────────────────────────────────
function checksum(bytes: number[]): number {
  return bytes.reduce((s, b) => (s + b) & 255, 0)
}

function makePkt(command: number, subData: number[] = []): DataView {
  const bytes = new Array<number>(16).fill(0)
  bytes[0] = command
  subData.forEach((b, i) => { bytes[i + 1] = b })
  bytes[15] = checksum(bytes.slice(0, 15))
  return numbersToDataView(bytes)
}

function bcd(b: number): number {
  return ((b >> 4) & 15) * 10 + (b & 15)
}

// ── Notification dispatcher ───────────────────────────────────────────────────
// Module-level: one active handler per command byte at a time.
const _handlers = new Map<number, (bytes: Uint8Array) => void>()

function _dispatch(value: DataView): void {
  const bytes = new Uint8Array(value.buffer, value.byteOffset, value.byteLength)
  if (bytes.length !== 16) return
  const cmd = bytes[0] & 0x7f // clear error bit
  _handlers.get(cmd)?.(bytes)
}

// ── Real-time heart rate ───────────────────────────────────────────────────────
// Mirrors Python client._poll_real_time_reading(HEART_RATE).
// Collects up to 3 valid non-zero readings (or times out at 15 s) and returns the last.
async function readCurrentHr(deviceId: string): Promise<number | undefined> {
  return new Promise((resolve) => {
    const valid: number[] = []
    let tries = 0

    const done = (value: number | undefined) => {
      clearTimeout(timer)
      _handlers.delete(CMD_START_REAL_TIME)
      void BleClient.writeWithoutResponse(deviceId, UART_SERVICE, UART_RX,
        makePkt(CMD_STOP_REAL_TIME, [1, 0, 0])).catch(() => undefined)
      resolve(value)
    }

    const timer = setTimeout(() => done(valid.length ? valid[valid.length - 1] : undefined), 8_000)

    _handlers.set(CMD_START_REAL_TIME, (bytes) => {
      const errorCode = bytes[2]
      const value = bytes[3]
      if (errorCode === 0 && value > 0) {
        valid.push(value)
        if (valid.length >= 1) done(valid[valid.length - 1])
      } else {
        tries++
        if (tries > 8) done(valid.length ? valid[valid.length - 1] : undefined)
      }
    })

    void BleClient.writeWithoutResponse(deviceId, UART_SERVICE, UART_RX,
      makePkt(CMD_START_REAL_TIME, [1, 1])).catch(() => undefined) // HEART_RATE=1, START=1
  })
}

// ── Today's steps (multi-packet) ───────────────────────────────────────────────
// Mirrors Python SportDetailParser.
// First packet: byte[1]==0xF0 (header), byte[3]==1 means new calorie protocol.
// Data packets: byte[5] is current index, byte[6]-1 is last index.
interface StepsSummary { steps: number; calories: number; distance_m: number }

function sumSteps(pkts: Uint8Array[], newCalProt: boolean): StepsSummary {
  let steps = 0, calories = 0, distance_m = 0
  for (let i = 1; i < pkts.length; i++) { // skip header at index 0
    const p = pkts[i]
    const year = bcd(p[1]) + 2000
    if (year < 2020 || year > 2035) continue // sanity-check year
    let cal = p[7] | (p[8] << 8)
    if (newCalProt) cal *= 10
    steps += p[9] | (p[10] << 8)
    calories += cal
    distance_m += p[11] | (p[12] << 8)
  }
  return { steps, calories, distance_m }
}

async function readStepsToday(deviceId: string): Promise<StepsSummary | undefined> {
  return new Promise((resolve) => {
    const pkts: Uint8Array[] = []
    let newCalProt = false

    const done = (result: StepsSummary | undefined) => {
      clearTimeout(timer)
      _handlers.delete(CMD_GET_STEP_SOMEDAY)
      resolve(result)
    }

    const timer = setTimeout(
      () => done(pkts.length > 1 ? sumSteps(pkts, newCalProt) : undefined),
      8_000,
    )

    _handlers.set(CMD_GET_STEP_SOMEDAY, (bytes) => {
      if (bytes[1] === 255) { done(undefined); return } // NoData

      if (pkts.length === 0 && bytes[1] === 240) { // 0xF0 header
        newCalProt = bytes[3] === 1
        pkts.push(new Uint8Array(bytes))
        return
      }

      pkts.push(new Uint8Array(bytes))

      // Last packet: current_index (byte[5]) == total - 1 (byte[6] - 1)
      if (bytes[5] === bytes[6] - 1) done(sumSteps(pkts, newCalProt))
    })

    void BleClient.writeWithoutResponse(deviceId, UART_SERVICE, UART_RX,
      makePkt(CMD_GET_STEP_SOMEDAY, [0, 0x0f, 0x00, 0x5f, 0x01])).catch(() => undefined) // day_offset=0 (today)
  })
}

// ── Device ID cache ───────────────────────────────────────────────────────────
const RING_DEVICE_KEY = 'favilla:ring_device_id'

async function getDeviceId(): Promise<string> {
  const stored = localStorage.getItem(RING_DEVICE_KEY)
  if (stored) {
    try {
      await BleClient.connect(stored, () => { _handlers.clear() })
      return stored
    } catch {
      localStorage.removeItem(RING_DEVICE_KEY)
    }
  }
  const device = await BleClient.requestDevice({ optionalServices: [UART_SERVICE] })
  localStorage.setItem(RING_DEVICE_KEY, device.deviceId)
  await BleClient.connect(device.deviceId, () => { _handlers.clear() })
  return device.deviceId
}

// ── Public API ────────────────────────────────────────────────────────────────
export type RingSyncResult =
  | { ok: true; current_hr?: number; steps?: number; calories?: number; distance_m?: number }
  | { ok: false; error: string }

export async function syncRingToServer(): Promise<RingSyncResult> {
  const apiBase = ((appConfig.apiBase || (import.meta.env.VITE_API_BASE as string) || '').trim()).replace(/\/+$/, '')
  const token = (appConfig.ingestToken || (import.meta.env.VITE_INGEST_TOKEN as string) || '').trim()

  try {
    await BleClient.initialize()
    const deviceId = await getDeviceId()
    await BleClient.startNotifications(deviceId, UART_SERVICE, UART_TX, _dispatch)

    let current_hr: number | undefined
    let stepsData: StepsSummary | undefined
    try {
      current_hr = await readCurrentHr(deviceId)
      stepsData = await readStepsToday(deviceId)
    } finally {
      await BleClient.stopNotifications(deviceId, UART_SERVICE, UART_TX).catch(() => undefined)
      await BleClient.disconnect(deviceId).catch(() => undefined)
      _handlers.clear()
    }

    const today = new Date().toISOString().slice(0, 10)
    const body: Record<string, unknown> = { date: today }
    if (current_hr != null) body.current_hr = current_hr
    if (stepsData != null) {
      body.steps = stepsData.steps
      body.calories = stepsData.calories
      body.distance_m = stepsData.distance_m
    }

    const resp = await fetch(`${apiBase}/ring/sync`, {
      method: 'POST',
      headers: {
        'Content-Type': 'application/json',
        ...(token ? { 'X-Fiam-Token': token } : {}),
      },
      body: JSON.stringify(body),
    })

    if (!resp.ok) throw new Error(`Server returned ${resp.status}`)

    return { ok: true, current_hr, ...stepsData }
  } catch (err) {
    return { ok: false, error: err instanceof Error ? err.message : String(err) }
  }
}
