import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { Artifact } from '../api/artifacts'
import { ArtifactsView } from '../pages/ArtifactsPage'
import { RunArtifactsSection } from '../pages/RunDetailPage'
import ArtifactList from './ArtifactList'

const base: Artifact = {
  id: 'a'.repeat(32),
  kind: 'file',
  title: 'Market Report',
  description: 'Final result',
  filename: 'report.md',
  mime_type: 'text/markdown',
  size_bytes: 2048,
  sha256: 'hash',
  run_id: 'run-123456',
  conversation_id: 'conv-123456',
  task_id: null,
  source_url: null,
  created_at: '2026-08-20T00:00:00+00:00',
}

describe('Artifact UI', () => {
  it('Artifacts empty state', () => {
    const html = renderToStaticMarkup(<ArtifactsView artifacts={[]} />)
    expect(html).toContain('暂无 Artifact')
  })

  it('file artifact 展示并用 id 构造下载地址', () => {
    const html = renderToStaticMarkup(<ArtifactList artifacts={[base]} />)
    expect(html).toContain('Market Report')
    expect(html).toContain('report.md')
    expect(html).toContain(`/artifacts/${base.id}/content`)
    expect(html).toContain('Download / Open')
  })

  it('URL artifact 展示 Open Link', () => {
    const artifact: Artifact = {
      ...base,
      id: 'b'.repeat(32),
      kind: 'url',
      filename: null,
      source_url: 'https://example.com/result',
      size_bytes: 0,
    }
    const html = renderToStaticMarkup(<ArtifactList artifacts={[artifact]} />)
    expect(html).toContain('https://example.com/result')
    expect(html).toContain('Open Link')
  })

  it('Run Detail 有 Artifact 时展示交付区', () => {
    const html = renderToStaticMarkup(<RunArtifactsSection artifacts={[base]} />)
    expect(html).toContain('Artifacts')
    expect(html).toContain('Market Report')
    expect(renderToStaticMarkup(<RunArtifactsSection artifacts={[]} />)).toBe('')
  })
})
