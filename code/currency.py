"""Deterministic dated conversion using only supplied exchange-rate records."""

from __future__ import annotations

from collections import defaultdict, deque
from dataclasses import dataclass
from datetime import date
from decimal import Decimal
from enum import Enum
from types import MappingProxyType
from typing import Mapping, Sequence

from domain import Currency, ExchangeRate


class ConversionStatus(str, Enum):
    CONVERTED = "converted"
    IDENTITY = "identity"
    MISSING_RATE = "missing_rate"


@dataclass(frozen=True, slots=True)
class RateLeg:
    """One supplied directed rate used in a dated conversion path."""

    rate_date: date
    from_currency: Currency
    to_currency: Currency
    rate: Decimal


@dataclass(frozen=True, slots=True)
class ConversionResult:
    """A successful conversion and the exact supplied-rate path used."""

    source_id: str
    rate_date: date
    source_amount: Decimal
    from_currency: Currency
    to_currency: Currency
    converted_amount: Decimal
    effective_rate: Decimal
    status: ConversionStatus
    path: tuple[RateLeg, ...]


@dataclass(frozen=True, slots=True)
class ConversionAudit:
    """A serializable conversion outcome, including conservative missing-rate cases."""

    source_id: str
    rate_date: date
    source_amount: Decimal
    from_currency: Currency
    to_currency: Currency
    status: ConversionStatus
    converted_amount: Decimal | None
    effective_rate: Decimal | None
    path: tuple[RateLeg, ...]
    detail: str


class MissingExchangeRateError(ValueError):
    """Raised by strict conversion when no direct/supplied route exists for the date."""

    def __init__(self, audit: ConversionAudit) -> None:
        super().__init__(audit.detail)
        self.audit = audit


class CurrencyConverter:
    """Resolve direct or deterministic, same-date supplied-rate paths only."""

    def __init__(self, exchange_rates: Sequence[ExchangeRate]) -> None:
        by_date: dict[date, dict[tuple[Currency, Currency], RateLeg]] = defaultdict(dict)
        for exchange_rate in exchange_rates:
            key = (exchange_rate.from_currency, exchange_rate.to_currency)
            if key in by_date[exchange_rate.rate_date]:
                raise ValueError(
                    "duplicate supplied exchange rate for "
                    f"{exchange_rate.rate_date} {exchange_rate.from_currency.value}->{exchange_rate.to_currency.value}"
                )
            by_date[exchange_rate.rate_date][key] = RateLeg(
                rate_date=exchange_rate.rate_date,
                from_currency=exchange_rate.from_currency,
                to_currency=exchange_rate.to_currency,
                rate=exchange_rate.rate,
            )
        self._rates_by_date: Mapping[date, Mapping[tuple[Currency, Currency], RateLeg]] = (
            MappingProxyType(
                {rate_date: MappingProxyType(dict(rates)) for rate_date, rates in by_date.items()}
            )
        )

    def convert(
        self,
        *,
        source_id: str,
        amount: Decimal,
        from_currency: Currency,
        to_currency: Currency,
        rate_date: date,
    ) -> ConversionResult:
        """Strictly convert an exact Decimal or raise with its missing-rate audit."""
        audit = self.convert_with_audit(
            source_id=source_id,
            amount=amount,
            from_currency=from_currency,
            to_currency=to_currency,
            rate_date=rate_date,
        )
        if audit.status is ConversionStatus.MISSING_RATE:
            raise MissingExchangeRateError(audit)
        return ConversionResult(
            source_id=audit.source_id,
            rate_date=audit.rate_date,
            source_amount=audit.source_amount,
            from_currency=audit.from_currency,
            to_currency=audit.to_currency,
            converted_amount=audit.converted_amount,
            effective_rate=audit.effective_rate,
            status=audit.status,
            path=audit.path,
        )

    def convert_with_audit(
        self,
        *,
        source_id: str,
        amount: Decimal,
        from_currency: Currency,
        to_currency: Currency,
        rate_date: date,
    ) -> ConversionAudit:
        """Return an audit outcome; missing rates never produce a fabricated amount."""
        _validate_request(source_id, amount, from_currency, to_currency, rate_date)
        if from_currency is to_currency:
            return ConversionAudit(
                source_id=source_id,
                rate_date=rate_date,
                source_amount=amount,
                from_currency=from_currency,
                to_currency=to_currency,
                status=ConversionStatus.IDENTITY,
                converted_amount=amount,
                effective_rate=Decimal("1"),
                path=(),
                detail="identity conversion",
            )
        path = self._find_path(rate_date, from_currency, to_currency)
        if path is None:
            return ConversionAudit(
                source_id=source_id,
                rate_date=rate_date,
                source_amount=amount,
                from_currency=from_currency,
                to_currency=to_currency,
                status=ConversionStatus.MISSING_RATE,
                converted_amount=None,
                effective_rate=None,
                path=(),
                detail=(
                    "no supplied exchange-rate path for "
                    f"{rate_date.isoformat()} {from_currency.value}->{to_currency.value}"
                ),
            )
        effective_rate = Decimal("1")
        for leg in path:
            effective_rate *= leg.rate
        return ConversionAudit(
            source_id=source_id,
            rate_date=rate_date,
            source_amount=amount,
            from_currency=from_currency,
            to_currency=to_currency,
            status=ConversionStatus.CONVERTED,
            converted_amount=amount * effective_rate,
            effective_rate=effective_rate,
            path=path,
            detail="supplied-rate conversion",
        )

    def _find_path(
        self, rate_date: date, from_currency: Currency, to_currency: Currency
    ) -> tuple[RateLeg, ...] | None:
        rates = self._rates_by_date.get(rate_date, {})
        direct = rates.get((from_currency, to_currency))
        if direct is not None:
            return (direct,)

        # Breadth-first search is deterministic: only exact-date directed edges
        # are considered and each expansion is ordered by target currency code.
        queue: deque[tuple[Currency, tuple[RateLeg, ...]]] = deque([(from_currency, ())])
        visited = {from_currency}
        while queue:
            current, path = queue.popleft()
            if len(path) >= 2:
                continue
            neighbors = sorted(
                (leg for (source, _), leg in rates.items() if source is current),
                key=lambda leg: leg.to_currency.value,
            )
            for leg in neighbors:
                next_path = path + (leg,)
                if leg.to_currency is to_currency:
                    return next_path
                if leg.to_currency not in visited:
                    visited.add(leg.to_currency)
                    queue.append((leg.to_currency, next_path))
        return None


def _validate_request(
    source_id: str,
    amount: Decimal,
    from_currency: Currency,
    to_currency: Currency,
    rate_date: date,
) -> None:
    if not isinstance(source_id, str) or not source_id.strip():
        raise ValueError("source_id must be non-empty")
    if not isinstance(amount, Decimal):
        raise TypeError("amount must be a Decimal; floats are forbidden")
    if not amount.is_finite():
        raise ValueError("amount must be finite")
    if not isinstance(from_currency, Currency) or not isinstance(to_currency, Currency):
        raise TypeError("currencies must be Currency values")
    if not isinstance(rate_date, date):
        raise TypeError("rate_date must be a date")
