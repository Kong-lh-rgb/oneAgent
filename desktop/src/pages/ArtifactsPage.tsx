import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { listArtifacts } from '../api/artifacts'
import ArtifactList from '../components/ArtifactList'
import { rpcClient } from '../rpc'

/** 最近交付物页面；Artifact 内容不进 Renderer，只展示公开 metadata。 */
export default function ArtifactsPage(): React.JSX.Element {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['artifacts'],
    queryFn: () => listArtifacts({ limit: 100 }),
    refetchInterval: 5000,
  })

  useEffect(
    () => rpcClient.on('artifact.created', () => {
      void queryClient.invalidateQueries({ queryKey: ['artifacts'] })
    }),
    [queryClient],
  )

  return (
    <ArtifactsView
      artifacts={query.data ?? []}
      pending={query.isPending}
      error={query.isError ? String(query.error) : null}
    />
  )
}

export function ArtifactsView({
  artifacts,
  pending = false,
  error = null,
}: {
  artifacts: Awaited<ReturnType<typeof listArtifacts>>
  pending?: boolean
  error?: string | null
}): React.JSX.Element {
  return (
    <div style={{ padding: 16, overflowY: 'auto', flex: 1 }}>
      <h2 style={{ margin: '0 0 12px', fontSize: 16 }}>Artifacts</h2>
      {error ? (
        <div className="error-text">{error}</div>
      ) : pending ? (
        <div className="empty"><span className="spinner" /> 正在加载…</div>
      ) : (
        <ArtifactList artifacts={artifacts} />
      )}
    </div>
  )
}
