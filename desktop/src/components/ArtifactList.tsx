import {
  buildArtifactDownloadUrl,
  type Artifact,
} from '../api/artifacts'

function formatSize(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`
  if (bytes < 1024 * 1024) return `${(bytes / 1024).toFixed(1)} KB`
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`
}

function formatTime(iso: string): string {
  const date = new Date(iso)
  return Number.isNaN(date.getTime()) ? iso : date.toLocaleString()
}

function openExternal(url: string): void {
  if (window.oneagent) {
    void window.oneagent.openExternal(url)
    return
  }
  window.open(url, '_blank', 'noopener,noreferrer')
}

export default function ArtifactList({
  artifacts,
  compact = false,
}: {
  artifacts: Artifact[]
  compact?: boolean
}): React.JSX.Element {
  if (artifacts.length === 0) {
    return <div className="empty">暂无 Artifact。</div>
  }

  return (
    <div className="panel" style={{ overflowX: 'auto' }}>
      <table className="table">
        <thead>
          <tr>
            <th>Title</th>
            <th>Type</th>
            <th>Filename / URL</th>
            {!compact && <th>Size</th>}
            <th>Run</th>
            {!compact && <th>Conversation</th>}
            <th>Created</th>
            <th />
          </tr>
        </thead>
        <tbody>
          {artifacts.map((artifact) => (
            <tr key={artifact.id}>
              <td>
                <strong>{artifact.title || artifact.filename || 'Artifact'}</strong>
                {artifact.description ? (
                  <div className="text-muted" style={{ marginTop: 3 }}>
                    {artifact.description}
                  </div>
                ) : null}
              </td>
              <td><span className="badge badge-pending">{artifact.kind}</span></td>
              <td className="artifact-target">
                {artifact.kind === 'file' ? artifact.filename : artifact.source_url}
              </td>
              {!compact && <td>{formatSize(artifact.size_bytes)}</td>}
              <td>{artifact.run_id?.slice(0, 8) ?? '-'}</td>
              {!compact && <td>{artifact.conversation_id?.slice(0, 8) ?? '-'}</td>}
              <td>{formatTime(artifact.created_at)}</td>
              <td>
                {artifact.kind === 'file' ? (
                  <a
                    className="btn btn-sm artifact-action"
                    href={buildArtifactDownloadUrl(artifact.id)}
                    download={artifact.filename ?? undefined}
                  >
                    Download / Open
                  </a>
                ) : artifact.source_url ? (
                  <button
                    className="btn btn-sm"
                    onClick={() => openExternal(artifact.source_url ?? '')}
                  >
                    Open Link
                  </button>
                ) : null}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  )
}

export { formatSize }
