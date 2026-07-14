"""Normalized web content — the DOM/extraction engines return THESE, never raw HTML."""
from __future__ import annotations

from pydantic import BaseModel

from atlas.capabilities.domain.common import Provenance


class Link(BaseModel):
    model_config = {"frozen": True}
    text: str
    href: str
    rel: str = ""

class ImageRef(BaseModel):
    model_config = {"frozen": True}
    src: str
    alt: str = ""

class Table(BaseModel):
    model_config = {"frozen": True}
    caption: str = ""
    headers: tuple[str, ...] = ()
    rows: tuple[tuple[str, ...], ...] = ()

class CodeBlock(BaseModel):
    model_config = {"frozen": True}
    language: str = ""
    code: str = ""

class FormField(BaseModel):
    model_config = {"frozen": True}
    name: str
    label: str = ""
    kind: str = "text"        # text|password|email|checkbox|select|file|hidden
    required: bool = False
    value: str = ""
    options: tuple[str, ...] = ()

class FormModel(BaseModel):
    """A form understood well enough to preview a fill before submit (the gate)."""
    model_config = {"frozen": True}
    id: str = ""
    action_url: str = ""
    method: str = "post"
    fields: tuple[FormField, ...] = ()
    submits_externally: bool = True

class Citation(BaseModel):
    model_config = {"frozen": True}
    title: str
    url: str
    author: str = ""
    published: str = ""

class Article(BaseModel):
    """Reader-mode extraction: the main content, stripped of chrome."""
    model_config = {"frozen": True}
    title: str
    byline: str = ""
    text: str = ""
    markdown: str = ""
    citations: tuple[Citation, ...] = ()
    provenance: Provenance

class PageMetadata(BaseModel):
    model_config = {"frozen": True}
    title: str = ""
    description: str = ""
    canonical_url: str = ""
    og: dict[str, str] = {}
    lang: str = ""

class WebPage(BaseModel):
    """The full normalized page: everything reasoning might want, typed."""
    model_config = {"frozen": True}
    url: str
    metadata: PageMetadata = PageMetadata()
    article: Article | None = None
    tables: tuple[Table, ...] = ()
    code_blocks: tuple[CodeBlock, ...] = ()
    forms: tuple[FormModel, ...] = ()
    images: tuple[ImageRef, ...] = ()
    links: tuple[Link, ...] = ()
    text: str = ""
    markdown: str = ""
    provenance: Provenance
