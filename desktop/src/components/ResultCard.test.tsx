/** ResultCard：Artifact 交付卡片渲染测试（下载地址只用 opaque id）。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { Artifact } from '../api/artifacts'
import ResultCard from './ResultCard'

const base: Artifact = {
  id: 'a'.repeat(32),
  kind: 'file',
  title: 'Market Report',
  description: 'Final result',
  filename: 'report.md',
  mime_type: 'text/markdown',
  size_bytes: 2048,
  sha256: 'hash',
  run_id: 'run-1',
  conversation_id: 'conv-1',
  task_id: null,
  source_url: null,
  created_at: '2026-08-20T00:00:00+00:00',
}

describe('ResultCard', () => {
  it('file artifact：Download 链接用 id 构造，不含 storage_path', () => {
    const html = renderToStaticMarkup(<ResultCard artifact={base} />)
    expect(html).toContain('Market Report')
    expect(html).toContain('report.md')
    expect(html).toContain('text/markdown')
    expect(html).toContain('Download')
    expect(html).toContain(`/artifacts/${base.id}/content`)
    expect(html).not.toContain('storage_path')
    expect(html).not.toContain('Open link')
  })

  it('url artifact：Open link 使用受控点击，不把 URL 复制成原生锚点', () => {
    const artifact: Artifact = {
      ...base,
      kind: 'url',
      filename: null,
      mime_type: null,
      source_url: 'https://example.com/result',
      size_bytes: 0,
    }
    const html = renderToStaticMarkup(<ResultCard artifact={artifact} />)
    expect(html).toContain('Open link')
    expect(html).toContain('https://example.com/result')
    expect(html).not.toContain('target="_blank"')
    expect(html).not.toContain('Download')
  })
})
