/** Agent Server 地址（可通过 VITE_AGENT_SERVER_URL 覆盖）。 */

export const SERVER_URL: string =
  (import.meta.env.VITE_AGENT_SERVER_URL as string | undefined) ??
  'http://127.0.0.1:8000'

export const WS_URL: string = SERVER_URL.replace(/^http/, 'ws')
