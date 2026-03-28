class CustomsAutomationError(Exception):
    """Base exception for the project."""


class ConfigError(CustomsAutomationError):
    """Raised when workflow configuration is invalid."""


class RulePackError(CustomsAutomationError):
    """Raised when rule-pack discovery or validation fails."""


class ArtifactError(CustomsAutomationError):
    """Raised when required run artifacts cannot be created or persisted."""
