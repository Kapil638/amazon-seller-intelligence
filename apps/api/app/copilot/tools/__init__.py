from app.copilot.registry import ToolRegistry


def register_all(registry: ToolRegistry) -> None:
    from app.copilot.tools import advertising, history, listing, product, profit

    history.register(registry)
    listing.register(registry)
    product.register(registry)
    profit.register(registry)
    advertising.register(registry)
