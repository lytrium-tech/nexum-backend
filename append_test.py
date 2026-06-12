with open("tests/unit/test_conversations.py", "a", encoding="utf-8") as f:
    f.write("""

@pytest.mark.asyncio
async def test_pending_action_confirm_unsupported_intent(conversations_service, mock_repo):
    user_id = uuid.uuid4()
    trace_id = uuid.uuid4()
    
    action_id = uuid.uuid4()
    command_id = uuid.uuid4()
    req = ConversationalRequest(message="sí", channel="api", pending_action_id=str(action_id))
    
    from datetime import datetime
    from app.conversations.schemas import PendingActionRead
    action = PendingActionRead(
        id=action_id, user_id=user_id, intent="create_goal",
        data={"amount": 500},
        missing_fields=None, status="awaiting_confirmation", command_id=command_id,
        created_at=datetime.now(UTC), updated_at=datetime.now(UTC), expires_at=datetime.now(UTC)
    )
    
    mock_repo.get_pending_action.return_value = action
    mock_repo.save_message.return_value = uuid.uuid4()
    
    resp = await conversations_service.handle_message(user_id, req, trace_id)
    assert resp.status == "error"
    assert "No se registró ningún movimiento" in resp.response_text
    
    mock_repo.update_pending_action_status.assert_not_called()
""")
