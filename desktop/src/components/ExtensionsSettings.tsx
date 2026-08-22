/** 扩展能力设置：安装 Skill，并用结构化表单生成 MCP JSON。 */

import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useMemo, useState } from 'react'

import {
  addMCPServer,
  applyExtensionImport,
  deleteMCPServer,
  deleteSkill,
  installSkill,
  listExtensions,
  previewExtensionImport,
  setMCPServerEnabled,
  setSkillEnabled,
  type AddMCPServerInput,
  type ExtensionImportInput,
  type ExtensionImportPlan,
  type InstallSkillInput,
  type MCPPermission,
  type ManagedMCPServer,
} from '../api/extensions'
import { ConfirmDialog } from './ConfirmDialog'
import { Icon } from './Icon'
import { EmptyState, ErrorState, LoadingState } from './PageStates'
import { toast } from '../stores/toasts'

type ExtensionTab = 'skills' | 'mcp'

export default function ExtensionsSettings(): React.JSX.Element {
  const [tab, setTab] = useState<ExtensionTab>('skills')
  const [showAdd, setShowAdd] = useState(false)
  const [showImporter, setShowImporter] = useState(false)
  const [deleteTarget, setDeleteTarget] = useState<
    | { kind: 'skill'; name: string; scope: 'user' | 'project'; enabled: boolean }
    | { kind: 'mcp'; name: string }
    | null
  >(null)
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['extensions'],
    queryFn: listExtensions,
    refetchInterval: 5000,
    retry: false,
  })

  const refresh = (): void => {
    void queryClient.invalidateQueries({ queryKey: ['extensions'] })
  }
  const skillMutation = useMutation({
    mutationFn: installSkill,
    onSuccess: (skill) => {
      toast.success(`Skill ${skill.name} 已安装`)
      setShowAdd(false)
      refresh()
    },
  })
  const mcpMutation = useMutation({
    mutationFn: addMCPServer,
    onSuccess: (result) => {
      toast.success(`MCP ${result.server.name} 已写入配置`)
      setShowAdd(false)
      refresh()
    },
  })
  const skillControlMutation = useMutation({
    mutationFn: async (action: { name: string; scope: 'user' | 'project'; enabled: boolean; delete?: boolean }): Promise<void> => {
      if (action.delete) await deleteSkill(action.name, action.scope, action.enabled)
      else await setSkillEnabled(action.name, action.scope, !action.enabled)
    },
    onSuccess: (_result, action) => {
      toast.info(action.delete ? `Skill ${action.name} 已删除` : `Skill ${action.name} 已${action.enabled ? '停用' : '启用'}`)
      setDeleteTarget(null)
      refresh()
    },
    onError: (error: unknown) => toast.error(error instanceof Error ? error.message : String(error)),
  })
  const mcpControlMutation = useMutation({
    mutationFn: async (action: { name: string; enabled?: boolean; delete?: boolean }): Promise<void> => {
      if (action.delete) await deleteMCPServer(action.name)
      else await setMCPServerEnabled(action.name, !action.enabled)
    },
    onSuccess: (_result, action) => {
      toast.info(action.delete ? `MCP ${action.name} 已从配置删除，重启后生效` : `MCP ${action.name} 已${action.enabled ? '停用' : '启用'}，重启后生效`)
      setDeleteTarget(null)
      refresh()
    },
    onError: (error: unknown) => toast.error(error instanceof Error ? error.message : String(error)),
  })

  return (
    <div className="extensions-settings">
      <header className="extensions-header">
        <div>
          <span className="extensions-header__eyebrow">Extensions</span>
          <h2>扩展 Vesta 的工作能力</h2>
          <p>Skill 提供可复用方法，MCP Server 提供外部工具。安装操作由 Host 校验并写入。</p>
        </div>
        <div className="extensions-header__actions">
          <button className="btn" onClick={() => { setShowImporter((value) => !value); setShowAdd(false) }}>
            {showImporter ? '收起导入' : '统一导入'}
          </button>
          <button className="btn btn-primary" onClick={() => { setShowAdd((value) => !value); setShowImporter(false) }}>
            {showAdd ? '收起' : tab === 'skills' ? '手动添加 Skill' : '手动添加 MCP'}
          </button>
        </div>
      </header>

      <div className="extensions-tabs" role="tablist" aria-label="扩展类型">
        <button className={tab === 'skills' ? 'active' : ''} onClick={() => { setTab('skills'); setShowAdd(false) }}>
          Skills <span>{query.data?.skills.length ?? 0}</span>
        </button>
        <button className={tab === 'mcp' ? 'active' : ''} onClick={() => { setTab('mcp'); setShowAdd(false) }}>
          MCP Servers <span>{query.data?.mcp.servers.length ?? 0}</span>
        </button>
      </div>

      {showImporter ? (
        <UnifiedImportForm
          onInstalled={(result) => {
            const count = result.skills.length + result.mcp_servers.length
            toast.success(`已导入 ${count} 个扩展${result.restart_required ? '，MCP 重启 Host 后生效' : ''}`)
            setShowImporter(false)
            refresh()
          }}
          onCancel={() => setShowImporter(false)}
        />
      ) : null}

      {showAdd && tab === 'skills' ? (
        <SkillInstallForm
          busy={skillMutation.isPending}
          serverError={skillMutation.error}
          onSubmit={(input) => skillMutation.mutateAsync(input)}
          onCancel={() => setShowAdd(false)}
        />
      ) : null}
      {showAdd && tab === 'mcp' ? (
        <MCPInstallForm
          busy={mcpMutation.isPending}
          serverError={mcpMutation.error}
          onSubmit={(input) => mcpMutation.mutateAsync(input)}
          onCancel={() => setShowAdd(false)}
        />
      ) : null}

      {query.isPending ? <LoadingState label="正在读取扩展能力…" />
        : query.isError ? <ErrorState message={String(query.error)} onRetry={() => void query.refetch()} />
          : tab === 'skills' ? (
            <SkillList
              skills={query.data?.skills ?? []}
              diagnostics={query.data?.skill_diagnostics ?? []}
              busy={skillControlMutation.isPending}
              onToggle={(skill) => skillControlMutation.mutate(skill)}
              onDelete={(skill) => setDeleteTarget({ kind: 'skill', ...skill })}
            />
          ) : (
            <MCPList
              servers={query.data?.mcp.servers ?? []}
              configPath={query.data?.mcp.config_path ?? ''}
              configError={query.data?.mcp.error ?? null}
              restartRequired={query.data?.mcp.restart_required ?? false}
              busy={mcpControlMutation.isPending}
              onToggle={(server) => mcpControlMutation.mutate({ name: server.name, enabled: server.enabled })}
              onDelete={(server) => setDeleteTarget({ kind: 'mcp', name: server.name })}
            />
          )}
      <ConfirmDialog
        open={deleteTarget !== null}
        title={deleteTarget?.kind === 'skill' ? `删除 Skill ${deleteTarget.name}？` : `删除 MCP ${deleteTarget?.name ?? ''}？`}
        message={deleteTarget?.kind === 'skill' ? '这会永久删除该 Skill 目录及其中的资源文件，无法恢复。' : '这会从 mcp.json 删除该 Server；当前连接会在 Host 重启后关闭。'}
        confirmLabel="删除"
        busy={skillControlMutation.isPending || mcpControlMutation.isPending}
        onConfirm={() => {
          if (deleteTarget?.kind === 'skill') skillControlMutation.mutate({ ...deleteTarget, delete: true })
          if (deleteTarget?.kind === 'mcp') mcpControlMutation.mutate({ name: deleteTarget.name, delete: true })
        }}
        onCancel={() => setDeleteTarget(null)}
      />
    </div>
  )
}

export function UnifiedImportForm({
  onInstalled,
  onCancel,
}: {
  onInstalled: (result: Awaited<ReturnType<typeof applyExtensionImport>>) => void
  onCancel: () => void
}): React.JSX.Element {
  const [rawInput, setRawInput] = useState('')
  const [scope, setScope] = useState<'user' | 'project'>('project')
  const [permission, setPermission] = useState<MCPPermission>('human_approval')
  const [plan, setPlan] = useState<ExtensionImportPlan | null>(null)
  const [previewedInput, setPreviewedInput] = useState<ExtensionImportInput | null>(null)
  const previewMutation = useMutation({ mutationFn: previewExtensionImport })
  const applyMutation = useMutation({ mutationFn: applyExtensionImport })

  const resetPreview = (): void => {
    setPlan(null)
    setPreviewedInput(null)
    previewMutation.reset()
    applyMutation.reset()
  }
  const buildInput = (): ExtensionImportInput => ({
    input: rawInput.trim(),
    skill_scope: scope,
    mcp_permission: permission,
  })
  const preview = async (): Promise<void> => {
    const input = buildInput()
    if (!input.input) return
    const nextPlan = await previewMutation.mutateAsync(input)
    setPreviewedInput(input)
    setPlan(nextPlan)
  }
  const install = async (): Promise<void> => {
    if (!plan || !previewedInput) return
    const result = await applyMutation.mutateAsync({
      ...previewedInput,
      fingerprint: plan.fingerprint,
      confirmed: true,
    })
    onInstalled(result)
  }
  const error = previewMutation.error ?? applyMutation.error

  return (
    <section className="extension-import">
      <div className="extension-import__intro">
        <div>
          <span className="extensions-header__eyebrow">Import</span>
          <h3>从外部来源导入</h3>
          <p>粘贴 GitHub URL、owner/repo、<code>npx skills add</code> 命令或外部 <code>mcpServers</code> JSON。</p>
        </div>
        <span className="extension-import__safe"><Icon name="check" size={13} />预览阶段不会联网或执行命令</span>
      </div>

      <div className="extension-import__input">
        <label>
          <span>扩展来源或配置</span>
          <textarea
            rows={9}
            value={rawInput}
            onChange={(event) => { setRawInput(event.target.value); resetPreview() }}
            placeholder={'assafelovic/gpt-researcher\n\n或粘贴 { "mcpServers": { ... } }'}
          />
        </label>
        <div className="extension-import__options">
          <label><span>Skill 安装范围</span><select value={scope} onChange={(event) => { setScope(event.target.value as 'user' | 'project'); resetPreview() }}><option value="project">当前项目</option><option value="user">用户全局</option></select></label>
          <label><span>MCP 默认权限</span><select value={permission} onChange={(event) => { setPermission(event.target.value as MCPPermission); resetPreview() }}><option value="human_approval">需要审批（推荐）</option><option value="allowed">自动允许</option><option value="forbidden">禁止调用</option></select></label>
        </div>
      </div>

      {plan ? <ImportPlanPreview plan={plan} /> : (
        <div className="extension-import__empty">
          <strong>先生成安全预览</strong>
          <span>Vesta 会识别 Skill 安装命令与 MCP Server，并展示实际动作。</span>
        </div>
      )}
      {error ? <div className="error-text extension-form__error">{error instanceof Error ? error.message : String(error)}</div> : null}
      <div className="extension-form__actions">
        {plan ? (
          <button className="btn btn-primary" disabled={applyMutation.isPending} onClick={() => void install()}>
            {applyMutation.isPending ? '正在导入…' : plan.requires_download ? '确认下载并安装' : '确认写入配置'}
          </button>
        ) : (
          <button className="btn btn-primary" disabled={!rawInput.trim() || previewMutation.isPending} onClick={() => void preview()}>
            {previewMutation.isPending ? '正在解析…' : '生成导入预览'}
          </button>
        )}
        <button className="btn" disabled={applyMutation.isPending} onClick={onCancel}>取消</button>
      </div>
    </section>
  )
}

function ImportPlanPreview({ plan }: { plan: ExtensionImportPlan }): React.JSX.Element {
  return (
    <div className="extension-import__preview">
      <div className="extension-import__detected">
        <span>识别结果</span>
        {plan.items.map((item) => (
          <article key={`${item.kind}:${item.name}`}>
            <span className={`extension-import__kind extension-import__kind--${item.kind}`}>{item.kind === 'skill' ? 'Skill' : 'MCP'}</span>
            <div><strong>{item.name}</strong><p>{item.summary}</p><small>{item.source}</small></div>
          </article>
        ))}
      </div>
      <div className="extension-import__actions-preview">
        <span>确认后执行</span>
        <ol>{plan.actions.map((action, index) => <li key={`${index}:${action}`}>{action}</li>)}</ol>
        {plan.warnings.map((warning) => <p className="extension-import__warning" key={warning}>{warning}</p>)}
        {plan.requires_restart ? <p className="extension-import__restart">MCP 只写入配置，重启 Vesta Host 后才会启动。</p> : null}
      </div>
    </div>
  )
}

function SkillList({
  skills,
  diagnostics,
  busy,
  onToggle,
  onDelete,
}: {
  skills: Awaited<ReturnType<typeof listExtensions>>['skills']
  diagnostics: Awaited<ReturnType<typeof listExtensions>>['skill_diagnostics']
  busy: boolean
  onToggle: (skill: { name: string; scope: 'user' | 'project'; enabled: boolean }) => void
  onDelete: (skill: { name: string; scope: 'user' | 'project'; enabled: boolean }) => void
}): React.JSX.Element {
  return (
    <div className="extension-list-wrap">
      {skills.length === 0 ? (
        <EmptyState title="尚未安装 Skill" hint="添加一个方法包，让 Vesta 在相关任务中按需激活。" icon="memory" />
      ) : (
        <div className="extension-list">
          {skills.map((skill) => (
            <article className={`extension-row ${skill.enabled ? '' : 'extension-row--disabled'}`} key={`${skill.scope}:${skill.name}`}>
              <span className="extension-row__icon"><Icon name="memory" size={15} /></span>
              <div className="extension-row__main">
                <div><strong>{skill.name}</strong><span className="extension-scope">{skill.scope === 'project' ? '当前项目' : '用户全局'}</span>{!skill.enabled ? <span className="extension-scope">已停用</span> : null}</div>
                <p>{skill.description}</p>
              </div>
              <span className="extension-row__path mono" title={skill.location}>{skill.location}</span>
              <div className="extension-row__actions">
                <button className="btn btn-sm" disabled={busy} onClick={() => onToggle(skill)}>{skill.enabled ? '停用' : '启用'}</button>
                <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => onDelete(skill)}>删除</button>
              </div>
            </article>
          ))}
        </div>
      )}
      {diagnostics.length > 0 ? (
        <details className="extension-diagnostics">
          <summary>{diagnostics.length} 个 Skill 未能加载</summary>
          {diagnostics.map((item) => <p key={`${item.scope}:${item.location}`}><strong>{item.name}</strong> · {item.reason}</p>)}
        </details>
      ) : null}
    </div>
  )
}

function MCPList({
  servers,
  configPath,
  configError,
  restartRequired,
  busy,
  onToggle,
  onDelete,
}: {
  servers: ManagedMCPServer[]
  configPath: string
  configError: string | null
  restartRequired: boolean
  busy: boolean
  onToggle: (server: ManagedMCPServer) => void
  onDelete: (server: ManagedMCPServer) => void
}): React.JSX.Element {
  return (
    <div className="extension-list-wrap">
      <div className="mcp-config-note">
        <div><strong>配置文件</strong><span className="mono">{configPath || '尚未创建'}</span></div>
        <p>{restartRequired ? '配置已有变更等待应用，请重启 Vesta Host。' : '新增 Server 会生成标准 JSON；保存后重启 Vesta Host 才会连接并注册工具。'}</p>
      </div>
      {restartRequired ? <div className="extension-restart-notice">MCP 配置与当前运行进程不同步，重启 Host 前旧连接与工具仍可能继续存在。</div> : null}
      {configError ? <div className="error-text extension-error">{configError}</div> : null}
      {servers.length === 0 ? (
        <EmptyState title="尚未添加 MCP Server" hint="通过 stdio 连接可信的本地 MCP Server。" icon="computer" />
      ) : (
        <div className="extension-list">
          {servers.map((server) => (
            <article className="extension-row extension-row--mcp" key={server.name}>
              <span className={`mcp-state mcp-state--${server.state}`} aria-hidden="true" />
              <div className="extension-row__main">
                <div><strong>{server.name}</strong><span className="extension-scope">{server.enabled ? mcpStateLabel(server.state) : '已停用'}</span></div>
                <p className="mono">{[server.command, ...server.args].join(' ')}</p>
                {server.error ? <small className="error-text">{server.error}</small> : null}
              </div>
              <div className="mcp-row__meta">
                <span>{permissionLabel(server.permission)}</span>
                <span>{server.tool_names.length} 个工具</span>
                {server.env_names.length ? <span>{server.env_names.length} 个环境变量</span> : null}
              </div>
              <div className="extension-row__actions">
                <button className="btn btn-sm" disabled={busy} onClick={() => onToggle(server)}>{server.enabled ? '停用' : '启用'}</button>
                <button className="btn btn-sm btn-danger" disabled={busy} onClick={() => onDelete(server)}>删除</button>
              </div>
            </article>
          ))}
        </div>
      )}
    </div>
  )
}

export function SkillInstallForm({
  busy,
  serverError,
  onSubmit,
  onCancel,
}: {
  busy: boolean
  serverError: unknown
  onSubmit: (input: InstallSkillInput) => Promise<unknown>
  onCancel: () => void
}): React.JSX.Element {
  const [name, setName] = useState('')
  const [description, setDescription] = useState('')
  const [instructions, setInstructions] = useState('')
  const [scope, setScope] = useState<'user' | 'project'>('project')
  const [error, setError] = useState<string | null>(null)

  const preview = useMemo(() => `---\nname: ${name || 'my-skill'}\ndescription: ${description || '这个 Skill 适合解决什么任务'}\n---\n\n${instructions || '# 操作方法\n\n1. 描述执行步骤。'}`, [description, instructions, name])
  const submit = async (): Promise<void> => {
    setError(null)
    if (!/^[a-z0-9]+(?:-[a-z0-9]+)*$/.test(name) || name.length > 64) {
      setError('名称只能使用小写字母、数字和单连字符，最长 64 个字符。')
      return
    }
    if (!description.trim() || !instructions.trim()) {
      setError('请填写用途说明和完整指令。')
      return
    }
    await onSubmit({ name, description: description.trim(), instructions: instructions.trim(), scope })
  }

  return (
    <section className="extension-form">
      <div className="extension-form__fields">
        <div className="extension-form__heading"><strong>添加 Skill</strong><span>Host 会生成并校验 SKILL.md</span></div>
        <label><span>名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 release-check" /></label>
        <label><span>用途说明</span><input value={description} onChange={(event) => setDescription(event.target.value)} placeholder="说明什么情况下应该使用这个 Skill" /></label>
        <label><span>安装范围</span><select value={scope} onChange={(event) => setScope(event.target.value as 'user' | 'project')}><option value="project">当前项目</option><option value="user">用户全局</option></select></label>
        <label><span>执行指令（Markdown）</span><textarea rows={9} value={instructions} onChange={(event) => setInstructions(event.target.value)} placeholder="# 操作方法&#10;&#10;1. 先确认输入…&#10;2. 执行…&#10;3. 验证结果…" /></label>
      </div>
      <div className="extension-form__preview"><span>文件预览</span><pre>{preview}</pre></div>
      {(error || serverError) ? <div className="error-text extension-form__error">{error ?? String(serverError)}</div> : null}
      <div className="extension-form__actions"><button className="btn btn-primary" disabled={busy} onClick={() => void submit()}>{busy ? '正在安装…' : '安装 Skill'}</button><button className="btn" onClick={onCancel}>取消</button></div>
    </section>
  )
}

export function MCPInstallForm({
  busy,
  serverError,
  onSubmit,
  onCancel,
}: {
  busy: boolean
  serverError: unknown
  onSubmit: (input: AddMCPServerInput) => Promise<unknown>
  onCancel: () => void
}): React.JSX.Element {
  const [name, setName] = useState('')
  const [command, setCommand] = useState('')
  const [argsText, setArgsText] = useState('')
  const [envText, setEnvText] = useState('')
  const [cwd, setCwd] = useState('')
  const [permission, setPermission] = useState<MCPPermission>('human_approval')
  const [error, setError] = useState<string | null>(null)
  const args = useMemo(() => lines(argsText), [argsText])
  const envResult = useMemo(() => parseEnv(envText), [envText])
  const preview = useMemo(() => ({
    servers: [{ name: name || 'example', transport: 'stdio', command: command || 'npx', args, ...(cwd.trim() ? { cwd: cwd.trim() } : {}), env: envResult.env, permission }],
  }), [args, command, cwd, envResult.env, name, permission])

  const submit = async (): Promise<void> => {
    setError(null)
    if (!/^[a-zA-Z0-9_]+$/.test(name)) {
      setError('Server 名称只能包含字母、数字和下划线。')
      return
    }
    if (!command.trim()) {
      setError('启动命令不能为空。')
      return
    }
    if (envResult.error) {
      setError(envResult.error)
      return
    }
    await onSubmit({ name, command: command.trim(), args, env: envResult.env, cwd: cwd.trim() || undefined, enabled: true, permission })
  }

  return (
    <section className="extension-form">
      <div className="extension-form__fields">
        <div className="extension-form__heading"><strong>添加 MCP Server</strong><span>当前支持 stdio；每行一个参数，避免引号和空格歧义</span></div>
        <label><span>Server 名称</span><input value={name} onChange={(event) => setName(event.target.value)} placeholder="例如 filesystem" /></label>
        <label><span>启动命令</span><input value={command} onChange={(event) => setCommand(event.target.value)} placeholder="例如 npx 或 /usr/local/bin/uvx" /></label>
        <label><span>参数（每行一个）</span><textarea rows={6} value={argsText} onChange={(event) => setArgsText(event.target.value)} placeholder={'-y\n@modelcontextprotocol/server-filesystem\n/Users/me/workspace'} /></label>
        <label><span>工作目录（可选）</span><input value={cwd} onChange={(event) => setCwd(event.target.value)} placeholder="留空则继承 Host 工作目录" /></label>
        <label><span>环境变量（每行 KEY=VALUE）</span><textarea rows={4} value={envText} onChange={(event) => setEnvText(event.target.value)} placeholder={'API_KEY=${API_KEY}\nLOG_LEVEL=info'} /><small>密钥请写成 <code>{'${ENV_NAME}'}</code> 引用，不要把真实密钥保存进 JSON。</small></label>
        <label><span>权限</span><select value={permission} onChange={(event) => setPermission(event.target.value as MCPPermission)}><option value="human_approval">每次按规则审批（推荐）</option><option value="allowed">自动允许（仅可信只读工具）</option><option value="forbidden">禁止调用</option></select></label>
      </div>
      <div className="extension-form__preview"><span>mcp.json 预览</span><pre>{JSON.stringify(preview, null, 2)}</pre></div>
      {(error || serverError) ? <div className="error-text extension-form__error">{error ?? String(serverError)}</div> : null}
      <div className="extension-form__notice">保存只写配置，不会在当前进程中执行命令。重启 Vesta Host 后连接生效。</div>
      <div className="extension-form__actions"><button className="btn btn-primary" disabled={busy} onClick={() => void submit()}>{busy ? '正在保存…' : '保存 MCP 配置'}</button><button className="btn" onClick={onCancel}>取消</button></div>
    </section>
  )
}

function lines(value: string): string[] {
  return value.split('\n').map((item) => item.trim()).filter(Boolean)
}

export function parseEnv(value: string): { env: Record<string, string>; error: string | null } {
  const env: Record<string, string> = {}
  for (const line of lines(value)) {
    const separator = line.indexOf('=')
    if (separator <= 0) return { env: {}, error: `环境变量格式错误：${line}` }
    const key = line.slice(0, separator).trim()
    const envValue = line.slice(separator + 1).trim()
    if (!/^[A-Za-z_][A-Za-z0-9_]*$/.test(key) || !envValue) return { env: {}, error: `环境变量格式错误：${line}` }
    env[key] = envValue
  }
  return { env, error: null }
}

function mcpStateLabel(state: ManagedMCPServer['state']): string {
  if (state === 'running') return '已连接'
  if (state === 'failed') return '启动失败'
  if (state === 'starting') return '正在启动'
  if (state === 'restart_required') return '等待重启'
  return '已停止'
}

function permissionLabel(permission: MCPPermission): string {
  if (permission === 'allowed') return '自动允许'
  if (permission === 'forbidden') return '禁止调用'
  return '需要审批'
}
