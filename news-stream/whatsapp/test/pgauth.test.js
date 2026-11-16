// The linked device's keys live in Postgres (table whatsapp_auth), so a new pod continues the same linked device
// without re-linking. Needs TEST_DATABASE_URL (a throwaway database; the table is dropped first).
//     TEST_DATABASE_URL=postgresql:///yyc_test node --test test/
import { test } from 'node:test'
import assert from 'node:assert/strict'
import pg from 'pg'
import { usePgAuthState, clearAuth } from '../pgauth.js'

const url = process.env.TEST_DATABASE_URL
// postgresql:///db = the local Unix socket (peer auth), as the Python tests use it
const pool = new pg.Pool(url.startsWith('postgresql:///') ? { host: '/var/run/postgresql', database: url.slice(14) } : { connectionString: url })

test('creds and keys survive a restart, deletes and clear work', async () => {
  await pool.query('DROP TABLE IF EXISTS whatsapp_auth')
  const a = await usePgAuthState(pool)
  assert.equal(a.state.creds.registered, false)            // fresh creds the first time
  a.state.creds.me = { id: '447448090466:3@s.whatsapp.net' }
  await a.saveCreds()
  await a.state.keys.set({ 'pre-key': { 1: { public: Buffer.from([1, 2, 3]), private: Buffer.from([4]) }, 2: null },
                           session: { 'x:1': Buffer.from('abc') } })

  const b = await usePgAuthState(pool)                    // "the next pod"
  assert.equal(b.state.creds.me.id, '447448090466:3@s.whatsapp.net')
  assert.deepEqual(b.state.creds.noiseKey.public, a.state.creds.noiseKey.public) // Buffers round-trip
  const got = await b.state.keys.get('pre-key', ['1', '2'])
  assert.deepEqual(got['1'].public, Buffer.from([1, 2, 3]))
  assert.equal(got['2'], undefined)
  assert.deepEqual((await b.state.keys.get('session', ['x:1']))['x:1'], Buffer.from('abc'))
  await b.state.keys.set({ 'pre-key': { 1: null } })
  assert.equal((await b.state.keys.get('pre-key', ['1']))['1'], undefined)

  await clearAuth(pool)
  const c = await usePgAuthState(pool)
  assert.equal(c.state.creds.me, undefined)
  await pool.end()
})
