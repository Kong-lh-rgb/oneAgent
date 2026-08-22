/** Usage Inspector：缓存未知语义与 Post-Run 总账。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import type { RunUsageSummary } from '../api/types'
import UsageInspector from './UsageInspector'

describe('UsageInspector', () => {
  it('展示 Main、Post-Run、调用次数与 Provider Total', () => {
    const summary: RunUsageSummary = {
      main_agent: {
        input_tokens: 94_600,
        output_tokens: 2_800,
        total_tokens: 97_400,
        cached_input_tokens: 71_200,
        uncached_input_tokens: 23_400,
        cache_read_input_tokens: 71_200,
        cache_write_input_tokens: 4_000,
        model_calls: 9,
      },
      memory_reflection: { input_tokens: 2_900, output_tokens: 200, total_tokens: 3_100, model_calls: 1 },
      memory_maintenance: { input_tokens: 0, output_tokens: 0, total_tokens: 0, model_calls: 0 },
      provider_total: { input_tokens: 97_500, output_tokens: 3_000, total_tokens: 100_500, model_calls: 10 },
      tool_schema_tokens_estimated: 39_000,
      memory_reflection_status: 'completed',
      memory_reflection_skip_reason: null,
      main_agent_chargeable_tokens: 26_200,
      run_budget_status: 'warning',
      run_budget_reason: 'model_calls',
      run_budget_warning_tokens: 50_000,
      run_budget_finalization_tokens: 75_000,
      run_budget_hard_tokens: 100_000,
      run_budget_warning_model_calls: 8,
      run_budget_finalization_model_calls: 10,
      run_budget_hard_model_calls: 12,
    }
    const html = renderToStaticMarkup(<UsageInspector summary={summary} />)
    expect(html).toContain('94.6k processed')
    expect(html).toContain('71.2k')
    expect(html).toContain('Memory Reflection')
    expect(html).toContain('100.5k')
    expect(html).toContain('10 calls')
    expect(html).toContain('≈39k')
    expect(html).toContain('26.2k budgeted')
    expect(html).toContain('Triggered by model_calls')
  })

  it('缓存字段未知时不显示成零', () => {
    const summary: RunUsageSummary = {
      main_agent: { input_tokens: 10, output_tokens: 2, total_tokens: 12, model_calls: 1 },
      memory_reflection: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
      memory_maintenance: { input_tokens: 0, output_tokens: 0, total_tokens: 0 },
      provider_total: { input_tokens: 10, output_tokens: 2, total_tokens: 12, model_calls: 1 },
      tool_schema_tokens_estimated: 0,
      memory_reflection_status: 'skipped',
      memory_reflection_skip_reason: 'gate:smalltalk',
      main_agent_chargeable_tokens: 12,
      run_budget_status: 'active',
      run_budget_reason: null,
      run_budget_warning_tokens: 50_000,
      run_budget_finalization_tokens: 75_000,
      run_budget_hard_tokens: 100_000,
      run_budget_warning_model_calls: 8,
      run_budget_finalization_model_calls: 10,
      run_budget_hard_model_calls: 12,
    }
    const html = renderToStaticMarkup(<UsageInspector summary={summary} />)
    expect(html).toContain('Unavailable')
    expect(html).toContain('Unavailable 不等于 0')
    expect(html).toContain('Skipped · gate:smalltalk')
  })
})
