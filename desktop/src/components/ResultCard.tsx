/** 统一 Artifact 交付卡片：Chat 结果区与 ArtifactsPage 可复用。 */

import type { ReactElement } from 'react'
import { buildArtifactDownloadUrl } from '../api/artifacts'
import type { Artifact } from '../api/artifacts'
import { Icon } from './Icon'

function formatBytes(bytes: number): string {
  if (!bytes) return '-'
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

/** 下载/打开地址只由 opaque artifact id 构造，绝不使用 storage_path。 */
export interface ResultCardProps {
  artifact: Artifact
}

export default function ResultCard({ artifact }: ResultCardProps): ReactElement {
  const isFile = artifact.kind === 'file'
  const href = isFile
    ? buildArtifactDownloadUrl(artifact.id)
    : (artifact.source_url ?? '#')

  const openUrl = (): void => {
    if (!artifact.source_url) return
    if (window.vesta) {
      void window.vesta.openExternal(artifact.source_url)
      return
    }
    window.open(artifact.source_url, '_blank', 'noopener,noreferrer')
  }

  return (
    <div className="result-card" data-testid="result-card">
      <div className="result-card__heading">
        <span className="result-card__icon"><Icon name="file" size={18} /></span>
        <div>
          <div className="result-card__title">
            {artifact.title || artifact.filename || 'Artifact'}
          </div>
          <div className="result-card__filename mono">
            {artifact.filename || artifact.source_url || artifact.kind}
          </div>
        </div>
      </div>
      {artifact.description ? (
        <div className="result-card__desc">{artifact.description}</div>
      ) : null}
      <div className="result-card__meta">
        <span className="mono">
          {artifact.mime_type || artifact.kind}
        </span>
        <span>{formatBytes(artifact.size_bytes)}</span>
      </div>
      <div className="result-card__actions">
        {isFile ? (
          <>
            <a className="btn btn-ghost btn-sm" href={href} target="_blank" rel="noreferrer">
              <Icon name="external" size={13} /> Open
            </a>
            <a className="btn btn-ghost btn-sm" href={href} download={artifact.filename ?? undefined}>
              <Icon name="download" size={13} /> Download
            </a>
          </>
        ) : (
          <button type="button" className="btn btn-ghost btn-sm" onClick={openUrl}>
            <Icon name="external" size={13} /> Open link
          </button>
        )}
      </div>
    </div>
  )
}
