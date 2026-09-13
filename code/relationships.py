"""Explicit data relationships and constant-time request-context retrieval."""

from __future__ import annotations

from collections import Counter, defaultdict
from dataclasses import dataclass
from types import MappingProxyType
from typing import Callable, Mapping, Sequence, TypeVar

from domain import FinancialEvent, FinancialProfile, ImageReference, Message, PaymentOption, Request
from ingest import DataValidationError, IngestedDataset, SampleRequest
from reporting import DatasetAudit


RecordType = TypeVar("RecordType")


def _group_by(
    records: Sequence[RecordType], key: Callable[[RecordType], str | None]
) -> Mapping[str, tuple[RecordType, ...]]:
    groups: dict[str, list[RecordType]] = defaultdict(list)
    for record in records:
        group_key = key(record)
        if group_key is None:
            raise ValueError("relationship keys must not be None")
        groups[group_key].append(record)
    return MappingProxyType({group_key: tuple(group) for group_key, group in groups.items()})


@dataclass(frozen=True, slots=True)
class RequestContext:
    """All graph-linked records relevant to one evaluation request."""

    request: Request
    profile: FinancialProfile
    user_events: tuple[FinancialEvent, ...]
    payment_options: tuple[PaymentOption, ...]
    user_messages: tuple[Message, ...]
    request_messages: tuple[Message, ...]
    user_images: tuple[ImageReference, ...]
    request_images: tuple[ImageReference, ...]
    event_messages: Mapping[str, tuple[Message, ...]]
    event_images: Mapping[str, tuple[ImageReference, ...]]


@dataclass(frozen=True, slots=True)
class RelationshipGraph:
    """Precomputed joins for user, request, event, message, image, and option data."""

    dataset: IngestedDataset
    evaluation_requests_by_user: Mapping[str, tuple[Request, ...]]
    sample_requests_by_user: Mapping[str, tuple[SampleRequest, ...]]
    messages_by_request: Mapping[str, tuple[Message, ...]]
    images_by_request: Mapping[str, tuple[ImageReference, ...]]
    messages_by_event: Mapping[str, tuple[Message, ...]]
    images_by_event: Mapping[str, tuple[ImageReference, ...]]
    audit: DatasetAudit

    def request_context(self, request_id: str) -> RequestContext:
        """Retrieve one evaluation request context through graph indexes only."""
        try:
            request = self.dataset.requests_by_id[request_id]
        except KeyError as error:
            raise KeyError(f"unknown evaluation request_id: {request_id}") from error
        user_events = self.dataset.events_by_user.get(request.user_id, ())
        event_ids = {event.event_id for event in user_events}
        return RequestContext(
            request=request,
            profile=self.dataset.profiles_by_user[request.user_id],
            user_events=user_events,
            payment_options=self.dataset.payment_options_by_request.get(request_id, ()),
            user_messages=self.dataset.messages_by_user.get(request.user_id, ()),
            request_messages=self.messages_by_request.get(request_id, ()),
            user_images=self.dataset.images_by_user.get(request.user_id, ()),
            request_images=self.images_by_request.get(request_id, ()),
            event_messages=MappingProxyType(
                {
                    event_id: self.messages_by_event[event_id]
                    for event_id in event_ids
                    if event_id in self.messages_by_event
                }
            ),
            event_images=MappingProxyType(
                {
                    event_id: self.images_by_event[event_id]
                    for event_id in event_ids
                    if event_id in self.images_by_event
                }
            ),
        )


def _validate_relationship_ownership(dataset: IngestedDataset) -> None:
    request_users = {request.request_id: request.user_id for request in dataset.requests}
    request_users.update(
        {sample.request.request_id: sample.request.user_id for sample in dataset.sample_requests}
    )
    event_users = {event.event_id: event.user_id for event in dataset.events}
    for label, records in (("message", dataset.messages), ("image", dataset.images)):
        for record in records:
            if record.request_id and request_users[record.request_id] != record.user_id:
                raise DataValidationError(
                    "relationship graph",
                    f"{label} {record_id(record)!r} user_id does not match request {record.request_id!r}",
                )
            if record.related_event_id and event_users[record.related_event_id] != record.user_id:
                raise DataValidationError(
                    "relationship graph",
                    f"{label} {record_id(record)!r} user_id does not match event {record.related_event_id!r}",
                )


def record_id(record: Message | ImageReference) -> str:
    return record.message_id if isinstance(record, Message) else record.image_id


def build_relationship_graph(dataset: IngestedDataset) -> RelationshipGraph:
    """Validate relationship ownership and build immutable, explicit graph indexes."""
    _validate_relationship_ownership(dataset)
    option_counts = Counter(
        len(dataset.payment_options_by_request.get(request.request_id, ()))
        for request in dataset.requests
    )
    invalid_counts = {
        request.request_id: len(dataset.payment_options_by_request.get(request.request_id, ()))
        for request in dataset.requests
        if not 2 <= len(dataset.payment_options_by_request.get(request.request_id, ())) <= 4
    }
    if invalid_counts:
        raise DataValidationError(
            "relationship graph",
            f"evaluation requests must have 2 to 4 payment options; invalid counts={invalid_counts!r}",
        )
    messages_by_request = _group_by(
        [message for message in dataset.messages if message.request_id], lambda message: message.request_id
    )
    images_by_request = _group_by(
        [image for image in dataset.images if image.request_id], lambda image: image.request_id
    )
    messages_by_event = _group_by(
        [message for message in dataset.messages if message.related_event_id],
        lambda message: message.related_event_id,
    )
    images_by_event = _group_by(
        [image for image in dataset.images if image.related_event_id], lambda image: image.related_event_id
    )
    audit = DatasetAudit(
        evaluation_request_count=len(dataset.requests),
        sample_request_count=len(dataset.sample_requests),
        option_count_histogram=dict(option_counts),
        request_message_count=sum(1 for message in dataset.messages if message.request_id),
        request_image_count=sum(1 for image in dataset.images if image.request_id),
        event_message_count=sum(1 for message in dataset.messages if message.related_event_id),
        event_image_count=sum(1 for image in dataset.images if image.related_event_id),
        user_only_message_count=sum(
            1 for message in dataset.messages if not message.request_id and not message.related_event_id
        ),
        user_only_image_count=sum(1 for image in dataset.images if not image.request_id and not image.related_event_id),
    )
    return RelationshipGraph(
        dataset=dataset,
        evaluation_requests_by_user=_group_by(dataset.requests, lambda request: request.user_id),
        sample_requests_by_user=_group_by(dataset.sample_requests, lambda sample: sample.request.user_id),
        messages_by_request=messages_by_request,
        images_by_request=images_by_request,
        messages_by_event=messages_by_event,
        images_by_event=images_by_event,
        audit=audit,
    )
