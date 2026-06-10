"""
app/ledger/exceptions.py
========================
Excepciones específicas del dominio Ledger.
Reutiliza la jerarquía transversal de app.core.errors.
"""

from app.core.errors import ConflictError


class DuplicateCommandError(ConflictError):
    """
    Se lanza cuando se intenta registrar un evento financiero con un command_id
    que ya existe en la base de datos (reintento idempotente).
    El repository lo atrapará y retornará el evento original.
    """

    error_code = "duplicate_command"
    message = "El comando financiero ya fue procesado."
