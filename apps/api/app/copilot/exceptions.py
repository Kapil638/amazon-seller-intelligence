"""Controlled errors for the intelligence tool layer. Not HTTP exceptions."""


class CopilotToolError(Exception):
    """Base error for tool registry and budget policy."""


class UnknownToolError(CopilotToolError):
    def __init__(self, name: str) -> None:
        self.name = name
        super().__init__(f"Unknown intelligence tool: {name}")


class ToolValidationError(CopilotToolError):
    def __init__(self, name: str, message: str) -> None:
        self.name = name
        super().__init__(f"Invalid input for {name}: {message}")


class BudgetRequiredError(CopilotToolError):
    def __init__(
        self,
        message: str = "Tool execution requires a BudgetTracker. Unlimited execution is not allowed.",
    ) -> None:
        super().__init__(message)


class BudgetExceededError(CopilotToolError):
    def __init__(self, message: str = "The tool execution budget for this turn has been reached.") -> None:
        super().__init__(message)


class ConfirmationRequiredError(CopilotToolError):
    def __init__(self, message: str, *, cost_kind: str) -> None:
        self.cost_kind = cost_kind
        super().__init__(message)
