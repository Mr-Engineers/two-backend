"""The one deliberate simulation: a mock payment provider.

The outcome is decided by server configuration (``MOCK_PAYMENT_MODE``) or by the provider injected in
tests. It is NEVER derived from tool/API arguments, and it performs no external operations.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class PaymentResult:
    approved: bool
    reference: str
    decline_reason: str | None = None


class PaymentProvider(Protocol):
    def charge(self, *, amount_gross_minor: int, currency: str, reference: str) -> PaymentResult: ...


class MockPaymentProvider:
    def __init__(self, mode: str = "approve"):
        if mode not in ("approve", "decline"):
            raise ValueError("mode must be 'approve' or 'decline'")
        self.mode = mode

    def charge(self, *, amount_gross_minor: int, currency: str, reference: str) -> PaymentResult:
        ref = f"mock_{uuid.uuid4().hex[:20]}"
        if self.mode == "decline":
            return PaymentResult(False, ref, "declined_by_mock_configuration")
        return PaymentResult(True, ref)
