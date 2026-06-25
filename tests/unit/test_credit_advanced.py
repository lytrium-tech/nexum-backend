import uuid
from decimal import Decimal
import pytest
from unittest.mock import AsyncMock

from app.credit.service import CreditCardService
from app.credit.models import CreditCard, CreditCardTransaction
from app.credit.exceptions import InvalidPaymentAmountError

pytestmark = pytest.mark.asyncio

async def test_early_payment_success():
    pass

async def test_early_payment_insufficient_funds():
    pass

async def test_early_payment_exceeds_principal():
    pass

async def test_waterfall_allocation():
    service = CreditCardService(AsyncMock())
    card = CreditCard(id=uuid.uuid4(), cutoff_day=15, due_day=30)
    user_id = uuid.uuid4()
    
    # Mocks
    past_due_inst = AsyncMock(scheduled_period="2020-01", total_amount=Decimal("100"), paid_amount=Decimal("0"), interest_amount=Decimal("0"))
    current_inst = AsyncMock(scheduled_period="2099-01", total_amount=Decimal("100"), paid_amount=Decimal("0"), interest_amount=Decimal("20"), installments_total=3)
    current_revol = AsyncMock(scheduled_period="2099-01", total_amount=Decimal("50"), paid_amount=Decimal("0"), interest_amount=Decimal("0"), installments_total=1)
    future_revol = AsyncMock(scheduled_period="2100-01", total_amount=Decimal("30"), paid_amount=Decimal("0"), interest_amount=Decimal("0"), installments_total=1)
    future_inst = AsyncMock(scheduled_period="2100-01", total_amount=Decimal("200"), paid_amount=Decimal("0"), interest_amount=Decimal("0"), installments_total=3)
    
    service.repo = AsyncMock()
    service.repo.list_pending_installments_for_update.return_value = [
        past_due_inst, current_inst, current_revol, future_revol, future_inst
    ]
    
    fee_charge = AsyncMock(amount=Decimal("10"), paid_amount=Decimal("0"))
    service.repo.list_unpaid_statement_charges_for_update.return_value = [fee_charge]
    
    from datetime import date
    import app.credit.utils
    original_calc = app.credit.utils.calculate_credit_card_dates
    
    try:
        # Override to make current_period = '2099-01'
        def fake_calc(d, c, due):
            return date(2099, 1, 1), date(2099, 1, 15), date(2099, 1, 30)
        app.credit.utils.calculate_credit_card_dates = fake_calc
        
        class FakeDate:
            @classmethod
            def today(cls):
                return date(2099, 1, 10)
        
        import app.credit.service
        app.credit.service.date = FakeDate
        
        # Payment amount: 100 (past due) + 20 (interest) + 10 (fees) + 80 (billed inst principal) + 50 (billed revol) + 30 (unbilled revol) = 290
        # If we pay 200:
        # - 100 past due -> remaining 100
        # - 20 current interest -> remaining 80
        # - 10 fee -> remaining 70
        # - 70 billed inst principal -> remaining 0.
        
        await service._apply_payment_to_waterfall(user_id, card, Decimal("200"))
        
        assert past_due_inst.paid_amount == Decimal("100")
        assert current_inst.paid_amount == Decimal("90") # 20 interest + 70 principal
        assert fee_charge.paid_amount == Decimal("10")
        assert current_revol.paid_amount == Decimal("0")
        assert future_revol.paid_amount == Decimal("0")
        assert future_inst.paid_amount == Decimal("0") # Never touches future unbilled installments
        
        # Overpayment testing
        past_due_inst.paid_amount = Decimal("0")
        current_inst.paid_amount = Decimal("0")
        fee_charge.paid_amount = Decimal("0")
        
        with pytest.raises(InvalidPaymentAmountError):
            await service._apply_payment_to_waterfall(user_id, card, Decimal("300"))
            
    finally:
        app.credit.utils.calculate_credit_card_dates = original_calc
        import datetime
        app.credit.service.date = datetime.date
