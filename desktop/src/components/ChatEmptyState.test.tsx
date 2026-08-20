/** ChatEmptyState：品牌入口与四个示例任务测试。 */

import { describe, expect, it } from 'vitest'
import { renderToStaticMarkup } from 'react-dom/server'

import ChatEmptyState, { EXAMPLE_PROMPTS } from './ChatEmptyState'

describe('ChatEmptyState', () => {
  it('渲染欢迎信息和四个可点击示例', () => {
    const html = renderToStaticMarkup(<ChatEmptyState onSelectPrompt={() => {}} />)
    expect(html).toContain('What can I work on for you?')
    expect(html.match(/<button/g)).toHaveLength(4)
    for (const prompt of EXAMPLE_PROMPTS) expect(html).toContain(prompt)
  })
})
