"""Typed external side-effect actions. Loopback-only for real 0.9.1 tests."""

from __future__ import annotations

import html
import http.client
import json
import re
from urllib.parse import urlencode, urlsplit

from pydantic import BaseModel, ConfigDict, Field

from .contracts import RiskLevel, ToolDefinition, ToolError, ToolProvider, ToolResult

FORM_SUBMIT = "FORM_SUBMIT"
MESSAGE_SEND = "MESSAGE_SEND"
EMAIL_SEND = "EMAIL_SEND"
ACCOUNT_CHANGE = "ACCOUNT_CHANGE"
PURCHASE = "PURCHASE"
FILE_UPLOAD = "FILE_UPLOAD"
DOWNLOAD = "DOWNLOAD"
EXTERNAL_WRITE = "EXTERNAL_WRITE"

KINDS = (
    FORM_SUBMIT,
    MESSAGE_SEND,
    EMAIL_SEND,
    ACCOUNT_CHANGE,
    PURCHASE,
    FILE_UPLOAD,
    DOWNLOAD,
    EXTERNAL_WRITE,
)

EXTERNAL_CAPABILITIES = {
    "external_form",
    "external_message",
    "external_purchase",
    "external_email",
}

_SESSIONS: dict[tuple[str, str], dict] = {}
FIELD_NAME = re.compile(r"^[A-Za-z][A-Za-z0-9_-]{0,80}$")
INPUT_NAME = re.compile(r'(?is)<(?:input|textarea|select)[^>]*\bname=["\']([^"\']+)["\']')
DATA_ATTR = re.compile(r'data-(item|price|currency|seller)=["\']([^"\']+)["\']')


class InspectUrlArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    purpose: str | None = Field(default=None, max_length=500)


class FillFormArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    field: str = Field(min_length=1, max_length=80, pattern=r"^[A-Za-z][A-Za-z0-9_-]{0,80}$")
    value: str = Field(default="", max_length=2000)
    purpose: str | None = Field(default=None, max_length=500)


class SubmitFormArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    purpose: str | None = Field(default=None, max_length=500)


class CheckoutArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    url: str = Field(min_length=8, max_length=500)
    item: str = Field(min_length=1, max_length=200)
    quantity: int = Field(default=1, ge=1, le=10)
    currency: str = Field(default="USD", max_length=8, pattern=r"^[A-Z]{3}$")
    total_price: str = Field(min_length=1, max_length=20, pattern=r"^\d+(\.\d{1,2})?$")
    seller: str = Field(min_length=1, max_length=200)
    purpose: str | None = Field(default=None, max_length=500)


class EmailSendArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    to: str = Field(min_length=3, max_length=200)
    subject: str = Field(default="", max_length=200)
    body: str = Field(default="", max_length=4000)
    purpose: str | None = Field(default=None, max_length=500)


class MessageSendArgs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target: str = Field(min_length=1, max_length=200)
    body: str = Field(default="", max_length=4000)
    purpose: str | None = Field(default=None, max_length=500)


def assert_loopback_http(url: str) -> tuple[str, str, int, str]:
    parsed = urlsplit(url or "")
    host = (parsed.hostname or "").rstrip(".").lower()
    if (
        parsed.scheme != "http"
        or host not in {"127.0.0.1", "localhost", "::1"}
        or parsed.username
        or parsed.password
        or parsed.port is None
        or parsed.fragment
        or not 1 <= parsed.port <= 65535
    ):
        raise ToolError("unsafe_url")
    path = parsed.path or "/"
    if parsed.query:
        path = f"{path}?{parsed.query}"
    return url, host, parsed.port, path


def _session(user_id: str, url: str) -> dict:
    return _SESSIONS.setdefault((user_id, url), {"fields": {}, "names": [], "product": {}})


def _http(
    host: str, port: int, method: str, path: str, body: str | None = None, content_type: str | None = None
):
    payload = body.encode("utf-8") if body is not None else None
    headers = {"Accept": "text/html, text/plain, application/json"}
    if content_type:
        headers["Content-Type"] = content_type
    connection = http.client.HTTPConnection(host, port, timeout=8)
    try:
        connection.request(method, path, body=payload, headers=headers)
        response = connection.getresponse()
        raw = response.read(20000)
        text = raw.decode("utf-8", errors="replace")
        return response.status, text
    except (OSError, http.client.HTTPException) as error:
        raise ToolError("provider_unavailable") from error
    finally:
        connection.close()


class LoopbackFormProvider(ToolProvider[InspectUrlArgs | FillFormArgs | SubmitFormArgs]):
    async def execute(self, args, context):
        url, host, port, path = assert_loopback_http(args.url)
        session = _session(context.user_id, url)
        if isinstance(args, InspectUrlArgs):
            status, text = _http(host, port, "GET", path)
            names = [item for item in INPUT_NAME.findall(text) if FIELD_NAME.match(item)][:32]
            session["names"] = names
            listing = ", ".join(names) or "(none)"
            return ToolResult(
                text=f"form_status={status}\nfields={listing}",
                metadata={
                    "kind": FORM_SUBMIT,
                    "side_effect": "none",
                    "url": url,
                    "fields": names,
                    "requires_confirmation": False,
                },
            )
        if isinstance(args, FillFormArgs):
            names = session.get("names") or []
            if args.field not in names:
                raise ToolError("invalid_arguments")
            session["fields"][args.field] = args.value
            return ToolResult(
                text=f"filled {args.field}",
                metadata={
                    "kind": FORM_SUBMIT,
                    "side_effect": "none",
                    "url": url,
                    "field": args.field,
                    "requires_confirmation": False,
                },
            )
        action = url
        submit_path = path
        if path.rstrip("/").endswith("/form") or "form" in path:
            submit_path = "/submit"
            parsed = urlsplit(url)
            action = f"http://{parsed.hostname}:{parsed.port}/submit"
        _, host, port, _ignored = assert_loopback_http(action)
        status, text = _http(
            host,
            port,
            "POST",
            submit_path,
            urlencode(session.get("fields") or {}),
            "application/x-www-form-urlencoded",
        )
        return ToolResult(
            text=html.unescape(text)[:4000],
            metadata={
                "kind": FORM_SUBMIT,
                "side_effect": "form_submit",
                "url": action,
                "status": status,
                "requires_confirmation": True,
            },
        )


class LoopbackPurchaseProvider(ToolProvider[InspectUrlArgs | CheckoutArgs]):
    async def execute(self, args, context):
        url, host, port, path = assert_loopback_http(args.url)
        session = _session(context.user_id, url)
        if isinstance(args, InspectUrlArgs):
            status, text = _http(host, port, "GET", path)
            found = {key: value for key, value in DATA_ATTR.findall(text)}
            product = {
                "item": found.get("item") or "Alex Test Item",
                "total_price": found.get("price") or "1.23",
                "currency": found.get("currency") or "USD",
                "seller": found.get("seller") or "Alex Test Shop",
            }
            session["product"] = product
            return ToolResult(
                text=(
                    f"item={product['item']}\nprice=${product['total_price']}\n"
                    f"currency={product['currency']}\nseller={product['seller']}"
                ),
                metadata={"kind": PURCHASE, "side_effect": "none", "url": url, **product, "status": status},
            )
        product = session.get("product") or {}
        if product:
            if args.item != product.get("item") or args.total_price != product.get("total_price"):
                raise ToolError("confirmation_mismatch")
            if args.currency != product.get("currency") or args.seller != product.get("seller"):
                raise ToolError("confirmation_mismatch")
        parsed = urlsplit(url)
        checkout = f"http://{parsed.hostname}:{parsed.port}/checkout"
        _, host, port, checkout_path = assert_loopback_http(checkout)
        body = json.dumps(
            {
                "item": args.item,
                "quantity": args.quantity,
                "currency": args.currency,
                "total_price": args.total_price,
                "seller": args.seller,
            },
            sort_keys=True,
        )
        status, text = _http(host, port, "POST", checkout_path, body, "application/json")
        return ToolResult(
            text=text[:4000],
            metadata={
                "kind": PURCHASE,
                "side_effect": "purchase_test",
                "url": checkout,
                "item": args.item,
                "quantity": args.quantity,
                "currency": args.currency,
                "total_price": args.total_price,
                "seller": args.seller,
                "status": status,
                "requires_confirmation": True,
            },
        )


class UnconfiguredExternalProvider(ToolProvider[BaseModel]):
    def __init__(self, code: str, kind: str):
        self.code = code
        self.kind = kind

    async def execute(self, args, context):
        del args, context
        raise ToolError(self.code)


def register_external_tools(registry):
    forms = LoopbackFormProvider()
    shop = LoopbackPurchaseProvider()
    email = UnconfiguredExternalProvider("email_not_configured", EMAIL_SEND)
    message = UnconfiguredExternalProvider("message_not_configured", MESSAGE_SEND)
    for name, schema, capability, risk, description, adapter in (
        (
            "inspect_form",
            InspectUrlArgs,
            "external_form",
            RiskLevel.READ,
            "Inspect a local loopback HTML form. Lists typed field names. No JavaScript.",
            forms,
        ),
        (
            "fill_form_field",
            FillFormArgs,
            "external_form",
            RiskLevel.NORMAL_CHANGE,
            "Fill one inspected form field in a local session. Does not submit.",
            forms,
        ),
        (
            "submit_form",
            SubmitFormArgs,
            "external_form",
            RiskLevel.SENSITIVE,
            "Submit a previously filled local loopback form after confirmation.",
            forms,
        ),
        (
            "inspect_product",
            InspectUrlArgs,
            "external_purchase",
            RiskLevel.READ,
            "Inspect a local fake-shop page. No real merchant.",
            shop,
        ),
        (
            "checkout_purchase",
            CheckoutArgs,
            "external_purchase",
            RiskLevel.CRITICAL,
            "Local fake checkout only. Never a real purchase. Always CRITICAL confirmation.",
            shop,
        ),
        (
            "email_send",
            EmailSendArgs,
            "external_email",
            RiskLevel.SENSITIVE,
            "Email send contract. No provider is configured in 0.9.1.",
            email,
        ),
        (
            "message_send",
            MessageSendArgs,
            "external_message",
            RiskLevel.SENSITIVE,
            "Message send contract. No provider is configured in 0.9.1.",
            message,
        ),
    ):
        registry.register(
            ToolDefinition(name, description, schema, capability, risk, "free", 30, "external"),
            adapter,
        )
