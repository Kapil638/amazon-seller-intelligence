from app.copilot.registry import ToolRegistry


def register_all(registry: ToolRegistry) -> None:
    from app.copilot.tools import history, listing, product

    history.register(registry)
    listing.register(registry)
    product.register(registry)
