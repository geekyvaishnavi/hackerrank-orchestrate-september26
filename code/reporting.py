"""Safe, aggregate-only reporting for validated participant data."""

from __future__ import annotations

from dataclasses import dataclass
from types import MappingProxyType
from typing import Mapping


@dataclass(frozen=True, slots=True)
class DatasetAudit:
    """Aggregate graph diagnostics with no message or image contents."""

    evaluation_request_count: int
    sample_request_count: int
    option_count_histogram: Mapping[int, int]
    request_message_count: int
    request_image_count: int
    event_message_count: int
    event_image_count: int
    user_only_message_count: int
    user_only_image_count: int

    def __post_init__(self) -> None:
        object.__setattr__(self, "option_count_histogram", MappingProxyType(dict(self.option_count_histogram)))

    def render(self) -> str:
        """Render counts only; source text and image payloads are intentionally absent."""
        histogram = ", ".join(
            f"{count} options: {requests} requests"
            for count, requests in sorted(self.option_count_histogram.items())
        )
        return "\n".join(
            (
                "Dataset relationship audit passed.",
                f"Evaluation requests: {self.evaluation_request_count}",
                f"Sample requests: {self.sample_request_count}",
                f"Payment-option cardinality: {histogram}",
                f"Request-linked messages: {self.request_message_count}",
                f"Request-linked images: {self.request_image_count}",
                f"Event-linked messages: {self.event_message_count}",
                f"Event-linked images: {self.event_image_count}",
                f"User-only messages: {self.user_only_message_count}",
                f"User-only images: {self.user_only_image_count}",
            )
        )
