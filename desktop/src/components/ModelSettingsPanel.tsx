import { useMutation, useQuery, useQueryClient } from '@tanstack/react-query'
import { useEffect, useMemo, useState } from 'react'

import {
  getModelSettings,
  testModelConnection,
  updateModelSettings,
  type ApiStyle,
  type ModelProvider,
  type ModelRoleSettings,
  type ModelSettingsUpdate,
  type ProviderModelSettingsUpdate,
} from '../api/modelSettings'
import { ErrorState } from './PageStates'

const PROVIDER_ORDER: ModelProvider[] = ['openai', 'qwen', 'deepseek', 'anthropic']
const PROVIDER_LABELS: Record<ModelProvider, string> = {
  openai: 'OpenAI',
  qwen: 'Qwen',
  deepseek: 'DeepSeek',
  anthropic: 'Claude',
}

type ProviderDraft = ProviderModelSettingsUpdate & {
  configured: boolean
  keySource: 'keychain' | 'environment' | 'none'
}

export default function ModelSettingsPanel(): React.JSX.Element {
  const queryClient = useQueryClient()
  const query = useQuery({
    queryKey: ['model-settings'],
    queryFn: getModelSettings,
    retry: false,
  })
  const [selected, setSelected] = useState<ModelProvider>('openai')
  const [defaultProvider, setDefaultProvider] = useState<ModelProvider>('openai')
  const [providers, setProviders] = useState<ProviderDraft[]>([])
  const [reflection, setReflection] = useState<ModelRoleSettings>(defaultRole())
  const [maintenance, setMaintenance] = useState<ModelRoleSettings>(defaultRole())
  const [notice, setNotice] = useState<string | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!query.data) return
    setDefaultProvider(query.data.default_provider)
    setSelected(query.data.default_provider)
    setProviders(query.data.providers.map((item) => ({
      provider: item.provider,
      model: item.model,
      base_url: item.base_url,
      api_style: item.api_style,
      configured: item.configured,
      keySource: item.key_source,
    })))
    setReflection(query.data.reflection)
    setMaintenance(query.data.maintenance)
  }, [query.data])

  const current = useMemo(
    () => providers.find((item) => item.provider === selected),
    [providers, selected],
  )

  const saveMutation = useMutation({
    mutationFn: (input: ModelSettingsUpdate) => updateModelSettings(input),
    onSuccess: (data) => {
      queryClient.setQueryData(['model-settings'], data)
      setProviders((items) => items.map((item) => ({ ...item, api_key: undefined })))
      setError(null)
      setNotice('设置已安全保存。请重启 Vesta Host，让新模型配置生效。')
    },
    onError: (reason) => {
      setNotice(null)
      setError(errorMessage(reason))
    },
  })

  const testMutation = useMutation({
    mutationFn: (input: ProviderModelSettingsUpdate) => testModelConnection(input),
    onSuccess: (result) => {
      setError(null)
      setNotice(`连接成功 · ${result.model} · ${Math.round(result.duration_ms)} ms`)
    },
    onError: (reason) => {
      setNotice(null)
      setError(errorMessage(reason))
    },
  })

  if (query.isLoading) {
    return <div className="settings-loading"><span className="spinner" />正在读取模型设置…</div>
  }
  if (query.isError || !query.data || !current) {
    return <ErrorState message="无法读取模型设置" onRetry={() => void query.refetch()} />
  }

  const updateCurrent = (changes: Partial<ProviderDraft>): void => {
    setProviders((items) => items.map((item) => (
      item.provider === selected ? { ...item, ...changes } : item
    )))
    setNotice(null)
  }

  const save = (): void => {
    setError(null)
    saveMutation.mutate({
      default_provider: defaultProvider,
      providers: providers.map(({ configured: _configured, keySource: _keySource, ...item }) => item),
      reflection: normalizedRole(reflection),
      maintenance: normalizedRole(maintenance),
    })
  }

  return (
    <div className="settings-models">
      <header className="settings-content__header">
        <h2>模型</h2>
        <p>管理主 Agent 与后台记忆任务使用的模型。API Key 不会写入配置文件。</p>
      </header>

      <section className="settings-group">
        <header className="settings-group__header">
          <div><h3>主 Agent</h3><p>对话、工具调用和电脑操作使用的模型</p></div>
          <span className="settings-active-model">当前：{query.data.active_provider} / {query.data.active_model}</span>
        </header>
        <label className="model-field model-field--inline">
          <span>默认提供商</span>
          <select value={defaultProvider} onChange={(event) => setDefaultProvider(event.target.value as ModelProvider)}>
            {PROVIDER_ORDER.map((provider) => <option key={provider} value={provider}>{PROVIDER_LABELS[provider]}</option>)}
          </select>
        </label>
      </section>

      <section className="settings-group">
        <header className="settings-group__header">
          <div><h3>Provider 配置</h3><p>选择提供商后编辑模型、端点和密钥</p></div>
        </header>
        <div className="model-provider-tabs" role="tablist" aria-label="模型提供商">
          {PROVIDER_ORDER.map((provider) => {
            const item = providers.find((candidate) => candidate.provider === provider)
            return (
              <button
                type="button"
                role="tab"
                aria-selected={selected === provider}
                className={selected === provider ? 'active' : ''}
                key={provider}
                onClick={() => setSelected(provider)}
              >
                {PROVIDER_LABELS[provider]}
                <i className={item?.configured ? 'configured' : ''} />
              </button>
            )
          })}
        </div>

        <div className="model-form">
          <label className="model-field">
            <span>模型名称</span>
            <input value={current.model} onChange={(event) => updateCurrent({ model: event.target.value })} />
          </label>
          <label className="model-field">
            <span>API 端点</span>
            <input
              value={current.base_url ?? ''}
              placeholder={selected === 'openai' || selected === 'anthropic' ? '使用官方默认端点' : undefined}
              onChange={(event) => updateCurrent({ base_url: event.target.value || null })}
            />
          </label>
          <label className="model-field">
            <span>API 格式</span>
            <select
              value={current.api_style}
              disabled={selected === 'anthropic'}
              onChange={(event) => updateCurrent({ api_style: event.target.value as ApiStyle })}
            >
              {selected === 'anthropic' ? (
                <option value="anthropic_messages">Anthropic Messages</option>
              ) : (
                <>
                  <option value="responses">Responses</option>
                  <option value="chat_completions">Chat Completions</option>
                </>
              )}
            </select>
          </label>
          <label className="model-field">
            <span>API Key</span>
            <input
              type="password"
              autoComplete="new-password"
              value={current.api_key ?? ''}
              placeholder={current.configured ? `已配置（${keySourceLabel(current.keySource)}）` : '输入 API Key'}
              onChange={(event) => updateCurrent({ api_key: event.target.value })}
            />
            <small>留空会保留现有密钥；新密钥保存到 macOS Keychain。</small>
          </label>
        </div>
        <div className="model-actions model-actions--secondary">
          <button
            type="button"
            className="btn"
            disabled={testMutation.isPending}
            onClick={() => testMutation.mutate(stripDraft(current))}
          >
            {testMutation.isPending ? '正在测试…' : '测试官方连接'}
          </button>
          <span>自定义端点可保存，但出于密钥安全考虑不在此处测试。</span>
        </div>
      </section>

      <section className="settings-group">
        <header className="settings-group__header">
          <div><h3>后台模型</h3><p>记忆反思和容量维护默认继承主模型，也可单独指定</p></div>
        </header>
        <RoleEditor title="记忆反思" value={reflection} providers={providers} onChange={setReflection} />
        <RoleEditor title="容量维护" value={maintenance} providers={providers} onChange={setMaintenance} />
      </section>

      {(notice || error) && <div className={error ? 'model-notice model-notice--error' : 'model-notice'}>{error ?? notice}</div>}
      <div className="model-actions">
        <button type="button" className="btn btn--primary" disabled={saveMutation.isPending} onClick={save}>
          {saveMutation.isPending ? '正在保存…' : '保存模型设置'}
        </button>
        <span>保存不会中断正在执行的 Run。</span>
      </div>
    </div>
  )
}

function RoleEditor({
  title,
  value,
  providers,
  onChange,
}: {
  title: string
  value: ModelRoleSettings
  providers: ProviderDraft[]
  onChange: (value: ModelRoleSettings) => void
}): React.JSX.Element {
  const selectedProvider = value.provider ?? providers[0]?.provider ?? 'openai'
  const selectedModel = value.model ?? providers.find((item) => item.provider === selectedProvider)?.model ?? ''
  return (
    <div className="model-role-row">
      <div className="model-role-row__title"><strong>{title}</strong><label><input type="checkbox" checked={value.enabled} onChange={(event) => onChange({ ...value, enabled: event.target.checked })} />启用</label></div>
      <label className="model-role-inherit">
        <input
          type="checkbox"
          checked={value.inherit_main}
          onChange={(event) => onChange(event.target.checked
            ? { ...value, inherit_main: true, provider: null, model: null }
            : { ...value, inherit_main: false, provider: selectedProvider, model: selectedModel })}
        />
        跟随主模型
      </label>
      {!value.inherit_main && (
        <div className="model-role-custom">
          <select value={selectedProvider} onChange={(event) => {
            const provider = event.target.value as ModelProvider
            onChange({ ...value, provider, model: providers.find((item) => item.provider === provider)?.model ?? '' })
          }}>
            {PROVIDER_ORDER.map((provider) => <option key={provider} value={provider}>{PROVIDER_LABELS[provider]}</option>)}
          </select>
          <input value={selectedModel} onChange={(event) => onChange({ ...value, model: event.target.value })} />
        </div>
      )}
    </div>
  )
}

function defaultRole(): ModelRoleSettings {
  return { enabled: true, inherit_main: true, provider: null, model: null }
}

function normalizedRole(role: ModelRoleSettings): ModelRoleSettings {
  return role.inherit_main ? { ...role, provider: null, model: null } : role
}

function stripDraft({ configured: _configured, keySource: _keySource, ...item }: ProviderDraft): ProviderModelSettingsUpdate {
  return item
}

function keySourceLabel(source: ProviderDraft['keySource']): string {
  return source === 'keychain' ? '钥匙串' : source === 'environment' ? '环境变量' : '未设置'
}

function errorMessage(reason: unknown): string {
  return reason instanceof Error ? reason.message : '操作失败，请检查配置后重试。'
}
