/** 扩展设置表单的格式约束与静态展示测试。 */

import { describe, expect, it, vi } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'
import { QueryClient, QueryClientProvider } from '@tanstack/react-query'

import { MCPInstallForm, SkillInstallForm, UnifiedImportForm, parseEnv } from './ExtensionsSettings'

describe('ExtensionsSettings forms', () => {
  it('环境变量按第一个等号拆分并保留占位符', () => {
    expect(parseEnv('API_KEY=${API_KEY}\nMODE=a=b')).toEqual({
      env: { API_KEY: '${API_KEY}', MODE: 'a=b' },
      error: null,
    })
    expect(parseEnv('BAD LINE').error).toContain('格式错误')
  })

  it('Skill 表单展示生成文件预览', () => {
    const html = renderToStaticMarkup(
      <SkillInstallForm busy={false} serverError={null} onSubmit={vi.fn()} onCancel={vi.fn()} />,
    )
    expect(html).toContain('SKILL.md')
    expect(html).toContain('文件预览')
    expect(html).toContain('当前项目')
  })

  it('MCP 表单强调逐行参数、JSON 预览和重启生效', () => {
    const html = renderToStaticMarkup(
      <MCPInstallForm busy={false} serverError={null} onSubmit={vi.fn()} onCancel={vi.fn()} />,
    )
    expect(html).toContain('每行一个')
    expect(html).toContain('mcp.json 预览')
    expect(html).toContain('重启 Vesta Host')
  })

  it('统一导入入口强调先预览且不会执行粘贴命令', () => {
    const client = new QueryClient()
    const html = renderToStaticMarkup(
      <QueryClientProvider client={client}>
        <UnifiedImportForm onInstalled={vi.fn()} onCancel={vi.fn()} />
      </QueryClientProvider>,
    )
    expect(html).toContain('GitHub URL')
    expect(html).toContain('mcpServers')
    expect(html).toContain('预览阶段不会联网或执行命令')
    expect(html).toContain('生成导入预览')
  })
})
