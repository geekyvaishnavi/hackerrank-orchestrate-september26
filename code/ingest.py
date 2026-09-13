"""Participant-facing CSV ingestion, validation, indexing, and quality reporting."""

from __future__ import annotations

import csv
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from types import MappingProxyType
from typing import Callable, Mapping, Sequence, TypeVar

from domain import (
    Currency,
    Direction,
    EventStatus,
    EventType,
    ExchangeRate,
    FinancialEvent,
    FinancialProfile,
    Flexibility,
    ImageReference,
    Message,
    PaymentMethod,
    PaymentOption,
    Request,
    RequestType,
    parse_date,
    parse_money,
)


REQUEST_COLUMNS = (
    "request_id",
    "user_id",
    "request_date",
    "request_type",
    "requested_amount",
    "desired_completion_date",
    "allows_partial_payment",
    "request_text",
)
OUTPUT_COLUMNS = (
    "request_id",
    "amount_safe_to_pay",
    "affordability_status",
    "recommended_payment_method",
    "payment_plan",
    "earliest_date_for_full_payment",
    "spending_changes_needed",
    "decision_explanation",
)
REQUIRED_HEADERS: Mapping[str, tuple[str, ...]] = MappingProxyType(
    {
        "financial_profiles.csv": (
            "user_id",
            "home_currency",
            "current_available_balance",
            "minimum_balance_to_keep",
            "financial_priorities",
            "expense_categories_to_protect",
            "expense_categories_user_is_willing_to_reduce",
            "expense_categories_user_is_willing_to_stop",
            "payment_methods_user_will_consider",
            "max_installment_months",
        ),
        "financial_events.csv": (
            "event_id",
            "user_id",
            "event_type",
            "description",
            "category",
            "direction",
            "amount",
            "currency",
            "event_date",
            "settlement_date",
            "status",
            "linked_event_id",
            "flexibility",
            "minimum_allowed_amount",
        ),
        "exchange_rates.csv": ("rate_date", "from_currency", "to_currency", "rate"),
        "requests.csv": REQUEST_COLUMNS,
        "sample_requests.csv": REQUEST_COLUMNS + OUTPUT_COLUMNS[1:],
        "request_payment_options.csv": (
            "payment_option_id",
            "request_id",
            "payment_method",
            "payment_amount",
            "number_of_payments",
            "first_payment_date",
            "payment_frequency_days",
            "financing_fee",
            "total_payable_amount",
        ),
        "messages.csv": (
            "message_id",
            "user_id",
            "request_id",
            "related_event_id",
            "sent_at",
            "source_type",
            "message_text",
        ),
        "images.csv": ("image_id", "user_id", "request_id", "related_event_id"),
        "output.csv": OUTPUT_COLUMNS,
    }
)


class DataValidationError(ValueError):
    """A source-data violation annotated with file, row, and field context."""

    def __init__(self, filename: str, message: str, row_number: int | None = None) -> None:
        location = filename if row_number is None else f"{filename}: row {row_number}"
        super().__init__(f"{location}: {message}")


@dataclass(frozen=True, slots=True)
class SampleRequest:
    request: Request
    expected_output: Mapping[str, str]


@dataclass(frozen=True, slots=True)
class DataQualityReport:
    row_counts: Mapping[str, int]
    optional_blank_counts: Mapping[str, int]

    def render(self) -> str:
        lines = ["Input validation passed.", "Rows loaded:"]
        lines.extend(f"  {name}: {count}" for name, count in sorted(self.row_counts.items()))
        lines.append("Optional blank fields:")
        lines.extend(
            f"  {field}: {count}" for field, count in sorted(self.optional_blank_counts.items())
        )
        return "\n".join(lines)


@dataclass(frozen=True, slots=True)
class IngestedDataset:
    profiles: tuple[FinancialProfile, ...]
    events: tuple[FinancialEvent, ...]
    exchange_rates: tuple[ExchangeRate, ...]
    requests: tuple[Request, ...]
    sample_requests: tuple[SampleRequest, ...]
    payment_options: tuple[PaymentOption, ...]
    messages: tuple[Message, ...]
    images: tuple[ImageReference, ...]
    output_template_request_ids: tuple[str, ...]
    profiles_by_user: Mapping[str, FinancialProfile]
    events_by_id: Mapping[str, FinancialEvent]
    events_by_user: Mapping[str, tuple[FinancialEvent, ...]]
    requests_by_id: Mapping[str, Request]
    payment_options_by_id: Mapping[str, PaymentOption]
    payment_options_by_request: Mapping[str, tuple[PaymentOption, ...]]
    messages_by_id: Mapping[str, Message]
    messages_by_user: Mapping[str, tuple[Message, ...]]
    images_by_id: Mapping[str, ImageReference]
    images_by_user: Mapping[str, tuple[ImageReference, ...]]
    report: DataQualityReport


RecordType = TypeVar("RecordType")


def _read_rows(dataset_dir: Path, filename: str) -> list[dict[str, str]]:
    path = dataset_dir / filename
    if not path.is_file():
        raise DataValidationError(filename, "required file is missing")
    try:
        with path.open("r", encoding="utf-8", newline="") as handle:
            reader = csv.DictReader(handle)
            actual_headers = tuple(reader.fieldnames or ())
            if actual_headers != REQUIRED_HEADERS[filename]:
                raise DataValidationError(
                    filename,
                    f"header mismatch; expected {REQUIRED_HEADERS[filename]!r}, got {actual_headers!r}",
                )
            rows = list(reader)
    except UnicodeDecodeError as error:
        raise DataValidationError(filename, "must be valid UTF-8") from error
    for row_number, row in enumerate(rows, start=2):
        if None in row:
            raise DataValidationError(filename, "row has more fields than its header", row_number)
    return rows


def _error(filename: str, row_number: int, field: str, error: Exception) -> DataValidationError:
    return DataValidationError(filename, f"invalid {field}: {error}", row_number)


def _parse_enum(
    enum_type: type[RecordType], value: str, filename: str, row_number: int, field: str
) -> RecordType:
    try:
        return enum_type(value)  # type: ignore[call-arg]
    except ValueError as error:
        raise _error(filename, row_number, field, error) from error


def _parse_money(value: str, filename: str, row_number: int, field: str):
    try:
        return parse_money(value, field_name=field)
    except (TypeError, ValueError) as error:
        raise _error(filename, row_number, field, error) from error


def _parse_optional_money(value: str, filename: str, row_number: int, field: str):
    return None if value == "" else _parse_money(value, filename, row_number, field)


def _parse_date(value: str, filename: str, row_number: int, field: str):
    try:
        return parse_date(value, field_name=field)
    except (TypeError, ValueError) as error:
        raise _error(filename, row_number, field, error) from error


def _parse_optional_date(value: str, filename: str, row_number: int, field: str):
    return None if value == "" else _parse_date(value, filename, row_number, field)


def _parse_positive_int(value: str, filename: str, row_number: int, field: str) -> int:
    try:
        parsed = int(value)
    except ValueError as error:
        raise _error(filename, row_number, field, error) from error
    if parsed < 1:
        raise DataValidationError(filename, f"invalid {field}: must be positive", row_number)
    return parsed


def _parse_bool(value: str, filename: str, row_number: int, field: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise DataValidationError(filename, f"invalid {field}: expected true or false", row_number)


def _split_set(value: str) -> frozenset[str]:
    return frozenset(part for part in value.split("|") if part)


def _parse_datetime(value: str, filename: str, row_number: int) -> datetime:
    try:
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as error:
        raise _error(filename, row_number, "sent_at", error) from error


def _unique(
    rows: Sequence[dict[str, str]], filename: str, id_field: str
) -> None:
    seen: set[str] = set()
    for row_number, row in enumerate(rows, start=2):
        identifier = row[id_field]
        if not identifier:
            raise DataValidationError(filename, f"{id_field} cannot be blank", row_number)
        if identifier in seen:
            raise DataValidationError(filename, f"duplicate {id_field} {identifier!r}", row_number)
        seen.add(identifier)


def _build_records(
    rows: Sequence[dict[str, str]], filename: str, parser: Callable[[dict[str, str], int], RecordType]
) -> tuple[RecordType, ...]:
    records: list[RecordType] = []
    for row_number, row in enumerate(rows, start=2):
        try:
            records.append(parser(row, row_number))
        except DataValidationError:
            raise
        except (TypeError, ValueError) as error:
            raise DataValidationError(filename, str(error), row_number) from error
    return tuple(records)


def _profile(row: dict[str, str], n: int) -> FinancialProfile:
    filename = "financial_profiles.csv"
    methods = frozenset(
        _parse_enum(PaymentMethod, item, filename, n, "payment_methods_user_will_consider")
        for item in _split_set(row["payment_methods_user_will_consider"])
    )
    maximum = (
        None
        if row["max_installment_months"] == ""
        else _parse_positive_int(row["max_installment_months"], filename, n, "max_installment_months")
    )
    return FinancialProfile(
        user_id=row["user_id"],
        home_currency=_parse_enum(Currency, row["home_currency"], filename, n, "home_currency"),
        current_available_balance=_parse_money(row["current_available_balance"], filename, n, "current_available_balance"),
        minimum_balance_to_keep=_parse_money(row["minimum_balance_to_keep"], filename, n, "minimum_balance_to_keep"),
        financial_priorities=_split_set(row["financial_priorities"]),
        protected_categories=_split_set(row["expense_categories_to_protect"]),
        reducible_categories=_split_set(row["expense_categories_user_is_willing_to_reduce"]),
        stoppable_categories=_split_set(row["expense_categories_user_is_willing_to_stop"]),
        payment_methods=methods,
        max_installment_months=maximum,
    )


def _event(row: dict[str, str], n: int) -> FinancialEvent:
    filename = "financial_events.csv"
    return FinancialEvent(
        event_id=row["event_id"],
        user_id=row["user_id"],
        event_type=_parse_enum(EventType, row["event_type"], filename, n, "event_type"),
        description=row["description"],
        category=row["category"],
        direction=_parse_enum(Direction, row["direction"], filename, n, "direction"),
        amount=_parse_optional_money(row["amount"], filename, n, "amount"),
        currency=_parse_enum(Currency, row["currency"], filename, n, "currency"),
        event_date=_parse_date(row["event_date"], filename, n, "event_date"),
        settlement_date=_parse_optional_date(row["settlement_date"], filename, n, "settlement_date"),
        status=_parse_enum(EventStatus, row["status"], filename, n, "status"),
        linked_event_id=row["linked_event_id"] or None,
        flexibility=_parse_enum(Flexibility, row["flexibility"], filename, n, "flexibility"),
        minimum_allowed_amount=_parse_optional_money(row["minimum_allowed_amount"], filename, n, "minimum_allowed_amount"),
    )


def _rate(row: dict[str, str], n: int) -> ExchangeRate:
    filename = "exchange_rates.csv"
    return ExchangeRate(
        rate_date=_parse_date(row["rate_date"], filename, n, "rate_date"),
        from_currency=_parse_enum(Currency, row["from_currency"], filename, n, "from_currency"),
        to_currency=_parse_enum(Currency, row["to_currency"], filename, n, "to_currency"),
        rate=_parse_money(row["rate"], filename, n, "rate"),
    )


def _request(row: dict[str, str], n: int, filename: str) -> Request:
    return Request(
        request_id=row["request_id"],
        user_id=row["user_id"],
        request_date=_parse_date(row["request_date"], filename, n, "request_date"),
        request_type=_parse_enum(RequestType, row["request_type"], filename, n, "request_type"),
        requested_amount=_parse_money(row["requested_amount"], filename, n, "requested_amount"),
        desired_completion_date=_parse_date(
            row["desired_completion_date"], filename, n, "desired_completion_date"
        ),
        allows_partial_payment=_parse_bool(row["allows_partial_payment"], filename, n, "allows_partial_payment"),
        request_text=row["request_text"],
    )


def _option(row: dict[str, str], n: int) -> PaymentOption:
    filename = "request_payment_options.csv"
    frequency = (
        None
        if row["payment_frequency_days"] == ""
        else _parse_positive_int(row["payment_frequency_days"], filename, n, "payment_frequency_days")
    )
    return PaymentOption(
        payment_option_id=row["payment_option_id"],
        request_id=row["request_id"],
        payment_method=_parse_enum(PaymentMethod, row["payment_method"], filename, n, "payment_method"),
        payment_amount=_parse_money(row["payment_amount"], filename, n, "payment_amount"),
        number_of_payments=_parse_positive_int(row["number_of_payments"], filename, n, "number_of_payments"),
        first_payment_date=_parse_date(row["first_payment_date"], filename, n, "first_payment_date"),
        payment_frequency_days=frequency,
        financing_fee=_parse_money(row["financing_fee"], filename, n, "financing_fee"),
        total_payable_amount=_parse_money(row["total_payable_amount"], filename, n, "total_payable_amount"),
    )


def _message(row: dict[str, str], n: int) -> Message:
    filename = "messages.csv"
    return Message(
        message_id=row["message_id"],
        user_id=row["user_id"],
        request_id=row["request_id"] or None,
        related_event_id=row["related_event_id"] or None,
        sent_at=_parse_datetime(row["sent_at"], filename, n),
        source_type=row["source_type"],
        message_text=row["message_text"],
    )


def _image(row: dict[str, str], n: int) -> ImageReference:
    return ImageReference(
        image_id=row["image_id"],
        user_id=row["user_id"],
        request_id=row["request_id"] or None,
        related_event_id=row["related_event_id"] or None,
    )


def _index_by_id(records: Sequence[RecordType], identifier: Callable[[RecordType], str]) -> Mapping[str, RecordType]:
    return MappingProxyType({identifier(record): record for record in records})


def _group_by(records: Sequence[RecordType], key: Callable[[RecordType], str]) -> Mapping[str, tuple[RecordType, ...]]:
    groups: dict[str, list[RecordType]] = defaultdict(list)
    for record in records:
        groups[key(record)].append(record)
    return MappingProxyType({group_key: tuple(values) for group_key, values in groups.items()})


def _validate_references(
    profiles: Sequence[FinancialProfile],
    events: Sequence[FinancialEvent],
    requests: Sequence[Request],
    samples: Sequence[SampleRequest],
    options: Sequence[PaymentOption],
    messages: Sequence[Message],
    images: Sequence[ImageReference],
    output_ids: Sequence[str],
) -> None:
    profile_ids = {profile.user_id for profile in profiles}
    event_ids = {event.event_id for event in events}
    evaluation_ids = {request.request_id for request in requests}
    sample_ids = {sample.request.request_id for sample in samples}
    all_request_ids = evaluation_ids | sample_ids

    def require_known(values: Sequence[tuple[str, str]], known: set[str], label: str) -> None:
        for source_id, target_id in values:
            if target_id not in known:
                raise DataValidationError("cross-file references", f"{source_id} references unknown {label} {target_id!r}")

    require_known([(event.event_id, event.user_id) for event in events], profile_ids, "user_id")
    require_known([(request.request_id, request.user_id) for request in requests], profile_ids, "user_id")
    require_known([(sample.request.request_id, sample.request.user_id) for sample in samples], profile_ids, "user_id")
    require_known([(option.payment_option_id, option.request_id) for option in options], all_request_ids, "request_id")
    require_known([(message.message_id, message.user_id) for message in messages], profile_ids, "user_id")
    require_known([(image.image_id, image.user_id) for image in images], profile_ids, "user_id")
    require_known(
        [(message.message_id, message.request_id) for message in messages if message.request_id],
        all_request_ids,
        "request_id",
    )
    require_known(
        [(image.image_id, image.request_id) for image in images if image.request_id],
        all_request_ids,
        "request_id",
    )
    require_known(
        [(message.message_id, message.related_event_id) for message in messages if message.related_event_id],
        event_ids,
        "related_event_id",
    )
    require_known(
        [(image.image_id, image.related_event_id) for image in images if image.related_event_id],
        event_ids,
        "related_event_id",
    )
    if len(output_ids) != len(set(output_ids)):
        raise DataValidationError("output.csv", "duplicate request_id")
    if set(output_ids) != evaluation_ids:
        missing = sorted(evaluation_ids - set(output_ids))
        unexpected = sorted(set(output_ids) - evaluation_ids)
        raise DataValidationError(
            "output.csv", f"request_id set mismatch; missing={missing!r}, unexpected={unexpected!r}"
        )


def load_dataset(dataset_dir: Path) -> IngestedDataset:
    """Load and validate every participant-facing input from *dataset_dir*."""
    raw = {filename: _read_rows(dataset_dir, filename) for filename in REQUIRED_HEADERS}
    for filename, id_field in (
        ("financial_profiles.csv", "user_id"),
        ("financial_events.csv", "event_id"),
        ("requests.csv", "request_id"),
        ("sample_requests.csv", "request_id"),
        ("request_payment_options.csv", "payment_option_id"),
        ("messages.csv", "message_id"),
        ("images.csv", "image_id"),
        ("output.csv", "request_id"),
    ):
        _unique(raw[filename], filename, id_field)

    profiles = _build_records(raw["financial_profiles.csv"], "financial_profiles.csv", _profile)
    events = _build_records(raw["financial_events.csv"], "financial_events.csv", _event)
    exchange_rates = _build_records(raw["exchange_rates.csv"], "exchange_rates.csv", _rate)
    requests = _build_records(
        raw["requests.csv"], "requests.csv", lambda row, n: _request(row, n, "requests.csv")
    )
    sample_requests = tuple(
        SampleRequest(
            request=_request(row, row_number, "sample_requests.csv"),
            expected_output=MappingProxyType({column: row[column] for column in OUTPUT_COLUMNS[1:]}),
        )
        for row_number, row in enumerate(raw["sample_requests.csv"], start=2)
    )
    options = _build_records(raw["request_payment_options.csv"], "request_payment_options.csv", _option)
    messages = _build_records(raw["messages.csv"], "messages.csv", _message)
    images = _build_records(raw["images.csv"], "images.csv", _image)
    output_ids = tuple(row["request_id"] for row in raw["output.csv"])
    _validate_references(profiles, events, requests, sample_requests, options, messages, images, output_ids)

    optional_fields = {
        "financial_events.amount",
        "financial_events.settlement_date",
        "financial_events.linked_event_id",
        "financial_events.minimum_allowed_amount",
        "financial_profiles.expense_categories_user_is_willing_to_reduce",
        "financial_profiles.expense_categories_user_is_willing_to_stop",
        "financial_profiles.max_installment_months",
        "request_payment_options.payment_frequency_days",
        "messages.request_id",
        "messages.related_event_id",
    }
    blanks = {
        field: sum(
            row[field.split(".", 1)[1]] == ""
            for row in raw[f"{field.split('.', 1)[0]}.csv"]
        )
        for field in optional_fields
    }
    report = DataQualityReport(
        row_counts=MappingProxyType({filename: len(rows) for filename, rows in raw.items()}),
        optional_blank_counts=MappingProxyType(blanks),
    )
    return IngestedDataset(
        profiles=profiles,
        events=events,
        exchange_rates=exchange_rates,
        requests=requests,
        sample_requests=sample_requests,
        payment_options=options,
        messages=messages,
        images=images,
        output_template_request_ids=output_ids,
        profiles_by_user=_index_by_id(profiles, lambda profile: profile.user_id),
        events_by_id=_index_by_id(events, lambda event: event.event_id),
        events_by_user=_group_by(events, lambda event: event.user_id),
        requests_by_id=_index_by_id(requests, lambda request: request.request_id),
        payment_options_by_id=_index_by_id(options, lambda option: option.payment_option_id),
        payment_options_by_request=_group_by(options, lambda option: option.request_id),
        messages_by_id=_index_by_id(messages, lambda message: message.message_id),
        messages_by_user=_group_by(messages, lambda message: message.user_id),
        images_by_id=_index_by_id(images, lambda image: image.image_id),
        images_by_user=_group_by(images, lambda image: image.user_id),
        report=report,
    )
