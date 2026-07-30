"""Errors raised by the model adapter layer."""


class ModelAdapterError(RuntimeError):
    """Base error for provider configuration and invocation failures."""


class ProviderNotConfiguredError(ModelAdapterError):
    def __init__(self, provider: str, environment_variable: str) -> None:
        super().__init__(
            f"Provider '{provider}' is not configured. "
            f"Set {environment_variable} in the environment."
        )


class UnsupportedProviderError(ModelAdapterError):
    def __init__(self, provider: str) -> None:
        super().__init__(f"No model adapter is registered for provider '{provider}'.")


class UnsupportedMessageError(ModelAdapterError):
    """Raised when a message cannot be represented by a provider API."""
