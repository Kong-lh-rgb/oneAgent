"""Application：Vesta 的 composition root（统一依赖装配）。

CLI（``app.models.chat``）与 Vesta Host（``app.server``）共用这一份
“初始化并持有全部运行依赖”的逻辑，避免各自复制一套 wiring。

生命周期：

    app = Application(provider=..., model=...)
    await app.start()   # 建库、建工具表、启动 MCP / Scheduler
    ...                 # 使用 app.conversation_service 等
    await app.close()   # 关闭 Scheduler / MCP / 模型适配器

职责边界：
- 只负责“装配与生命周期”，不实现 Agent 业务逻辑；
- 不重造 ConversationService / RunManager / AgentRuntime 的任何行为；
- CLI 特有的交互（输入循环、打印、命令解析）仍留在 CLI。
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any

from app.agent.budget import RunBudgetConfig
from app.agent.events import AgentEventHandler
from app.agent.post_run_processor import PostRunProcessor
from app.agent.runtime import AgentRuntime
from app.approval import (
    DesktopApprovalGate,
    SQLiteApprovalStore,
)
from app.artifact import (
    ArtifactService,
    SQLiteArtifactStore,
    register_artifact_tools,
)
from app.automation import (
    AutomationScheduler,
    SQLiteAutomationStore,
    register_automation_tools,
)
from app.checkpoint import SQLiteCheckpointStore
from app.computer import (
    ComputerHostStatus,
    ComputerLeaseHook,
    ComputerLeaseManager,
    ComputerRuntime,
    ComputerSessionManager,
    register_computer_tools,
)
from app.context import (
    ContextManager,
    ContextSettings,
    ConversationReducer,
    ModelContextSummarizer,
    SQLiteConversationSummaryStore,
)
from app.conversation import (
    DEFAULT_DATABASE_PATH,
    SQLiteConversationStore,
)
from app.conversation.service import ConversationService
from app.mcp import (
    DEFAULT_MCP_CONFIG_PATH,
    MCPClientManager,
    MCPConfigurationError,
    MCPConfigurationStore,
    MCPStatusTool,
)
from app.memory import (
    DEFAULT_MEMORY_DIR,
    MemoryMaintenanceConfig,
    MemoryMaintenanceReflector,
    MemoryManager,
    MemoryReflectionConfig,
    PostRunMemoryReflector,
    register_memory_tools,
)
from app.models.config import ModelSettings
from app.models.registry import ModelAdapterRegistry
from app.models.types import ModelProvider
from app.run import RunManager, RunStatus, SQLiteRunStore
from app.skill_learning import (
    SkillCandidateStore,
    SkillLearningService,
    SkillLearningSettings,
)
from app.skills import (
    SkillContextProvider,
    SkillSettings,
    SkillStore,
    register_skill_tools,
)
from app.skills.discovery import (
    DEFAULT_PROJECT_SKILLS_DIR,
    DEFAULT_USER_SKILLS_DIR,
)
from app.task import (
    DEFAULT_TASKS_DIR,
    FileTaskStore,
    TaskContextProvider,
    register_task_tools,
)
from app.tools import (
    ConsoleApprovalGate,
    PermissionPolicyEngine,
    SQLitePermissionRuleStore,
    ToolRegistry,
    build_builtin_tool_registry,
    describe_safe_rule,
)
from app.tools.builtin._workspace import workspace_root_path
from app.trace import SQLiteTraceStore

logger = logging.getLogger("vesta.application")

# 默认按需暴露的工具（不进入模型 schema，需 tool_search 搜索后激活）。
_DEFERRED_TOOL_NAMES = frozenset(
    {
        "http_request",
        "memory_list",
        "core_memory_update",
        "core_memory_remove",
    }
)

DEFAULT_SYSTEM_PROMPT = (
    "你是 Vesta，一个本地运行的智能助理。请使用用户的语言回答。"
    "调用工具时优先使用已有结果；网页搜索通常只需一到两次，获得可用结果后"
    "立即整理回答，不要为了追求完美而反复改写相同查询。"
    "当用户明确要求记录任务，或工作复杂、需要多个步骤或跨多轮跟踪时，"
    "调用 task_create；简单的一次性问题不要创建任务。完成任务步骤、计划"
    "变化或任务状态变化后调用 task_update，必要时用 task_get/task_list"
    "重新确认任务状态。"
    "如果生成了用户需要保留、下载或查看的文件（如报告、CSV、代码、图片），"
    "在最终回答前调用 artifact_publish 发布它；如果最终交付的是结果链接，"
    "也用 artifact_publish 发布。普通中间文件、临时文件、Trace、"
    "Computer Screenshot 不要发布为 Artifact；没有实际交付物时不要调用"
    "artifact_publish。"
)


def select_provider(
    settings: ModelSettings,
    requested: str | None,
) -> ModelProvider:
    """在已配置的 provider 中选择一个（显式指定 / 默认 / 唯一）。"""

    configured = settings.configured_providers()

    if requested is not None:
        provider = ModelProvider(requested)
        if provider not in configured:
            raise ValueError(
                f"Provider '{provider.value}' is not configured in backend/.env."
            )
        return provider

    if settings.model_default_provider in configured:
        return settings.model_default_provider
    if len(configured) == 1:
        return configured[0]
    if not configured:
        raise ValueError("No model provider is configured in backend/.env.")
    names = ", ".join(provider.value for provider in configured)
    raise ValueError(
        f"Multiple providers are configured ({names}); use --provider to select one."
    )


def title_from_content(content: str) -> str:
    """从第一条用户消息生成会话标题（CLI / Server 共用）。"""

    title = " ".join(content.split()).strip()
    return title[:40] or "新会话"


def _mark_deferred_tools(
    registry: ToolRegistry,
    names: frozenset[str],
) -> None:
    """把不常用工具标记为按需暴露（不进入模型 schema，可 tool_search 激活）。"""

    for name in names:
        tool = registry.unregister(name)
        registry.register(tool, deferred=True)


class Application:
    """统一创建并持有 Vesta 的全部运行依赖。"""

    def __init__(
        self,
        *,
        provider: ModelProvider | str | None = None,
        model: str | None = None,
        system_prompt: str | None = None,
        database: str | Path = DEFAULT_DATABASE_PATH,
        tasks_dir: str | Path = DEFAULT_TASKS_DIR,
        mcp_config: str | Path = DEFAULT_MCP_CONFIG_PATH,
        memory_dir: str | Path | None = None,
        skills_user_dir: str | Path | None = None,
        skills_project_dir: str | Path | None = None,
        max_steps: int = 10,
        max_tool_rounds: int = 15,
        max_output_tokens: int | None = None,
        run_budget_config: RunBudgetConfig | None = None,
        settings: ModelSettings | None = None,
        registry: ModelAdapterRegistry | None = None,
        shared_event_handler: AgentEventHandler | None = None,
        memory_reflection_config: MemoryReflectionConfig | None = None,
        memory_maintenance_config: MemoryMaintenanceConfig | None = None,
        skill_learning_settings: SkillLearningSettings | None = None,
        desktop_approval: bool = False,
        computer_runtime: ComputerRuntime | None = None,
        computer_host_status: ComputerHostStatus | None = None,
        workspace_root: str | Path | None = None,
    ) -> None:
        self.database = Path(database).expanduser().resolve()
        self.tasks_dir = Path(tasks_dir).expanduser().resolve()
        self.workspace_root = workspace_root_path(workspace_root)
        self.mcp_config = Path(mcp_config).expanduser().resolve()
        self.mcp_config_store = MCPConfigurationStore(self.mcp_config)
        self.memory_dir = (
            Path(memory_dir).expanduser().resolve() if memory_dir is not None else None
        )
        self.skills_user_dir = (
            Path(skills_user_dir).expanduser().resolve()
            if skills_user_dir is not None
            else None
        )
        self.skills_project_dir = (
            Path(skills_project_dir).expanduser().resolve()
            if skills_project_dir is not None
            else None
        )
        self.system_prompt = system_prompt
        self.max_steps = max_steps
        self.max_tool_rounds = max_tool_rounds
        self.max_output_tokens = max_output_tokens
        self._run_budget_config = run_budget_config
        self._memory_reflection_config = memory_reflection_config
        self._memory_maintenance_config = memory_maintenance_config
        self._skill_learning_settings = skill_learning_settings
        # True = DesktopApprovalGate（Host）；False = ConsoleApprovalGate（CLI）。
        self.desktop_approval = desktop_approval
        # Computer Runtime 由入口注入；None 时不注册 computer_* 工具。
        self._computer_runtime = computer_runtime
        # Computer Host 状态（bootstrap 产物；None = 未配置 Computer）。
        self.computer_host_status = computer_host_status
        self.settings = settings or ModelSettings()
        if registry is not None:
            # 测试注入的离线 registry：provider 直接取传入值（不校验 .env）。
            self.registry = registry
            self.provider: str = (
                provider.value if isinstance(provider, ModelProvider) else provider
            ) or "fake"
            self.model: str = model or "fake-model"
        else:
            self.registry = ModelAdapterRegistry(self.settings)
            selected = select_provider(
                self.settings,
                provider.value if isinstance(provider, ModelProvider) else provider,
            )
            self.provider = selected.value
            config = self.settings.provider_config(selected)
            self.model = model or config.model

        # 全局共享事件观察者（Server 在 start() 前注入 Desktop 广播 handler）。
        self.shared_event_handler = shared_event_handler

        # Post-Run 后台任务（Memory Reflection 等）由 Application 统一管理生命周期。
        self.post_run_processor = PostRunProcessor()

        # 在 start() 中构建的依赖。
        self.conversation_store: SQLiteConversationStore | None = None
        self.summary_store: SQLiteConversationSummaryStore | None = None
        self.trace_store: SQLiteTraceStore | None = None
        self.checkpoint_store: SQLiteCheckpointStore | None = None
        self.rule_store: SQLitePermissionRuleStore | None = None
        self.policy_engine: PermissionPolicyEngine | None = None
        self.approval_store: SQLiteApprovalStore | None = None
        self.approval_gate: Any | None = None
        self.desktop_approval_gate: DesktopApprovalGate | None = None
        self.artifact_store: SQLiteArtifactStore | None = None
        self.artifact_service: ArtifactService | None = None
        self.computer_runtime: ComputerRuntime | None = None
        self.computer_lease: ComputerLeaseManager | None = None
        self.computer_session: ComputerSessionManager | None = None
        self.tool_registry: ToolRegistry | None = None
        self.task_store: FileTaskStore | None = None
        self.memory_manager: MemoryManager | None = None
        self.skill_store: SkillStore | None = None
        self.skill_context_provider: SkillContextProvider | None = None
        self.skill_learning: SkillLearningService | None = None
        self.memory_reflector: PostRunMemoryReflector | None = None
        self.memory_maintenance_reflector: MemoryMaintenanceReflector | None = None
        self.memory_reflection_enabled = True
        self.memory_maintenance_enabled = True
        self.context_summarizer: ModelContextSummarizer | None = None
        self.mcp_manager: MCPClientManager | None = None
        self.mcp_statuses: tuple[Any, ...] = ()
        self.mcp_error: str | None = None
        self.runtime: AgentRuntime | None = None
        self.run_store: SQLiteRunStore | None = None
        self.run_manager: RunManager | None = None
        self.conversation_service: ConversationService | None = None
        self.automation_store: SQLiteAutomationStore | None = None
        self.automation_scheduler: AutomationScheduler | None = None
        self.reconciled_runs: tuple[Any, ...] = ()

        self._started = False

    # ------------------------------------------------------------------
    # 生命周期
    # ------------------------------------------------------------------

    async def start(self) -> None:
        """初始化全部 store，装配工具 / Runtime / Service / Scheduler。"""

        if self._started:
            return

        database = self.database
        conversation_store = SQLiteConversationStore(database)
        await conversation_store.initialize()
        summary_store = SQLiteConversationSummaryStore(database)
        await summary_store.initialize()
        trace_store = SQLiteTraceStore(database)
        await trace_store.initialize()
        checkpoint_store = SQLiteCheckpointStore(database)
        await checkpoint_store.initialize()
        rule_store = SQLitePermissionRuleStore(database)
        await rule_store.initialize()
        policy_engine = PermissionPolicyEngine(rule_store)

        approval_store = SQLiteApprovalStore(database)
        await approval_store.initialize()
        if self.desktop_approval:
            approval_gate: Any = DesktopApprovalGate(approval_store)
        else:
            approval_gate = ConsoleApprovalGate(
                rule_label_factory=describe_safe_rule,
            )

        tool_registry = build_builtin_tool_registry(self.workspace_root)

        artifact_store = SQLiteArtifactStore(database)
        await artifact_store.initialize()
        artifact_service = ArtifactService(
            artifact_store,
            self.workspace_root,
            managed_dir=database.parent / "artifacts",
        )
        register_artifact_tools(tool_registry, artifact_service)

        task_store = FileTaskStore(self.tasks_dir)
        await task_store.initialize()
        register_task_tools(tool_registry, task_store)

        memory_manager = MemoryManager(
            memory_dir=self.memory_dir or DEFAULT_MEMORY_DIR
        )
        await memory_manager.initialize()
        register_memory_tools(tool_registry, memory_manager)

        skill_store = SkillStore(
            user_dir=self.skills_user_dir or DEFAULT_USER_SKILLS_DIR,
            project_dir=self.skills_project_dir or DEFAULT_PROJECT_SKILLS_DIR,
        )
        await skill_store.initialize()
        register_skill_tools(tool_registry, skill_store)
        skill_settings = SkillSettings()
        skill_context_provider = SkillContextProvider(
            max_tokens=skill_settings.skill_context_max_tokens,
            max_active=skill_settings.skill_max_active,
            catalog_max_tokens=skill_settings.skill_catalog_max_tokens,
        )

        skill_learning_settings = (
            self._skill_learning_settings or SkillLearningSettings()
        )
        skill_candidate_store = SkillCandidateStore(
            skill_learning_settings.skill_learning_data_dir
        )
        await skill_candidate_store.initialize()
        skill_learning = SkillLearningService(
            task_store=task_store,
            trace_store=trace_store,
            skill_store=skill_store,
            candidate_store=skill_candidate_store,
            registry=self.registry,
            settings=skill_learning_settings,
            default_provider=self.provider,
            default_model=self.model,
        )

        # Computer Runtime 可注入真实 macOS 实现或测试 Fake；未注入则不注册，
        # 普通 CLI / Host 现有功能完全不受影响。
        computer_lease: ComputerLeaseManager | None = None
        computer_session: ComputerSessionManager | None = None
        computer_hooks = ()
        if self._computer_runtime is not None:
            computer_session = ComputerSessionManager()
            set_session_manager = getattr(
                self._computer_runtime, "set_session_manager", None
            )
            if callable(set_session_manager):
                set_session_manager(computer_session)
            begin_session = getattr(
                self._computer_runtime, "begin_session_rpc", None
            )
            computer_lease = ComputerLeaseManager(
                database.parent / "computer" / "machine.lock"
            )
            computer_hooks = (
                ComputerLeaseHook(
                    computer_lease,
                    computer_session,
                    session_starter=(
                        begin_session if callable(begin_session) else None
                    ),
                ),
            )
            register_computer_tools(tool_registry, self._computer_runtime)
            self.computer_runtime = self._computer_runtime
            # 真实 MacOSComputerRuntime 才有显式 start / close；
            # FakeComputerRuntime 没有 start，用 getattr 探测，不影响现有路径。
            start_runtime = getattr(self._computer_runtime, "start", None)
            if callable(start_runtime):
                await start_runtime()

        _mark_deferred_tools(tool_registry, _DEFERRED_TOOL_NAMES)

        reflection_config = self._memory_reflection_config or MemoryReflectionConfig()
        memory_reflector = PostRunMemoryReflector(
            self.registry,
            config=reflection_config,
            default_provider=self.provider,
            default_model=self.model,
        )
        maintenance_config = (
            self._memory_maintenance_config or MemoryMaintenanceConfig()
        )
        memory_maintenance_reflector = MemoryMaintenanceReflector(
            self.registry,
            config=maintenance_config,
            default_provider=memory_reflector.provider_hint or self.provider,
            default_model=memory_reflector.model_hint or self.model,
        )

        context_settings = ContextSettings()
        context_summarizer = ModelContextSummarizer(
            self.registry,
            provider=self.provider,
            model=self.model,
            max_output_tokens=context_settings.context_summary_max_output_tokens,
        )

        # MCP：配置缺失 / 损坏时不阻断启动（CLI 会检查 mcp_error 决定退出码）。
        mcp_manager: MCPClientManager | None = None
        mcp_statuses: tuple[Any, ...] = ()
        mcp_error: str | None = None
        try:
            mcp_settings = await self.mcp_config_store.load()
            mcp_manager = MCPClientManager(mcp_settings.servers)
            mcp_statuses = await mcp_manager.start(tool_registry)
        except MCPConfigurationError as exc:
            mcp_error = f"{type(exc).__name__}: {exc}"
            logger.warning("MCP disabled: %s", mcp_error)
        # 管理查询只读取当前 Manager 快照，不启动 Server，也不暴露全部 MCP Schema。
        tool_registry.register(
            MCPStatusTool(mcp_manager, configuration_error=mcp_error)
        )

        runtime = AgentRuntime(
            self.registry,
            tool_registry,
            provider=self.provider,
            model=self.model,
            max_steps=self.max_steps,
            max_tool_rounds=self.max_tool_rounds,
            max_output_tokens=self.max_output_tokens,
            approval_gate=approval_gate,
            policy_engine=policy_engine,
            rule_store=rule_store,
            context_manager=ContextManager(
                context_settings=context_settings,
                conversation_reducer=ConversationReducer(
                    context_summarizer,
                    keep_recent_conversation_blocks=(
                        context_settings.context_keep_recent_conversation_blocks
                    ),
                    keep_recent_tool_rounds=(
                        context_settings.context_keep_recent_tool_rounds
                    ),
                ),
            ),
            task_context_provider=TaskContextProvider(task_store),
            checkpoint_store=checkpoint_store,
            memory_manager=memory_manager,
            memory_reflector=memory_reflector,
            memory_maintenance_reflector=memory_maintenance_reflector,
            skill_store=skill_store,
            skill_context_provider=skill_context_provider,
            tool_hooks=computer_hooks,
            post_run_submit=self.post_run_processor.submit,
            run_budget_config=self._run_budget_config,
        )

        run_store = SQLiteRunStore(database)
        # Run 终态 finalizer 顺序：先 end ComputerSession（清 target/snapshot + 通知
        # helper 清 Native 状态），再 release Machine Lease。
        computer_finalizers: list[object] = []
        if computer_session is not None:
            end_session = getattr(self._computer_runtime, "end_session", None)
            if callable(end_session):
                computer_finalizers.append(end_session)
        if computer_lease is not None:
            computer_finalizers.append(computer_lease.release)
        run_manager = RunManager(
            run_store,
            checkpoint_store,
            runtime,
            approval_store=approval_store,
            run_finalizers=tuple(computer_finalizers),
        )
        # 启动 reconciliation（Run + Checkpoint 统一处理）。
        reconciled_runs = await run_manager.initialize()
        # 孤儿审批：Host 启动后，没有对应活跃 Run（非终态）的 PENDING approval
        # 统一置为 CANCELLED（无人等待，也不应再显示为 pending）。
        active_run_ids = {
            run.id
            for run in (
                await run_store.list_runs(status=RunStatus.PENDING)
            )
            + (
                await run_store.list_runs(status=RunStatus.RUNNING)
            )
        }
        await approval_store.reconcile_orphans(active_run_ids)

        conversation_service = ConversationService(
            conversation_store,
            run_manager,
            trace_store,
            summary_store=summary_store,
            shared_event_handler=self.shared_event_handler,
        )

        automation_store = SQLiteAutomationStore(database)
        automation_scheduler = AutomationScheduler(
            automation_store,
            conversation_service,
        )
        register_automation_tools(tool_registry, automation_scheduler)
        await automation_scheduler.start()

        # 挂到 self，供 CLI / Server 读取。
        self.conversation_store = conversation_store
        self.summary_store = summary_store
        self.trace_store = trace_store
        self.checkpoint_store = checkpoint_store
        self.rule_store = rule_store
        self.policy_engine = policy_engine
        self.approval_store = approval_store
        self.approval_gate = approval_gate
        self.desktop_approval_gate = (
            approval_gate if isinstance(approval_gate, DesktopApprovalGate) else None
        )
        self.artifact_store = artifact_store
        self.artifact_service = artifact_service
        self.tool_registry = tool_registry
        self.task_store = task_store
        self.memory_manager = memory_manager
        self.skill_store = skill_store
        self.skill_context_provider = skill_context_provider
        self.skill_learning = skill_learning
        self.memory_reflector = memory_reflector
        self.memory_maintenance_reflector = memory_maintenance_reflector
        self.memory_reflection_enabled = reflection_config.enabled
        self.memory_maintenance_enabled = maintenance_config.enabled
        self.context_summarizer = context_summarizer
        self.mcp_manager = mcp_manager
        self.mcp_statuses = mcp_statuses
        self.mcp_error = mcp_error
        self.computer_lease = computer_lease
        self.computer_session = computer_session
        self.runtime = runtime
        self.run_store = run_store
        self.run_manager = run_manager
        self.conversation_service = conversation_service
        self.automation_store = automation_store
        self.automation_scheduler = automation_scheduler
        self.reconciled_runs = reconciled_runs

        self._started = True

    async def close(self) -> None:
        """优雅关闭 Post-Run / Scheduler / MCP / Computer / 模型适配器（幂等）。"""

        if not self._started:
            return
        # 先 drain post-run 后台任务（可能仍在用模型 registry / memory store），
        # 避免 event loop 关闭时遗留 pending task；有界等待，超时后 cancel。
        await self.post_run_processor.close()
        if self.automation_scheduler is not None:
            await self.automation_scheduler.shutdown()
        if self.mcp_manager is not None and self.tool_registry is not None:
            await self.mcp_manager.close(self.tool_registry)
        if self.computer_lease is not None:
            self.computer_lease.close()
        if self.computer_session is not None:
            self.computer_session.close()
        if self.computer_runtime is not None:
            # 只在确实注入真实 MacOSComputerRuntime（有 close）时关闭 helper。
            close_runtime = getattr(self.computer_runtime, "close", None)
            if callable(close_runtime):
                await close_runtime()
        await self.registry.close()
        self._started = False
