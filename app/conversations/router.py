import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Header
from sqlalchemy.ext.asyncio import AsyncSession

from app.cash.router import get_cash_service
from app.cash.service import CashService
from app.conversations.schemas import ConversationalRequest, ConversationalResponse
from app.conversations.service import ConversationsService
from app.core.database import get_db_session
from app.credit.router import get_credit_service
from app.credit.service import CreditCardService
from app.goals.router import get_goal_service
from app.goals.service import GoalService
from app.intelligence.router import get_intelligence_service
from app.intelligence.service import IntelligenceService
from app.obligations.router import get_obligation_service
from app.obligations.service import ObligationService
from app.transfers.router import get_transfers_service
from app.transfers.service import TransfersService
from app.users.dependencies import CurrentUserProfile

router = APIRouter(prefix="/conversations", tags=["Conversational"])


def get_conversations_service(
    session: AsyncSession = Depends(get_db_session),
    intel_service: IntelligenceService = Depends(get_intelligence_service),
    cash_service: CashService = Depends(get_cash_service),
    goals_service: GoalService = Depends(get_goal_service),
    obl_service: ObligationService = Depends(get_obligation_service),
    credit_service: CreditCardService = Depends(get_credit_service),
    transfers_service: TransfersService = Depends(get_transfers_service),
) -> ConversationsService:
    return ConversationsService(
        session=session,
        intel_service=intel_service,
        cash_service=cash_service,
        goals_service=goals_service,
        obl_service=obl_service,
        credit_service=credit_service,
        transfers_service=transfers_service,
    )

@router.post("/message", response_model=ConversationalResponse)
async def process_message(
    request: ConversationalRequest,
    current_profile: CurrentUserProfile,
    service: Annotated[ConversationsService, Depends(get_conversations_service)],
    db: Annotated[AsyncSession, Depends(get_db_session)],
    trace_id: Annotated[str | None, Header(alias="X-Trace-Id")] = None,
):
    """
    Endpoint conversacional unificado para MVP.
    """
    eff_trace_id = uuid.UUID(trace_id) if trace_id else uuid.uuid4()
    
    response = await service.handle_message(
        user_id=current_profile.id,
        request=request,
        trace_id=eff_trace_id
    )
    
    # Commit the transaction to save messages and pending actions
    await db.commit()
    
    return response
