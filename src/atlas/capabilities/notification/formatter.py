"""Notification formatter — domain -> rendered, provider-neutral."""

from __future__ import annotations

from atlas.capabilities.notification.domain.models import Notification


class RenderedMessage:
    def __init__(self, title: str, body: str) -> None:
        self.title = title
        self.body = body
        # Actions are attached separately in Dispatcher, but we could put them here if needed.


class Formatter:
    def render(self, n: Notification) -> RenderedMessage:
        title = n.title
        body = n.body
        # Here we could pass it through a TemplateEngine if n.template is set
        return RenderedMessage(title=title, body=body)
