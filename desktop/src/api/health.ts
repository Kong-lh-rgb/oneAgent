/** Health API 客户端。 */

import { apiGet } from './http'
import type { Health } from './types'

export async function getHealth(): Promise<Health> {
  return apiGet<Health>('/health')
}
