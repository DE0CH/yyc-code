// Baileys auth state in Postgres (Baileys' own advice: "write an auth state for a proper SQL DB" instead of its
// file store). One row per creds/key, JSON with Baileys' BufferJSON so Buffers round-trip. The news-stream DB holds
// all state, so a new pod (or a new node) continues the same linked device without re-linking.
import { BufferJSON, initAuthCreds, proto } from 'baileys'

const TABLE = 'CREATE TABLE IF NOT EXISTS whatsapp_auth (k TEXT PRIMARY KEY, v TEXT NOT NULL)'

export async function clearAuth (pool) {
  await pool.query(TABLE)
  await pool.query('DELETE FROM whatsapp_auth')
}

export async function usePgAuthState (pool) {
  await pool.query(TABLE)
  const read = async (k) => {
    const r = await pool.query('SELECT v FROM whatsapp_auth WHERE k=$1', [k])
    return r.rows.length ? JSON.parse(r.rows[0].v, BufferJSON.reviver) : null
  }
  const creds = (await read('creds')) || initAuthCreds()
  return {
    state: {
      creds,
      keys: {
        get: async (type, ids) => {
          const out = {}
          if (!ids.length) return out
          const r = await pool.query('SELECT k, v FROM whatsapp_auth WHERE k = ANY($1)', [ids.map(id => `${type}:${id}`)])
          for (const row of r.rows) {
            let v = JSON.parse(row.v, BufferJSON.reviver)
            if (type === 'app-state-sync-key' && v) v = proto.Message.AppStateSyncKeyData.fromObject(v)
            out[row.k.slice(type.length + 1)] = v
          }
          return out
        },
        set: async (data) => {
          const up = []; const del = []
          for (const type in data) {
            for (const id in data[type]) {
              const v = data[type][id]
              if (v) up.push([`${type}:${id}`, JSON.stringify(v, BufferJSON.replacer)])
              else del.push(`${type}:${id}`)
            }
          }
          const c = await pool.connect()
          try {
            await c.query('BEGIN')
            if (up.length) {
              await c.query(`INSERT INTO whatsapp_auth (k, v) SELECT * FROM unnest($1::text[], $2::text[])
                             ON CONFLICT (k) DO UPDATE SET v = excluded.v`, [up.map(x => x[0]), up.map(x => x[1])])
            }
            if (del.length) await c.query('DELETE FROM whatsapp_auth WHERE k = ANY($1)', [del])
            await c.query('COMMIT')
          } catch (e) {
            await c.query('ROLLBACK'); throw e
          } finally { c.release() }
        }
      }
    },
    saveCreds: async () => {
      await pool.query(`INSERT INTO whatsapp_auth (k, v) VALUES ('creds', $1)
                        ON CONFLICT (k) DO UPDATE SET v = excluded.v`, [JSON.stringify(creds, BufferJSON.replacer)])
    }
  }
}
