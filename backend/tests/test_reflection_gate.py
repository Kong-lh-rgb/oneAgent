"""Reflection Gate：只跳过确定性无长期价值场景。"""

from app.memory import ReflectionGateReason, decide_reflection_gate


def test_skips_exact_smalltalk_and_ephemeral_queries() -> None:
    greeting = decide_reflection_gate("你好！")
    weather = decide_reflection_gate("搜索一下明天的天气")
    capability = decide_reflection_gate("你目前有什么 m c p？")
    informal_capability = decide_reflection_gate("你现有的mcp工具我看看")

    assert greeting.should_reflect is False
    assert greeting.reason is ReflectionGateReason.SMALLTALK
    assert weather.reason is ReflectionGateReason.EPHEMERAL_LOOKUP
    assert capability.reason is ReflectionGateReason.CAPABILITY_QUERY
    assert informal_capability.should_reflect is False
    assert informal_capability.reason is ReflectionGateReason.CAPABILITY_QUERY


def test_durable_or_uncertain_signal_keeps_model_authority() -> None:
    durable = decide_reflection_gate("以后默认使用中文注释")
    uncertain = decide_reflection_gate("我们讨论一下新的架构")
    recalled = decide_reflection_gate("好的", recalled_memory_ids=("M001",))

    assert durable.should_reflect is True
    assert durable.reason is ReflectionGateReason.DURABLE_SIGNAL
    assert uncertain.should_reflect is True
    assert uncertain.reason is ReflectionGateReason.UNCERTAIN
    assert recalled.should_reflect is True
    assert recalled.reason is ReflectionGateReason.RECALLED_MEMORY
