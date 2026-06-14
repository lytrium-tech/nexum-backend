import asyncio
import uuid
import sys
from decimal import Decimal
from datetime import datetime, timezone, timedelta

from sqlalchemy import select

from app.core.database import init_engine, _session_factory
from app.core.database import init_engine, get_engine
from app.users.models import User
from app.credit.schemas import CreditCardCreate, CreditCardPurchaseCreate, CreditCardPaymentCreate
from app.credit.service import CreditCardService
from app.credit.models import CreditCardTransaction
from app.accounts.schemas import AccountCreate
from app.accounts.service import AccountService
from app.accounts.repository import AccountRepository
from app.categories.models import Category
from app.ledger.models import FinancialEvent
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

async def main():
    import app.core.config
    await init_engine()
    async_session = async_sessionmaker(get_engine(), expire_on_commit=False, class_=AsyncSession)
    async with async_session() as session:
        email = f"test_credit_core_{uuid.uuid4().hex[:6]}@example.com"
        user = User(email=email)
        session.add(user)
        await session.commit()
        await session.refresh(user)
        
        account_service = AccountService(AccountRepository(session))
        acc = await account_service.create_account(
            user.id,
            AccountCreate(name="Bancolombia", type="bank", currency="COP", balance=Decimal("1000000.00"))
        )
        from app.accounts.models import Account
        acc_db = await session.get(Account, acc.id)
        acc_db.balance = Decimal("1000000.00")
        await session.commit()
        
        credit_service = CreditCardService(session)
        card = await credit_service.create_card(
            user.id,
            CreditCardCreate(name="Nu", bank="Nu", credit_limit=Decimal("500000.00"), cutoff_day=15, due_day=30, currency="COP")
        )
        await session.commit()
        
        print(f"Creada TC: {card.id}")
        
        # Test 1: Compras
        cmd1 = uuid.uuid4()
        res1 = await credit_service.create_purchase(
            user.id,
            card.id,
            CreditCardPurchaseCreate(amount=Decimal("150000"), installments_total=1, description="Zapatos"),
            cmd1
        )
        await session.commit()
        
        cmd2 = uuid.uuid4()
        res2 = await credit_service.create_purchase(
            user.id,
            card.id,
            CreditCardPurchaseCreate(amount=Decimal("50000"), installments_total=1, description="Cena"),
            cmd2
        )
        await session.commit()
        
        print(f"Compras hechas: {res1.amount}, {res2.amount}")
        
        txs = (await session.execute(select(CreditCardTransaction).where(CreditCardTransaction.credit_card_id == card.id))).scalars().all()
        
        # Set one in the past to make it billed
        past_date = datetime.now(timezone.utc) - timedelta(days=40)
        txs[0].occurred_at = past_date
        await session.commit()
        
        status_updated = await credit_service.get_card_status(user.id, card.id)
        
        print(f"Deuda Total: {status_updated.total_debt}")
        print(f"Facturado: {status_updated.billed_debt}")
        print(f"Sin Facturar: {status_updated.unbilled_debt}")
        
        # Assert logic
        assert status_updated.total_debt == Decimal("200000.00")
        assert status_updated.billed_debt == Decimal("150000.00")
        assert status_updated.unbilled_debt == Decimal("50000.00")
        
        # Test 2: Pago
        cmd3 = uuid.uuid4()
        await credit_service.create_payment(
            user.id,
            card.id,
            CreditCardPaymentCreate(amount=Decimal("150000"), account_id=acc.id),
            cmd3
        )
        await session.commit()
        
        status_final = await credit_service.get_card_status(user.id, card.id)
        print(f"Final Total: {status_final.total_debt}")
        print(f"Final Facturado: {status_final.billed_debt}")
        print(f"Final Sin Facturar: {status_final.unbilled_debt}")
        
        assert status_final.total_debt == Decimal("50000.00")
        assert status_final.billed_debt == Decimal("0.00")
        assert status_final.unbilled_debt == Decimal("50000.00")

        # Pago over limit
        cmd4 = uuid.uuid4()
        from app.credit.exceptions import InvalidPaymentAmountError
        try:
            await credit_service.create_payment(
                user.id,
                card.id,
                CreditCardPaymentCreate(amount=Decimal("60000"), account_id=acc.id),
                cmd4
            )
            await session.commit()
            assert False, "Should have raised InvalidPaymentAmountError"
        except InvalidPaymentAmountError:
            await session.rollback()
            print("Overpayment blocked properly!")

        print("Test passed! ✅")

if __name__ == "__main__":
    asyncio.run(main())
