/**
 * Assistant 正文渲染：react-markdown + remark-gfm + Shiki。
 *  - react-markdown 默认转义原始 HTML（安全渲染），remark-gfm 提供表格 / 任务列表 / 删除线。
 *  - Shiki 使用 TextMate grammar，视觉接近 VS Code；走纯 JS 正则引擎，Electron/Vite 无需 WASM。
 *  - 同步渲染 + 客户端异步高亮：SSR / 测试环境代码块回退为纯文本 <pre><code>。
 */

import { Children, isValidElement, memo, useEffect, useRef, useState } from 'react'
import type { ReactElement, ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import type { Components } from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { createHighlighterCore } from 'shiki/core'
import type { HighlighterCore } from 'shiki/core'
import { createJavaScriptRegexEngine } from '@shikijs/engine-javascript'

const THEME = 'github-dark'

/** 支持高亮的语言子路径 bundle（shiki 按需加载 grammar），含常用别名。 */
const LANG_BUNDLES: Record<string, () => Promise<unknown>> = {
  typescript: () => import('@shikijs/langs/typescript'),
  ts: () => import('@shikijs/langs/typescript'),
  javascript: () => import('@shikijs/langs/javascript'),
  js: () => import('@shikijs/langs/javascript'),
  jsx: () => import('@shikijs/langs/jsx'),
  tsx: () => import('@shikijs/langs/tsx'),
  python: () => import('@shikijs/langs/python'),
  py: () => import('@shikijs/langs/python'),
  bash: () => import('@shikijs/langs/bash'),
  shell: () => import('@shikijs/langs/shellscript'),
  sh: () => import('@shikijs/langs/shellscript'),
  shellscript: () => import('@shikijs/langs/shellscript'),
  json: () => import('@shikijs/langs/json'),
  markdown: () => import('@shikijs/langs/markdown'),
  md: () => import('@shikijs/langs/markdown'),
  css: () => import('@shikijs/langs/css'),
  html: () => import('@shikijs/langs/html'),
  yaml: () => import('@shikijs/langs/yaml'),
  sql: () => import('@shikijs/langs/sql'),
  rust: () => import('@shikijs/langs/rust'),
  go: () => import('@shikijs/langs/go'),
}

let highlighterPromise: Promise<HighlighterCore> | null = null

function getHighlighter(): Promise<HighlighterCore> {
  if (!highlighterPromise) {
    const langs = Object.values(LANG_BUNDLES).map((load) => load()) as unknown as Parameters<
      typeof createHighlighterCore
    >[0]['langs']
    highlighterPromise = createHighlighterCore({
      themes: [import('@shikijs/themes/github-dark')],
      langs,
      engine: createJavaScriptRegexEngine(),
    })
  }
  return highlighterPromise
}

/** Shiki 代码块：先渲染纯文本，挂载后异步替换为高亮 HTML。
streaming=true（正文仍在流式增长）时跳过高亮，只显示纯文本 <pre><code>；
完成后由父组件以 streaming=false 重新渲染，再走 debounce 高亮。 */
function CodeBlock({
  code,
  lang,
  streaming,
}: {
  code: string
  lang: string
  streaming: boolean
}): ReactElement {
  const [html, setHtml] = useState<string | null>(null)
  const timerRef = useRef<number | null>(null)

  useEffect(() => {
    if (streaming) return
    // 流式期间 code 会持续变化：做短 debounce，只在停顿后高亮一次，
    // 避免每个 delta 都跑一遍 TextMate 正则高亮（大代码块会卡顿）。
    if (timerRef.current !== null) {
      window.clearTimeout(timerRef.current)
    }
    timerRef.current = window.setTimeout(() => {
      ;(async () => {
        try {
          const highlighter = await getHighlighter()
          const out = highlighter.codeToHtml(code, {
            lang,
            theme: THEME,
          })
          setHtml(out)
        } catch {
          // 高亮失败：保留纯文本 <pre><code>，不影响回复内容。
        }
      })()
    }, 120)
    return () => {
      if (timerRef.current !== null) {
        window.clearTimeout(timerRef.current)
      }
      timerRef.current = null
    }
  }, [code, lang, streaming])

  if (html !== null) {
    return (
      <div
        className="assistant-code"
        dangerouslySetInnerHTML={{ __html: html }}
      />
    )
  }
  return (
    <pre className="assistant-code">
      <code>{code}</code>
    </pre>
  )
}

const createComponents = (streaming: boolean): Components => ({
  // 块级代码由 pre 检测 language-* 后交给 Shiki；无语言的块级代码保留默认 <pre>。
  pre({ children }) {
    const child = Children.toArray(children)[0]
    if (isValidElement<{ className?: string; children?: ReactNode }>(child)) {
      const match = /language-(\w+)/.exec(child.props.className ?? '')
      if (match && match[1]) {
        const lang = match[1]
        const text = String(child.props.children ?? '').replace(/\n$/, '')
        if (lang in LANG_BUNDLES) {
          return <CodeBlock code={text} lang={lang} streaming={streaming} />
        }
        return (
          <pre className="assistant-code">
            <code>{text}</code>
          </pre>
        )
      }
    }
    return <pre>{children}</pre>
  },
  code({ className, children }) {
    return <code className={className}>{children}</code>
  },
  a({ href, children }) {
    const external = href?.startsWith('http://') || href?.startsWith('https://')
    return (
      <a
        href={href}
        {...(external ? { target: '_blank', rel: 'noreferrer' } : {})}
      >
        {children}
      </a>
    )
  },
})

/** Assistant 消息正文。memo：content 引用未变时跳过整块 markdown 解析，
避免父组件因流式事件频繁重渲染导致正文每秒几十次全量解析。
streaming=true 时不执行 Shiki 代码高亮。 */
export const AssistantContent = memo(function AssistantContent({
  content,
  streaming = false,
}: {
  content: string
  streaming?: boolean
}): React.JSX.Element {
  return (
    <div className="message-assistant__body">
      <ReactMarkdown
        remarkPlugins={[remarkGfm]}
        components={createComponents(streaming)}
      >
        {content}
      </ReactMarkdown>
    </div>
  )
})
