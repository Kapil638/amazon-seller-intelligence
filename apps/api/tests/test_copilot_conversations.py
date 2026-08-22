from __future__ import annotations

import ast
from pathlib import Path
from uuid import uuid4

from fastapi.testclient import TestClient

from app.copilot.conversation.context import MAX_USER_SNIPPETS, SNIPPET_MAX_CHARS, ContextBuilder
from app.copilot.conversation.service import ConversationService
from app.core.exceptions import ConversationNotFoundError
from app.models.copilot_conversation import CopilotConversationCreate
from app.persistence.database import current_organization_id, session_scope
from app.persistence.models import CopilotConversation, CopilotPendingConfirmation, Organization

CONVERSATIONS_URL = "/api/v1/copilot/conversations"
FORBIDDEN_IMPORTS = {
    "openai",
    "app.services.product_service",
    "app.providers.rainforest",
    "app.copilot.registry",
    "app.copilot.budget",
    "app.copilot.tools",
}


def _create(client: TestClient, **payload) -> dict:
    response = client.post(CONVERSATIONS_URL, json=payload)
    assert response.status_code == 201, response.text
    return response.json()


def test_create_conversation(client: TestClient) -> None:
    body = _create(client)
    assert body["organization_id"] == str(current_organization_id())
    assert body["status"] == "active"
    assert body["messages"] == []
    assert body["pending_confirmation"] is None
    assert body["compact_context"]["recent_user_snippets"] == []
    assert body["compact_context"]["evidence_refs"] == []
    assert body["last_asin"] is None


def test_create_ignores_organization_id_in_payload(client: TestClient) -> None:
    other = str(uuid4())
    body = _create(client, organization_id=other, title="Seller chat")
    assert body["organization_id"] == str(current_organization_id())
    assert body["organization_id"] != other
    assert body["title"] == "Seller chat"


def test_list_and_get_conversation(client: TestClient) -> None:
    created = _create(client, title="Why is my score low?")
    listed = client.get(CONVERSATIONS_URL)
    assert listed.status_code == 200
    payload = listed.json()
    assert payload["total"] >= 1
    assert any(item["id"] == created["id"] for item in payload["items"])

    fetched = client.get(f"{CONVERSATIONS_URL}/{created['id']}")
    assert fetched.status_code == 200
    assert fetched.json()["id"] == created["id"]
    assert fetched.json()["title"] == "Why is my score low?"


def test_unknown_conversation_is_404(client: TestClient) -> None:
    response = client.get(f"{CONVERSATIONS_URL}/{uuid4()}")
    assert response.status_code == 404


def test_organization_isolation(client: TestClient) -> None:
    visible = _create(client, title="Mine")
    other_org = uuid4()
    hidden_id = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        session.add(
            CopilotConversation(
                id=hidden_id,
                organization_id=other_org,
                status="active",
                title="Hidden Other Org",
            )
        )

    listed = client.get(CONVERSATIONS_URL)
    ids = [item["id"] for item in listed.json()["items"]]
    assert visible["id"] in ids
    assert str(hidden_id) not in ids
    assert client.get(f"{CONVERSATIONS_URL}/{hidden_id}").status_code == 404


def test_message_persistence_and_title_from_first_user_line() -> None:
    service = ConversationService()
    created = service.create_conversation(CopilotConversationCreate())
    updated = service.add_message(created.id, role="user", content="Why is my listing score low?")
    assert updated.title == "Why is my listing score low?"
    assert len(updated.messages) == 1
    assert updated.messages[0].role == "user"
    assert updated.messages[0].content == "Why is my listing score low?"

    fetched = service.get_conversation(created.id)
    assert len(fetched.messages) == 1
    assert fetched.messages[0].content == "Why is my listing score low?"


def test_add_message_other_org_is_not_found() -> None:
    other_org = uuid4()
    hidden_id = uuid4()
    with session_scope() as session:
        session.add(Organization(id=other_org, name="Other"))
        session.add(
            CopilotConversation(
                id=hidden_id,
                organization_id=other_org,
                status="active",
                title="Hidden",
            )
        )
    service = ConversationService()
    try:
        service.add_message(hidden_id, role="user", content="hello")
        raise AssertionError("expected ConversationNotFoundError")
    except ConversationNotFoundError:
        pass


def test_context_builder_truncates_and_does_not_dump_raw_history() -> None:
    service = ConversationService()
    created = service.create_conversation()
    long_first = "A" * (SNIPPET_MAX_CHARS + 80)
    long_second = "B" * (SNIPPET_MAX_CHARS + 40)
    long_third = "C" * 120
    service.add_message(created.id, role="user", content=long_first)
    service.add_message(created.id, role="assistant", content="I loaded your saved analysis.")
    service.add_message(created.id, role="user", content=long_second)
    service.add_message(created.id, role="user", content=long_third)

    detail = service.get_conversation(created.id)
    assert len(detail.messages) == 4
    assert detail.messages[0].content == long_first

    compact = detail.compact_context
    assert len(compact.recent_user_snippets) == MAX_USER_SNIPPETS
    assert compact.recent_user_snippets[0] == "B" * SNIPPET_MAX_CHARS
    assert compact.recent_user_snippets[1] == long_third
    assert long_first not in compact.recent_user_snippets
    assert all(len(snippet) <= SNIPPET_MAX_CHARS for snippet in compact.recent_user_snippets)
    dumped = compact.model_dump()
    assert long_first not in str(dumped)

    planner = service.planner_context(created.id)
    synthesis = service.synthesis_context(created.id)
    assert "nonce" not in planner.model_dump()
    assert planner.pending_confirmation is None
    assert synthesis.evidence_refs == []
    assert "catalog" not in planner.model_dump()
    assert "allowed_facts" not in synthesis.model_dump()


def test_context_builder_never_exposes_pending_nonce() -> None:
    service = ConversationService()
    created = service.create_conversation()
    secret = f"nonce-secret-{uuid4().hex}"
    plan_id = uuid4()
    with session_scope() as session:
        session.add(
            CopilotPendingConfirmation(
                conversation_id=created.id,
                organization_id=current_organization_id(),
                nonce=secret,
                plan_id=plan_id,
                plan_schema_version="copilot-plan-v1",
                plan_hash="abc123",
                summary="Looking this up uses product credits.",
            )
        )

    compact = service.get_compact_context(created.id)
    assert compact.pending_confirmation is not None
    assert compact.pending_confirmation.nonce_present is True
    assert compact.pending_confirmation.plan_id == plan_id
    assert compact.pending_confirmation.summary == "Looking this up uses product credits."
    dumped = compact.model_dump()
    assert secret not in str(dumped)
    assert "nonce-secret" not in str(dumped)
    planner = ContextBuilder().for_planner(compact)
    assert planner.pending_confirmation is not None
    assert "nonce" not in planner.model_dump()["pending_confirmation"]
    synthesis = ContextBuilder().for_synthesis(compact)
    assert synthesis.model_dump().get("pending_confirmation") is None


def test_slots_appear_in_compact_context_not_as_full_history() -> None:
    service = ConversationService()
    created = service.create_conversation()
    report_id = uuid4()
    service.update_slots(
        created.id,
        last_asin="B0TEST0001",
        last_report_id=report_id,
        previous_intent="explain_listing_score",
    )
    compact = service.get_compact_context(created.id)
    assert compact.last_asin == "B0TEST0001"
    assert compact.last_report_id == report_id
    assert compact.previous_intent == "explain_listing_score"


def test_conversation_modules_do_not_import_forbidden_layers() -> None:
    roots = [
        Path(__file__).resolve().parents[1] / "app" / "copilot" / "conversation",
        Path(__file__).resolve().parents[1] / "app" / "api" / "routes" / "copilot.py",
    ]
    files: list[Path] = []
    for root in roots:
        if root.is_dir():
            files.extend(root.glob("*.py"))
        else:
            files.append(root)
    imported: set[str] = set()
    for path in files:
        tree = ast.parse(path.read_text())
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                imported.update(alias.name for alias in node.names)
            elif isinstance(node, ast.ImportFrom) and node.module:
                imported.add(node.module)
    assert "openai" not in imported
    for name in FORBIDDEN_IMPORTS:
        assert name not in imported, f"forbidden import {name}"
