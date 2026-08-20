/** AssistantContent：react-markdown + remark-gfm + Shiki 渲染测试（SSR 纯文本回退）。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import { AssistantContent } from './AssistantContent'

describe('AssistantContent', () => {
  it('渲染 GFM 表格', () => {
    const html = renderToStaticMarkup(
      <AssistantContent
        content={'| 名称 | 状态 |\n| --- | --- |\n| run_1 | running |\n| run_2 | done |'}
      />,
    )
    expect(html).toContain('<table>')
    expect(html).toContain('<th>名称</th>')
    expect(html).toContain('<td>running</td>')
  })

  it('渲染 GFM 任务列表', () => {
    const html = renderToStaticMarkup(
      <AssistantContent content={'- [x] 完成\n- [ ] 待办'} />,
    )
    expect(html).toContain('contains-task-list')
    expect(html).toContain('task-list-item')
    expect(html).toContain('type="checkbox"')
  })

  it('渲染 blockquote 与删除线', () => {
    const html = renderToStaticMarkup(
      <AssistantContent content={'> 引用内容\n\n~~划掉~~'} />,
    )
    expect(html).toContain('<blockquote>')
    expect(html).toContain('引用内容')
    expect(html).toContain('<del>划掉</del>')
  })

  it('代码块 SSR 回退为纯文本 pre，不渲染高亮 HTML', () => {
    const html = renderToStaticMarkup(
      <AssistantContent content={'```ts\nconst a = 1\n```'} />,
    )
    expect(html).toContain('<pre class="assistant-code">')
    expect(html).toContain('const a = 1')
    // Shiki 高亮是异步客户端行为，SSR 阶段不应出现 shiki 结构。
    expect(html).not.toContain('shiki')
  })

  it('原始 HTML 默认被转义（安全渲染）', () => {
    const html = renderToStaticMarkup(
      <AssistantContent content={'<script>alert(1)</script> 安全'} />,
    )
    expect(html).not.toContain('<script>')
    expect(html).toContain('安全')
  })

  it('外部链接自动加 target=_blank / rel=noreferrer', () => {
    const html = renderToStaticMarkup(
      <AssistantContent content={'[官网](https://example.com)'} />,
    )
    expect(html).toContain('href="https://example.com"')
    expect(html).toContain('target="_blank"')
    expect(html).toContain('rel="noreferrer"')
  })
})
