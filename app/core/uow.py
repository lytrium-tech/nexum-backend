"""
app/core/uow.py
===============
Patrón Unit of Work (UoW) genérico para Nexum.

Provee un contexto transaccional asíncrono para agrupar múltiples operaciones
de base de datos (por ejemplo, escribir en Ledger y actualizar balances)
garantizando atomicidad (commit / rollback).

No contiene lógica de dominio, únicamente abstracción sobre AsyncSession.
"""

from collections.abc import AsyncGenerator
from contextlib import asynccontextmanager

from sqlalchemy.ext.asyncio import AsyncSession


class UnitOfWork:
    """
    Gestiona una transacción de base de datos.
    Delega a SQLAlchemy el manejo real del contexto.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    @asynccontextmanager
    async def transaction(self) -> AsyncGenerator["UnitOfWork"]:
        """
        Context manager asíncrono para ejecutar operaciones dentro de una transacción.
        Si no hay excepciones, hace commit automáticamente.
        Si ocurre una excepción, hace rollback y la relanza.
        """
        try:
            yield self
            await self.session.commit()
        except Exception:
            await self.session.rollback()
            raise
