/** Usage Inspector：展示 Main Agent 与 Post-Run 的完整 Provider 用量。 */

import type { ModelUsage, RunUsageSummary } from '../api/types'
import { formatTokens } from '../agent/turnPresentation'
import { EmptyState } from './ui'

function optionalTokens(value: number | null | undefined): string {
  return value === null || value === undefined ? 'Unavailable' : formatTokens(value)
}

function hasUsage(usage: ModelUsage): boolean {
  return usage.total_tokens > 0 || (usage.model_calls ?? 0) > 0
}

function PostRunRow({
  label,
  usage,
  status,
}: {
  label: string
  usage: ModelUsage
  status?: string
}): React.JSX.Element {
  return (
    <div className="usage-post-row">
      <span>{label}</span>
      <strong className="mono">{formatTokens(usage.total_tokens)}</strong>
      <small>{status ?? `${usage.model_calls ?? 0} calls`}</small>
    </div>
  )
}

export default function UsageInspector({
  summary,
}: {
  summary: RunUsageSummary | null | undefined
}): React.JSX.Element {
  if (!summary) {
    return <EmptyState title="暂无 Usage 数据" hint="Trace 加载后会显示完整 Provider 用量。" />
  }
  const main = summary.main_agent
  const cacheKnown = main.cached_input_tokens !== null
    && main.cached_input_tokens !== undefined

  return (
    <section className="usage-inspector">
      <div className="usage-heading">
        <div><h3>Main Agent</h3><span>{main.model_calls ?? 0} model calls</span></div>
        <strong className="mono">{formatTokens(main.input_tokens)} processed</strong>
      </div>
      <dl className="usage-main-grid">
        <div><dt>Cached</dt><dd>{optionalTokens(main.cached_input_tokens)}</dd></div>
        <div><dt>Uncached</dt><dd>{optionalTokens(main.uncached_input_tokens)}</dd></div>
        <div><dt>Output</dt><dd>{formatTokens(main.output_tokens)}</dd></div>
        <div><dt>Cache read</dt><dd>{optionalTokens(main.cache_read_input_tokens)}</dd></div>
        <div><dt>Cache write</dt><dd>{optionalTokens(main.cache_write_input_tokens)}</dd></div>
        <div><dt>Tool schemas</dt><dd>≈{formatTokens(summary.tool_schema_tokens_estimated)}</dd></div>
      </dl>
      {!cacheKnown ? (
        <p className="usage-note">当前 Provider 没有返回缓存细分；Unavailable 不等于 0。</p>
      ) : null}
      <div className="usage-budget">
        <div className="usage-heading">
          <div><h3>Run Budget</h3><span>{summary.run_budget_status}</span></div>
          <strong className="mono">{formatTokens(summary.main_agent_chargeable_tokens)} budgeted</strong>
        </div>
        <p className="usage-note">
          Token {summary.run_budget_warning_tokens === null ? '—' : formatTokens(summary.run_budget_warning_tokens)}
          {' → '}{summary.run_budget_finalization_tokens === null ? '—' : formatTokens(summary.run_budget_finalization_tokens)}
          {' → '}{summary.run_budget_hard_tokens === null ? '—' : formatTokens(summary.run_budget_hard_tokens)}
          {' · Calls '}{summary.run_budget_warning_model_calls ?? '—'}
          {' → '}{summary.run_budget_finalization_model_calls ?? '—'}
          {' → '}{summary.run_budget_hard_model_calls ?? '—'}
          {summary.run_budget_reason ? ` · Triggered by ${summary.run_budget_reason}` : ''}
        </p>
      </div>
      <div className="usage-post-run">
        <PostRunRow
          label="Memory Reflection"
          usage={summary.memory_reflection}
          status={
            summary.memory_reflection_status === 'skipped'
              ? `Skipped · ${summary.memory_reflection_skip_reason ?? 'policy'}`
              : undefined
          }
        />
        <PostRunRow label="Memory Maintenance" usage={summary.memory_maintenance} />
      </div>
      <div className="usage-total">
        <div><span>Provider Total</span><small>{summary.provider_total.model_calls ?? 0} calls</small></div>
        <strong className="mono">{formatTokens(summary.provider_total.total_tokens)}</strong>
      </div>
      {!hasUsage(summary.memory_reflection) && !hasUsage(summary.memory_maintenance) ? (
        <p className="usage-note">本 Run 没有产生 Post-Run 模型开销。</p>
      ) : null}
    </section>
  )
}
