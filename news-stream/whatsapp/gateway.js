// YYC's WhatsApp gateway: a linked device (Baileys) of YYC's own WhatsApp number, whose primary phone is the Aliyun
// cloud phone acp-hln9ilimxqzruvccu. Started and stopped by whatsapp_alerts.py (which holds the singleton lock), and
// spoken to on 127.0.0.1 only:
//   GET  /status                    {state, me, name, since, at, last_close}
//   POST /send   {jid, text}        → {id}; 503 while not connected
//   POST /pair   {phone}            fresh link by phone number → {code} (type it on the phone: Linked devices →
//                                   Link a device → Link with phone number instead)
//   POST /group  {subject, description}  → {jid, invite}: a group only admins post in and only admins edit
//   GET  /invite?jid=…              → {code}
//   POST /name   {name}             the account's profile name
// Keys live in Postgres (pgauth.js, table whatsapp_auth); the state goes to kv whatsapp_status for the monitor.
// States: unlinked (no device yet) · connecting · linked · logged_out (removed on the phone: 401) · banned (403) ·
// replaced (another connection took over: 440).
import http from 'node:http'
import pg from 'pg'
import pino from 'pino'
import makeWASocket, { Browsers, DisconnectReason, makeCacheableSignalKeyStore } from 'baileys'
import { usePgAuthState, clearAuth } from './pgauth.js'

const PORT = Number(process.env.WHATSAPP_GATEWAY_PORT || 8790)
const logger = pino({ level: process.env.WHATSAPP_LOG || 'warn' })
// YYC's Postgres (CloudNativePG) presents a certificate from its own CA: node-postgres verifies it under
// sslmode=require, libpq (the Python workloads, the same Secret db-app) does not; uselibpqcompat gives libpq's meaning
const dbUrl = new URL(process.env.DATABASE_URL)
if (!dbUrl.searchParams.has('uselibpqcompat')) dbUrl.searchParams.set('uselibpqcompat', 'true')
const pool = new pg.Pool({ connectionString: dbUrl.toString(), max: 4 })

let sock = null
let status = { state: 'connecting', since: now() }
let stopping = false
let retry = 0
const sent = new Map()          // id → message, for WhatsApp's retry receipts (a recipient that couldn't decrypt)
const groups = new Map()        // jid → metadata, so sends to a group don't fetch it every time

function now () { return new Date().toISOString() }

async function setStatus (patch) {
  const changed = patch.state && patch.state !== status.state
  status = { ...status, ...patch, at: now(), ...(changed ? { since: now() } : {}) }
  try {
    await pool.query(`INSERT INTO kv (k, v) VALUES ('whatsapp_status', $1)
                      ON CONFLICT (k) DO UPDATE SET v = excluded.v`, [JSON.stringify(status)])
  } catch (e) { logger.error({ e }, 'status write failed') }
}

async function connect ({ pairPhone } = {}) {
  const auth = await usePgAuthState(pool)
  if (!auth.state.creds.registered && !pairPhone) {
    await setStatus({ state: 'unlinked', me: null })
    return null
  }
  const s = makeWASocket({
    auth: { creds: auth.state.creds, keys: makeCacheableSignalKeyStore(auth.state.keys, logger) },
    logger,
    // a pairing code links only a real browser name (Baileys README: custom names are for QR links)
    browser: Browsers.ubuntu('Chrome'),
    markOnlineOnConnect: false,           // the phone keeps getting its own notifications
    syncFullHistory: false,   // (history sync itself stays on: Baileys needs it for the LID mappings)
    getMessage: async (key) => sent.get(key.id),
    cachedGroupMetadata: async (jid) => groups.get(jid)
  })
  sock = s
  s.ev.on('creds.update', auth.saveCreds)
  s.ev.on('groups.update', (ups) => { for (const u of ups) groups.delete(u.id) })
  s.ev.on('group-participants.update', (u) => groups.delete(u.id))
  s.ev.on('connection.update', async (u) => {
    if (s !== sock) return
    if (u.connection === 'open') {
      retry = 0
      await setStatus({ state: 'linked', me: s.user?.id?.split(':')[0].split('@')[0], name: s.user?.name || null })
    } else if (u.connection === 'close') {
      const code = u.lastDisconnect?.error?.output?.statusCode
      const reason = String(u.lastDisconnect?.error?.message || '').slice(0, 200)
      const last_close = { code, reason, at: now() }
      if (stopping) return
      if (code === DisconnectReason.loggedOut) return setStatus({ state: 'logged_out', last_close })
      if (code === DisconnectReason.forbidden) return setStatus({ state: 'banned', last_close })
      if (code === DisconnectReason.connectionReplaced) return setStatus({ state: 'replaced', last_close })
      await setStatus({ state: 'connecting', last_close })
      // 515 restartRequired comes right after a new link: reconnect at once; otherwise back off up to a minute
      const wait = code === DisconnectReason.restartRequired ? 0 : Math.min(60, 2 ** retry++) * 1000
      setTimeout(() => { if (!stopping && s === sock) connect().catch(e => logger.error({ e }, 'reconnect failed')) }, wait)
    }
  })
  await setStatus({ state: 'connecting' })
  return s
}


const routes = {
  'GET /status': async () => status,
  'POST /send': async ({ jid, text }) => {
    if (status.state !== 'linked' || !sock) throw Object.assign(new Error(`not connected (${status.state})`), { code: 503 })
    const m = await sock.sendMessage(jid, { text })
    sent.set(m.key.id, m.message)
    if (sent.size > 1000) sent.delete(sent.keys().next().value)
    return { id: m.key.id }
  },
  'POST /pair': async ({ phone }) => {
    const digits = String(phone || '').replace(/\D/g, '')
    if (!digits) throw Object.assign(new Error('phone (digits with country code) is required'), { code: 400 })
    if (status.state === 'linked') throw Object.assign(new Error('already linked'), { code: 409 })
    const old = sock; sock = null
    try { old?.end(undefined) } catch {}
    await clearAuth(pool)
    const s = await connect({ pairPhone: digits })
    // ask for the code once WhatsApp's server has answered (the first QR of the link handshake); asked earlier,
    // the request can leave before the socket is open and the phone then says the code is wrong (2026-10-16)
    const ready = await new Promise((resolve) => {
      const h = (u) => { if (u.qr) { s.ev.off('connection.update', h); resolve(true) } }
      s.ev.on('connection.update', h)
      setTimeout(() => resolve(false), 30000)
    })
    if (!ready) throw Object.assign(new Error('WhatsApp did not answer within 30 s'), { code: 503 })
    const code = await s.requestPairingCode(digits)
    return { code: code.match(/.{1,4}/g).join('-'), expires: 'about 3 minutes' }
  },
  'POST /group': async ({ subject, description }) => {
    if (status.state !== 'linked') throw Object.assign(new Error(`not connected (${status.state})`), { code: 503 })
    const g = await sock.groupCreate(subject, [])
    if (description) await sock.groupUpdateDescription(g.id, description)
    await sock.groupSettingUpdate(g.id, 'announcement')   // only admins post
    await sock.groupSettingUpdate(g.id, 'locked')         // only admins edit the group's info
    groups.set(g.id, await sock.groupMetadata(g.id))
    return { jid: g.id, invite: await sock.groupInviteCode(g.id) }
  },
  'GET /invite': async (_, q) => ({ code: await sock.groupInviteCode(q.get('jid')) }),
  'POST /name': async ({ name }) => { await sock.updateProfileName(name); return { ok: true } },
  'GET /groups': async () => Object.values(await sock.groupFetchAllParticipating())
    .map(g => ({ jid: g.id, subject: g.subject, size: g.participants?.length, announce: g.announce }))
}

const server = http.createServer(async (req, res) => {
  const u = new URL(req.url, 'http://x')
  const fn = routes[`${req.method} ${u.pathname}`]
  let body = ''
  for await (const c of req) body += c
  try {
    if (!fn) throw Object.assign(new Error('not found'), { code: 404 })
    const out = await fn(body ? JSON.parse(body) : {}, u.searchParams)
    res.writeHead(200, { 'Content-Type': 'application/json' }); res.end(JSON.stringify(out))
  } catch (e) {
    const code = Number.isInteger(e.code) && e.code >= 400 && e.code < 600 ? e.code : 500
    res.writeHead(code, { 'Content-Type': 'application/json' }); res.end(JSON.stringify({ error: String(e.message || e) }))
  }
})

async function stop () {
  if (stopping) return
  stopping = true
  try { sock?.end(undefined) } catch {}
  server.close()
  setTimeout(() => process.exit(0), 3000)
  await pool.end().catch(() => {})
  process.exit(0)
}
process.on('SIGTERM', stop)
process.on('SIGINT', stop)

await pool.query('CREATE TABLE IF NOT EXISTS kv (k TEXT PRIMARY KEY, v JSONB)')
server.listen(PORT, '127.0.0.1')
await connect()
