"""Action policy: the gate every action passes through.

Default-deny for unknown origins. Real payment hosts are denied by construction
and cannot be enabled by configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from urllib.parse import urlparse

from flowstate.db.models import ActionDefinition

SANDBOX_ORIGINS = frozenset(
    {
        "http://127.0.0.1:8100",
        "http://localhost:8100",
        "http://127.0.0.1:8000",
        "http://localhost:8000",
    }
)

# The controlled demo app may move ports (tests start it on a free one), so all
# loopback hosts are allow-listed while every non-loopback host stays denied.
# This is still an explicit allow-list: the check below is the only path to
# ALLOW for an origin not named in ``SANDBOX_ORIGINS``.
LOOPBACK_HOSTS = frozenset({"127.0.0.1", "localhost", "::1"})

REAL_PAYMENT_HOSTS = frozenset(
    {
        "stripe.com",
        "api.stripe.com",
        "paypal.com",
        "api.paypal.com",
        "adyen.com",
        "checkout.com",
        "braintreegateway.com",
        "squareup.com",
        "worldpay.com",
    }
)


class PolicyDecision(StrEnum):
    ALLOW = "ALLOW"
    DENY = "DENY"
    ESCALATE = "ESCALATE"


@dataclass(frozen=True)
class PolicyResult:
    decision: PolicyDecision
    code: str
    detail: str


class ActionPolicy:
    def __init__(self, allowed_origins: frozenset[str] = SANDBOX_ORIGINS) -> None:
        self.allowed_origins = allowed_origins

    def resolve_origin(self, url: str) -> PolicyResult:
        parsed = urlparse(url)
        if parsed.scheme not in {"http", "https"} or not parsed.hostname:
            return PolicyResult(
                PolicyDecision.DENY, "ORIGIN_INVALID", f"Unparseable origin {url!r}"
            )
        host = parsed.hostname.lower()
        if host in REAL_PAYMENT_HOSTS or any(
            host.endswith(f".{item}") for item in REAL_PAYMENT_HOSTS
        ):
            return PolicyResult(
                PolicyDecision.DENY,
                "REAL_PAYMENT_HOST",
                f"{host} is a real payment provider and is denied unconditionally.",
            )
        origin = f"{parsed.scheme}://{parsed.netloc}".lower()
        if origin in self.allowed_origins:
            return PolicyResult(PolicyDecision.ALLOW, "ORIGIN_ALLOWED", f"{origin} is allow-listed")
        if host in LOOPBACK_HOSTS:
            return PolicyResult(
                PolicyDecision.ALLOW,
                "LOOPBACK_ALLOWED",
                f"{origin} is a loopback sandbox origin.",
            )
        return PolicyResult(
            PolicyDecision.DENY,
            "ORIGIN_NOT_ALLOWED",
            f"{origin} is not in the sandbox allow-list.",
        )

    def may_execute(self, action: ActionDefinition) -> PolicyResult:
        target = action.target or {}
        origin = target.get("origin")
        if origin:
            result = self.resolve_origin(str(origin))
            if result.decision != PolicyDecision.ALLOW:
                return result
        elif action.kind == "PAYMENT_SIMULATED":
            return PolicyResult(
                PolicyDecision.DENY,
                "PAYMENT_ORIGIN_REQUIRED",
                "A payment-shaped action must declare its sandbox origin explicitly.",
            )

        if action.side_effect_class == "IRREVERSIBLE" and not (action.postconditions or []):
            return PolicyResult(
                PolicyDecision.ESCALATE,
                "IRREVERSIBLE_UNVERIFIABLE",
                "An irreversible action with no postcondition cannot be verified; a human "
                "must authorize it.",
            )
        return PolicyResult(PolicyDecision.ALLOW, "ALLOWED", "policy allows this action")


DEFAULT_POLICY = ActionPolicy()
