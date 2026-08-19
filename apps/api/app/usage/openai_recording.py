from app.usage.ledger import AIWorkflow, get_usage_ledger


def record_openai_generation(workflow: AIWorkflow, result: object) -> None:
    usage = getattr(result, "usage", None)
    model = str(getattr(result, "model", "") or "")
    get_usage_ledger().record_openai_call(
        workflow=workflow,
        model=model,
        input_tokens=getattr(usage, "input_tokens", None) if usage is not None else None,
        cached_input_tokens=getattr(usage, "cached_input_tokens", None) if usage is not None else None,
        output_tokens=getattr(usage, "output_tokens", None) if usage is not None else None,
        total_tokens=getattr(usage, "total_tokens", None) if usage is not None else None,
    )
