/** macOS 风格命令面板：搜索、方向键、Enter 执行、Esc 关闭。 */

import { useEffect, useMemo, useRef, useState } from 'react'

import { Icon, type IconName } from './Icon'

export interface ComposerCommand {
  id: string
  label: string
  icon?: IconName
  hint?: string
  disabled?: boolean
  onSelect: () => void
}

/** 命令面板的搜索逻辑保持为纯函数，方便独立验证键盘交互。 */
export function filterCommands(
  commands: ComposerCommand[],
  query: string,
): ComposerCommand[] {
  const needle = query.trim().toLowerCase()
  return commands.filter((command) =>
    !needle || `${command.label} ${command.hint ?? ''}`.toLowerCase().includes(needle),
  )
}

/** 在可用命令间循环移动，跳过 disabled 项。 */
export function nextEnabledCommandIndex(
  commands: ComposerCommand[],
  current: number,
  direction: 1 | -1,
): number {
  if (commands.length === 0) return 0
  for (let offset = 1; offset <= commands.length; offset += 1) {
    const index = (current + direction * offset + commands.length) % commands.length
    if (!commands[index].disabled) return index
  }
  return Math.max(0, Math.min(current, commands.length - 1))
}

export default function CommandPalette({
  open,
  commands,
  onClose,
}: {
  open: boolean
  commands: ComposerCommand[]
  onClose: () => void
}): React.JSX.Element | null {
  const [query, setQuery] = useState('')
  const [selected, setSelected] = useState(0)
  const inputRef = useRef<HTMLInputElement>(null)
  const filtered = useMemo(() => filterCommands(commands, query), [commands, query])

  useEffect(() => {
    if (!open) return
    setQuery('')
    setSelected(0)
    requestAnimationFrame(() => inputRef.current?.focus())
  }, [open])

  useEffect(() => {
    setSelected((current) => {
      const bounded = Math.min(current, Math.max(0, filtered.length - 1))
      if (!filtered[bounded]?.disabled) return bounded
      return nextEnabledCommandIndex(filtered, bounded, 1)
    })
  }, [filtered])

  if (!open) return null

  const execute = (index: number): void => {
    const command = filtered[index]
    if (!command || command.disabled) return
    onClose()
    command.onSelect()
  }

  return (
    <div className="command-palette-layer" onMouseDown={onClose}>
      <div
        className="command-palette"
        role="dialog"
        aria-modal="true"
        aria-label="Vesta commands"
        onMouseDown={(event) => event.stopPropagation()}
      >
        <div className="command-palette__search">
          <Icon name="activity" size={14} />
          <input
            ref={inputRef}
            value={query}
            placeholder="Search commands…"
            aria-label="Search commands"
            onChange={(event) => setQuery(event.target.value)}
            onKeyDown={(event) => {
              if (event.key === 'Escape') {
                event.preventDefault()
                onClose()
              } else if (event.key === 'ArrowDown') {
                event.preventDefault()
                setSelected((value) => nextEnabledCommandIndex(filtered, value, 1))
              } else if (event.key === 'ArrowUp') {
                event.preventDefault()
                setSelected((value) => nextEnabledCommandIndex(filtered, value, -1))
              } else if (event.key === 'Enter') {
                event.preventDefault()
                execute(selected)
              }
            }}
          />
          <kbd>esc</kbd>
        </div>
        <div className="command-palette__list" role="listbox">
          {filtered.length === 0 ? (
            <div className="command-palette__empty">No matching commands</div>
          ) : filtered.map((command, index) => (
            <button
              key={command.id}
              type="button"
              role="option"
              aria-selected={selected === index}
              disabled={command.disabled}
              className={`command-palette__item ${selected === index ? 'selected' : ''}`}
              onMouseEnter={() => setSelected(index)}
              onClick={() => execute(index)}
            >
              <span>{command.icon ? <Icon name={command.icon} size={14} /> : null}</span>
              <strong>{command.label}</strong>
              {command.hint ? <small>{command.hint}</small> : null}
            </button>
          ))}
        </div>
        <footer className="command-palette__footer">
          <span><kbd>↑</kbd><kbd>↓</kbd> navigate</span>
          <span><kbd>↵</kbd> open</span>
        </footer>
      </div>
    </div>
  )
}
