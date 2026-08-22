import { useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect } from 'react'

import { listArtifacts } from '../api/artifacts'
import ArtifactList from '../components/ArtifactList'
import { EmptyState, ErrorState, LoadingState } from '../components/PageStates'
import { PageShell } from '../components/PageShell'
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
      onRetry={() => void query.refetch()}
    />
  )
}

export function ArtifactsView({
  artifacts,
  pending = false,
  error = null,
  onRetry,
}: {
  artifacts: Awaited<ReturnType<typeof listArtifacts>>
  pending?: boolean
  error?: string | null
  onRetry?: () => void
}): React.JSX.Element {
  return (
    <PageShell
      title="交付物"
      subtitle="查看 Vesta 在各项工作中交付的文件与链接。"
      maxWidth={1360}
    >
      <div className="artifacts-page">
        {error ? (
          <ErrorState message={error} onRetry={onRetry} />
        ) : pending ? (
          <LoadingState label="正在加载交付物…" />
        ) : artifacts.length === 0 ? (
          <EmptyState
            title="暂无交付结果"
            hint="Vesta 创建的文件和链接会显示在这里。"
            icon="artifacts"
          />
        ) : (
          <ArtifactList artifacts={artifacts} />
        )}
      </div>
    </PageShell>
  )
}
