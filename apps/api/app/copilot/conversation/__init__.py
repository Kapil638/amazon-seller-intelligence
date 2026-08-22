"""Conversation foundation. Does not plan, execute tools, or call AI."""

from app.copilot.conversation.context import ContextBuilder
from app.copilot.conversation.service import ConversationService, get_conversation_service

__all__ = ["ContextBuilder", "ConversationService", "get_conversation_service"]
