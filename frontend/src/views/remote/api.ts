import { request } from '@/api/client'

import type {
  DispatchResult,
  LocatorConditions,
  MapData,
  ParcelPage,
  QueuePage,
  Summary,
} from './types'

const BASE = '/api/remote-queue'

function queryOf(conditions: LocatorConditions, extra?: Record<string, string | number | null>) {
  const params = new URLSearchParams()
  for (const [key, value] of Object.entries(conditions)) {
    if (value !== '' && value != null) params.set(key, String(value))
  }
  for (const [key, value] of Object.entries(extra ?? {})) {
    if (value !== '' && value != null) params.set(key, String(value))
  }
  const text = params.toString()
  return text ? `?${text}` : ''
}

async function readJson<T>(response: Response): Promise<T> {
  const payload = await response.json().catch(() => null)
  if (!response.ok) {
    throw new Error(payload?.detail ? String(payload.detail) : `接口返回 ${response.status}`)
  }
  return payload as T
}

export const queueApi = {
  async listQueue(conditions: LocatorConditions, cursor: string | null, size = 10): Promise<QueuePage> {
    const response = await request(
      `${BASE}/queue${queryOf(conditions, { cursor, size })}`,
    )
    return readJson(response)
  },

  async timeline(conditions: LocatorConditions): Promise<{ months: import('./types').TimelineMonth[] }> {
    const response = await request(`${BASE}/timeline${queryOf(conditions)}`)
    return readJson(response)
  },

  async map(conditions: LocatorConditions): Promise<MapData> {
    const response = await request(`${BASE}/map${queryOf(conditions)}`)
    return readJson(response)
  },

  async saveLocator(conditions: LocatorConditions): Promise<{ locator_id: string }> {
    const response = await request(`${BASE}/locator`, {
      method: 'POST',
      body: JSON.stringify(conditions),
    })
    return readJson(response)
  },

  async listParcels(options: {
    status?: string
    locatorId?: string | null
    cursorId?: number | null
    size?: number
  }): Promise<ParcelPage> {
    const response = await request(
      `${BASE}/parcels${queryOf(
        {},
        {
          status: options.status ?? null,
          locator_id: options.locatorId ?? null,
          cursor_id: options.cursorId ?? null,
          size: options.size ?? 50,
        },
      )}`,
    )
    return readJson(response)
  },

  async dispatch(body: {
    idempotency_key: string
    parcel_ids: number[]
    assignee: string
    conclusion?: string
  }): Promise<DispatchResult> {
    const response = await request(`${BASE}/dispatch`, {
      method: 'POST',
      body: JSON.stringify(body),
    })
    return readJson(response)
  },

  async review(imageId: number, result: '审核通过' | '审核驳回', note: string) {
    const response = await request(`${BASE}/images/${imageId}/review`, {
      method: 'POST',
      body: JSON.stringify({ result, note }),
    })
    return readJson<{ ok: boolean; message: string }>(response)
  },

  async deleteImage(imageId: number) {
    const response = await request(`${BASE}/images/${imageId}`, { method: 'DELETE' })
    return readJson<{ ok: boolean; message: string }>(response)
  },

  async summary(locatorId?: string | null): Promise<Summary> {
    const response = await request(
      `${BASE}/summary${queryOf({}, { locator_id: locatorId ?? null })}`,
    )
    return readJson(response)
  },
}
