/** 长期记忆卡片展示测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { LongTermMemory } from '../api/types'
import { CoreMemoryView, MemoryCard, parseCoreMemory } from './MemoryPage'

const memory: LongTermMemory = {
  id: 'M001',
  title: '中文偏好',
  summary: '用户希望界面使用中文',
  content: '设计与解释均使用中文。',
  created_at: '2026-08-22T00:00:00Z',
  updated_at: '2026-08-22T00:00:00Z',
  last_accessed_at: '2026-08-22T00:00:00Z',
  access_count: 3,
  revision: 2,
  status: 'active',
  last_update_reason: null,
  archive_reason: null,
}

describe('MemoryCard', () => {
  it('展示记忆摘要、版本和完整内容', () => {
    const html = renderToStaticMarkup(<MemoryCard memory={memory} />)
    expect(html).toContain('M001')
    expect(html).toContain('中文偏好')
    expect(html).toContain('r2')
    expect(html).toContain('查看完整内容')
    expect(html).toContain('设计与解释均使用中文')
  })

  it('核心记忆隐藏Markdown结构与内部key', () => {
    const core = [
      '# Core Memory',
      '',
      '## Managed Core Entries',
      '',
      '### user.preference.language',
      '',
      '用户希望始终使用中文。',
      '',
      '### user.constraint.comments',
      '',
      '代码注释使用中文。',
    ].join('\n')
    expect(parseCoreMemory(core)).toEqual([
      { label: '偏好', content: '用户希望始终使用中文。' },
      { label: '长期约束', content: '代码注释使用中文。' },
    ])
    const html = renderToStaticMarkup(<CoreMemoryView content={core} />)
    expect(html).toContain('始终随身携带')
    expect(html).toContain('用户希望始终使用中文')
    expect(html).not.toContain('Managed Core Entries')
    expect(html).not.toContain('user.preference.language')
  })
})
