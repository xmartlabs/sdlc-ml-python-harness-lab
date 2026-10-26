import asyncio
from typing import Dict, List


class SaldoInsuficienteError(Exception):
    """Excepción lanzada cuando el saldo del emisor es insuficiente."""
    pass


class CuentaNoActivaError(Exception):
    """Excepción lanzada cuando una cuenta no está en estado ACTIVA."""
    pass


class WalletSystem:
    """Sistema de billetera digital en memoria."""

    def __init__(self):
        """Inicializa el sistema de billetera."""
        self.users: Dict[str, Dict] = {}

    def create_user(self, user_id: str, name: str, balance: float = 0.0, status: str = 'ACTIVA'):
        """Crea un usuario en el sistema."""
        if user_id in self.users:
            raise ValueError(f"El usuario {user_id} ya existe")
        self.users[user_id] = {
            'name': name,
            'balance': balance,
            'status': status
        }

    def transfer(self, from_id: str, to_id: str, amount: float):
        """Realiza una transferencia entre usuarios con validaciones y comisión opcional."""
        if amount <= 0:
            raise ValueError("El monto debe ser mayor a 0")

        if from_id not in self.users:
            raise ValueError(f"El usuario {from_id} no existe")
        if to_id not in self.users:
            raise ValueError(f"El usuario {to_id} no existe")

        if self.users[from_id]['status'] != 'ACTIVA':
            raise CuentaNoActivaError(f"La cuenta {from_id} no está ACTIVA")
        if self.users[to_id]['status'] != 'ACTIVA':
            raise CuentaNoActivaError(f"La cuenta {to_id} no está ACTIVA")

        if self.users[from_id]['balance'] < amount:
            raise SaldoInsuficienteError(f"Saldo insuficiente en {from_id}")

        # Calcular comisión si amount > 1000
        commission = amount * 0.01 if amount > 1000 else 0

        # Asegurar que SYSTEM_REVENUE existe
        if 'SYSTEM_REVENUE' not in self.users:
            self.create_user('SYSTEM_REVENUE', 'System Revenue', 0.0, 'ACTIVA')

        # Actualizar balances
        self.users[from_id]['balance'] -= amount
        transfer_amount = amount - commission
        self.users[to_id]['balance'] += transfer_amount
        if commission > 0:
            self.users['SYSTEM_REVENUE']['balance'] += commission

    async def _async_deposit(self, user_id: str, amount: float, lock: asyncio.Lock):
        """Realiza un depósito de forma concurrente con protección de lock."""
        async with lock:
            self.users[user_id]['balance'] += amount

    def execute_concurrent_deposits(self, user_id: str, amounts_list: List[float]):
        """Ejecuta depósitos concurrentes de forma segura sin requerir async def en el arnés."""
        if user_id not in self.users:
            raise CuentaNoActivaError(f"La cuenta {user_id} no existe")
        if self.users[user_id]['status'] != 'ACTIVA':
            raise CuentaNoActivaError(f"La cuenta {user_id} no está ACTIVA")

        async def run_deposits():
            lock = asyncio.Lock()
            tasks = [self._async_deposit(user_id, amount, lock) for amount in amounts_list]
            await asyncio.gather(*tasks)

        asyncio.run(run_deposits())
