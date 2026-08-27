# shopify_connector/payload_validation.py
"""A5-PR2b — up-front, side-effect-free Shopify payload validators.

The founder-decided classification boundary (spec 2026-08-23): an orders/paid or
refunds/create payload from which an honest ``ShopifyOrder``/``ShopifyRefund``
cannot be constructed is a PERMANENT provider-data error — persist
``ShopifyRejectedEvidence``, acknowledge the webhook (no 503 loop); everything
that fails AFTER validation is classified transient (retryable 503, Shopify
redelivers). An explicit up-front validator was chosen over exception-type
enumeration (NEXT_TASKS A5-PR2b) because ``_prepare_order_item_metadata`` mixes
genuinely transient network failures with the same exception classes a malformed
payload raises.

Pure functions only: no Django model import, no I/O, no lock, no gate call —
safe to run at the very top of ``process_order_paid`` / ``process_refund``,
BEFORE the Company admission lock and before any remote read (the A4 pre-lock
region; see tests/e2e/test_a4_shopify_scheduled_rls.py).

Checks are the founder-enumerated set (id text-trustworthy or absent; money
fields finite bounded Decimal; currency ≤3-char str; transactions /
shipping_lines / line_items / refund_line_items lists of dicts with finite
numerics) plus the conservative string/shape bounds a payload must satisfy for
the canonical row write not to crash post-validation (CharField null/length
bounds, non-dict ``customer``, non-indexable ``payment_gateway_names``) — each
such payload is precisely one from which the honest row cannot be constructed.
Anything the live code tolerates today (absent keys, ``customer: null``,
``transactions: null`` on orders, ``note: null`` on refunds — normalized at the
ingress, absent/empty dates → documented fallback) stays VALID: the validator
must never reject a payload the pipeline can honestly process.

MALFORMED_DATE is emitted ONLY for a truthy ``created_at`` the DateTimeField
cannot store: the live writers store the RAW ``created_at`` string into
``shopify_created_at`` whenever it is truthy (``order_date_str or now()``), so
a truthy unparseable value crashes the row write — the date FALLBACK only ever
covers the falsy/absent case (and the derived ``order_date``/``paid_date``,
which keep their fallback and are never validated).
"""

from dataclasses import dataclass, field
from decimal import Decimal, InvalidOperation

from django.utils.dateparse import parse_date, parse_datetime

# Mirror ShopifyRejectedEvidence.RejectionCode values without importing models
# (pure-module contract; equality is pinned by tests).
MALFORMED_JSON = "MALFORMED_JSON"
MISSING_EXTERNAL_ID = "MISSING_EXTERNAL_ID"
MALFORMED_MONEY = "MALFORMED_MONEY"
MALFORMED_CURRENCY = "MALFORMED_CURRENCY"
MALFORMED_DATE = "MALFORMED_DATE"  # raw-stored created_at only — see module docstring
MALFORMED_STRUCTURE = "MALFORMED_STRUCTURE"

# DecimalField(max_digits=18, decimal_places=2) holds < 10^16 in integer digits.
# The extra unit of margin keeps a value like 9999999999999999.999 — which the
# database would ROUND over the 16-digit ceiling — out of the storable range.
_MONEY_MAX = Decimal(10) ** 16 - 1
_BIGINT_MAX = 9223372036854775807


@dataclass(frozen=True)
class PayloadVerdict:
    """Outcome of validating one payload. ``errors`` holds EVERY detected defect
    (persisted as ``validation_errors``); ``rejection_code`` is the single
    deterministic primary code — the first defect in the fixed check order."""

    ok: bool
    rejection_code: str | None = None
    rejection_message: str = ""
    errors: list[dict] = field(default_factory=list)
    external_id: str | None = None
    parent_external_id: str | None = None


def _short(value) -> str:
    """Truncated, PII-light repr for defect messages."""
    text = repr(value)
    return text if len(text) <= 80 else text[:77] + "..."


def _defect(code: str, fld: str, message: str) -> dict:
    return {"code": code, "field": fld, "message": message}


def trustworthy_external_id(value) -> str | None:
    """A Shopify id we can honestly key rows on: a positive BigInteger-range int,
    or a pure ASCII-digit string of one. Anything else is untrustworthy and must
    stay in the raw evidence only. ASCII + length are checked BEFORE int(): bare
    str.isdigit() accepts superscripts ("²") that make int() raise ValueError,
    and an unbounded digit string would trip CPython's int-conversion limit —
    either would crash the validator instead of classifying the payload."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and 0 < value <= _BIGINT_MAX:
        return str(value)
    if isinstance(value, str) and value.isascii() and value.isdigit() and len(value) <= 19:
        as_int = int(value)
        if 0 < as_int <= _BIGINT_MAX:
            return str(as_int)
    return None


def _money_defect(value, fld: str) -> dict | None:
    """Present money values must be finite Decimals the (18,2) schema can hold.
    Mirrors the live coercion ``Decimal(str(value))`` exactly, then bounds it."""
    if isinstance(value, bool) or not isinstance(value, str | int | float):
        return _defect(MALFORMED_MONEY, fld, f"{fld} is not a numeric value: {_short(value)}")
    try:
        parsed = Decimal(str(value))
    except (InvalidOperation, ValueError):
        return _defect(MALFORMED_MONEY, fld, f"{fld} does not parse as a decimal: {_short(value)}")
    if not parsed.is_finite():
        return _defect(MALFORMED_MONEY, fld, f"{fld} is not finite: {_short(value)}")
    # copy_abs() is CONTEXT-FREE (Codex round-12): abs() applies the active
    # Decimal context, so a finite value with an exponent past Emax (e.g.
    # "1e1000000") raises decimal.Overflow inside the validator — comparisons
    # never round, so copy_abs() >= bound is the safe magnitude check (same at
    # the two aggregate sites).
    if parsed.copy_abs() >= _MONEY_MAX:
        return _defect(MALFORMED_MONEY, fld, f"{fld} exceeds the storable money magnitude: {_short(value)}")
    return None


def _bounded_text_defect(
    value, fld: str, max_length: int, *, allow_none: bool, require_str: bool = True
) -> dict | None:
    """CharField-bound payload strings: None crashes the NOT NULL column (unless
    the live code coerces it), and an over-long value is unstorable on
    PostgreSQL. ``require_str`` (the default): most of these values are also
    copied VERBATIM into typed event payloads — Django str()-coerces at the
    column, but ``validate_event_payload`` rejects a non-str at emission, AFTER
    the canonical row write, rolling everything back into the retryable loop
    (Codex round-1). Only a field the live code str()-coerces on BOTH the model
    and the event path (order_number) may relax it."""
    if value is None:
        if allow_none:
            return None
        return _defect(MALFORMED_STRUCTURE, fld, f"{fld} is null")
    if require_str and not isinstance(value, str):
        return _defect(MALFORMED_STRUCTURE, fld, f"{fld} is not a string: {_short(value)}")
    if len(str(value)) > max_length:
        return _defect(MALFORMED_STRUCTURE, fld, f"{fld} longer than {max_length} characters")
    return None


def _dict_list_defects(value, fld: str, money_keys: tuple[str, ...]) -> list[dict]:
    """Founder-enumerated shape: a list of dicts whose money keys, when present,
    are finite numerics. Present-as-null money is a defect too — the live
    coercion is ``Decimal(str(element.get(key, "0")))``, and ``str(None)``
    raises, so a null amount crashes post-validation into the retry loop."""
    if not isinstance(value, list):
        return [_defect(MALFORMED_STRUCTURE, fld, f"{fld} is not a list: {_short(value)}")]
    defects: list[dict] = []
    for index, element in enumerate(value):
        if not isinstance(element, dict):
            defects.append(
                _defect(MALFORMED_STRUCTURE, f"{fld}[{index}]", f"element is not an object: {_short(element)}")
            )
            continue
        for key in money_keys:
            if key in element:
                if element[key] is None:
                    defects.append(_defect(MALFORMED_MONEY, f"{fld}[{index}].{key}", f"{key} is null"))
                    continue
                money = _money_defect(element[key], f"{fld}[{index}].{key}")
                if money:
                    defects.append(money)
    return defects


def _unstorable_text_reason(value: str) -> str | None:
    """A string json.loads accepts but PostgreSQL jsonb refuses: U+0000 (NUL,
    Codex round-6) or a lone Unicode surrogate from a \\ud800-style escape
    (Codex round-7 — UTF-8 encoding of an unpaired surrogate raises, so both
    the jsonb write and a naive canonical hash would fail on it)."""
    if "\x00" in value:
        return "NUL (\\u0000) in string is not JSON-storable"
    try:
        value.encode("utf-8")
    except UnicodeEncodeError:
        return "lone Unicode surrogate is not JSON-storable"
    return None


def _safe_path_fragment(key) -> str:
    """Defect paths quote payload OBJECT KEYS — an attacker-controlled key can
    itself carry NUL or a lone surrogate, which would make the defect path (and
    so validation_errors) jsonb-unstorable (Codex round-8). Escape to visible
    text and cap the length."""
    text = str(key)[:40]
    if "\x00" in text:
        text = text.replace("\x00", "\\u0000")
    try:
        text.encode("utf-8")
    except UnicodeEncodeError:
        text = text.encode("utf-8", "backslashreplace").decode("utf-8")
    return text


def _jsonb_unstorable_defects(payload) -> list[dict]:
    """Values json.loads accepts but PostgreSQL jsonb refuses — either would
    crash the canonical ``raw_payload`` write AFTER validation, into the
    retryable loop: bare NaN/Infinity tokens ANYWHERE (Codex round-2), and
    NUL / lone-surrogate strings in values OR object keys (rounds 6-7). The
    evidence writer sanitizes its own storage, so rejecting here yields durable
    evidence. Iterative walk — a deeply nested payload must not RecursionError
    the validator — capped at 10 reported sites (one is enough to reject)."""
    defects: list[dict] = []
    stack: list[tuple[str, object]] = [("payload", payload)]
    while stack and len(defects) < 10:
        path, value = stack.pop()
        if isinstance(value, float) and (value != value or value in (float("inf"), float("-inf"))):
            defects.append(_defect(MALFORMED_STRUCTURE, path, "non-finite number is not JSON-storable"))
        elif isinstance(value, str):
            reason = _unstorable_text_reason(value)
            if reason:
                defects.append(_defect(MALFORMED_STRUCTURE, path, reason))
        elif isinstance(value, dict):
            for key, item in value.items():
                key_reason = _unstorable_text_reason(key) if isinstance(key, str) else None
                if key_reason:
                    defects.append(_defect(MALFORMED_STRUCTURE, f"{path}.<key>", f"object key: {key_reason}"))
                    if len(defects) >= 10:
                        break
                # _safe_path_fragment (Codex round-8): a raw NUL/surrogate KEY
                # copied into a child path would land in validation_errors and
                # poison the EVIDENCE insert itself.
                stack.append((f"{path}.{_safe_path_fragment(key)}", item))
        elif isinstance(value, list):
            stack.extend((f"{path}[{index}]", item) for index, item in enumerate(value))
    return defects


def _raw_stored_datetime_defect(payload, fld: str) -> dict | None:
    """The live writers store the RAW ``created_at`` value into the NOT NULL
    ``shopify_created_at`` DateTimeField whenever it is truthy (falsy falls back
    to now()). A truthy value must therefore be a string Django's
    DateTimeField.to_python accepts (parse_datetime, then parse_date) — anything
    else crashes the row write AFTER validation, which would misclassify a
    permanent provider-data error as an endless transient retry."""
    value = payload.get(fld)
    if not value:
        return None  # falsy ⇒ the live `or now()` fallback
    if not isinstance(value, str):
        return _defect(MALFORMED_DATE, fld, f"{fld} is not a datetime string: {_short(value)}")
    try:
        parsed = parse_datetime(value)
        if parsed is None:
            parsed = parse_date(value)
    except ValueError:
        # Well-formatted but impossible (e.g. month 13) — same storage crash.
        parsed = None
    if parsed is None:
        return _defect(MALFORMED_DATE, fld, f"{fld} is not a storable datetime: {_short(value)}")
    return None


def _verdict(errors: list[dict], external_id: str | None, parent_external_id: str | None) -> PayloadVerdict:
    if not errors:
        return PayloadVerdict(
            ok=True,
            external_id=external_id,
            parent_external_id=parent_external_id,
        )
    primary = errors[0]
    extra = f" (+{len(errors) - 1} more defect(s))" if len(errors) > 1 else ""
    return PayloadVerdict(
        ok=False,
        rejection_code=primary["code"],
        rejection_message=f"{primary['message']}{extra}",
        errors=errors,
        external_id=external_id,
        parent_external_id=parent_external_id,
    )


def validate_order_paid_payload(payload) -> PayloadVerdict:
    """Validate an orders/paid payload (webhook + poller share this ingress)."""
    if not isinstance(payload, dict):
        return _verdict(
            [_defect(MALFORMED_STRUCTURE, "payload", f"payload is not an object: {_short(payload)}")], None, None
        )

    errors: list[dict] = []

    # 1. External id — text-trustworthy or the payload is unkeyable.
    external_id = trustworthy_external_id(payload.get("id"))
    if external_id is None:
        if payload.get("id") in (None, "", 0):
            errors.append(_defect(MISSING_EXTERNAL_ID, "id", "order id is missing"))
        else:
            errors.append(
                _defect(MISSING_EXTERNAL_ID, "id", f"order id is not trustworthy: {_short(payload.get('id'))}")
            )

    # 2. Header money fields (absent ⇒ live default "0" — valid).
    for fld in ("total_price", "subtotal_price", "total_tax", "total_discounts"):
        if fld in payload and payload[fld] is not None:
            money = _money_defect(payload[fld], fld)
            if money:
                errors.append(money)
        elif fld in payload:
            # Present-as-null: Decimal(str(None)) raises in the live coercion.
            errors.append(_defect(MALFORMED_MONEY, fld, f"{fld} is null"))

    # 2b. Implied tax rate (Codex round-13): the projection derives
    #     tax_rate = (total_tax / revenue).quantize(0.0001) and stores it into
    #     TaxCode.rate, a DecimalField(5,4) capped below 10 — individually
    #     storable header amounts can imply an unstorable ratio, which would
    #     deterministically fail the projection AFTER the order/event committed.
    #     Mirrors the projection's derivation exactly; only computable once the
    #     header money checks passed.
    if not errors:
        header_total_tax = Decimal(str(payload.get("total_tax", "0") if payload.get("total_tax") is not None else "0"))
        header_subtotal = Decimal(
            str(payload.get("subtotal_price", "0") if payload.get("subtotal_price") is not None else "0")
        )
        header_total = Decimal(str(payload.get("total_price", "0") if payload.get("total_price") is not None else "0"))
        if header_total_tax > 0:
            revenue_amount = header_subtotal if header_subtotal > 0 else header_total - header_total_tax
            if revenue_amount > 0:
                # try/except (Codex round-14): individually valid values can
                # make the DIVISION itself trap — e.g. subtotal "1e-1000000"
                # passes the magnitude check but 1 / 1e-1000000 overflows the
                # Decimal context. An uncomputable ratio is exactly as
                # unstorable as an oversized one.
                try:
                    implied_rate = (header_total_tax / revenue_amount).quantize(Decimal("0.0001"))
                    rate_defect = implied_rate >= Decimal("10")
                    rate_text = str(implied_rate)
                except ArithmeticError:
                    rate_defect = True
                    rate_text = "uncomputable"
                if rate_defect:
                    errors.append(
                        _defect(
                            MALFORMED_MONEY,
                            "total_tax",
                            f"implied tax rate {rate_text} exceeds the storable TaxCode bound",
                        )
                    )

    # 3. Currency: absent ⇒ live default; present must satisfy the EVENT
    #    contract's currency rule (events/types.py currency_fields: exactly 3
    #    uppercase letters) — anything looser is unemittable: the event
    #    validation rejects it AFTER the row write, into the retryable loop.
    if "currency" in payload:
        currency = payload["currency"]
        if (
            not isinstance(currency, str)
            or len(currency) != 3
            or not currency.isalpha()
            or currency != currency.upper()
        ):
            errors.append(
                _defect(
                    MALFORMED_CURRENCY,
                    "currency",
                    f"currency is not a 3-letter uppercase ISO code: {_short(currency)}",
                )
            )

    # 4. transactions: any falsy value tolerated (the live payment-verification
    #    block falsy-guards its loop); a truthy value must be a list of dicts
    #    with finite amounts.
    if payload.get("transactions"):
        errors.extend(_dict_list_defects(payload["transactions"], "transactions", ("amount",)))

    # 5. shipping_lines: null/non-list crashes the live iteration.
    if "shipping_lines" in payload and payload["shipping_lines"] is not None:
        errors.extend(_dict_list_defects(payload["shipping_lines"], "shipping_lines", ("price",)))
    elif "shipping_lines" in payload:
        errors.append(_defect(MALFORMED_STRUCTURE, "shipping_lines", "shipping_lines is null"))

    # 6. line_items: null/non-list crashes the live iteration; sku must be a
    #    string or null; price finite; variant_id/product_id must be honest ids
    #    or null (they hit BigInteger lookups in Phase-1 preparation).
    if "line_items" in payload and payload["line_items"] is not None:
        line_items = payload["line_items"]
        errors.extend(_dict_list_defects(line_items, "line_items", ("price",)))
        if isinstance(line_items, list):
            for index, item in enumerate(line_items):
                if not isinstance(item, dict):
                    continue  # already reported
                sku = item.get("sku")
                if sku is not None and not isinstance(sku, str):
                    errors.append(
                        _defect(MALFORMED_STRUCTURE, f"line_items[{index}].sku", f"sku is not a string: {_short(sku)}")
                    )
                for id_field in ("variant_id", "product_id"):
                    id_value = item.get(id_field)
                    if id_value is not None and trustworthy_external_id(id_value) is None:
                        errors.append(
                            _defect(
                                MALFORMED_STRUCTURE,
                                f"line_items[{index}].{id_field}",
                                f"{id_field} is not an id: {_short(id_value)}",
                            )
                        )
    elif "line_items" in payload:
        errors.append(_defect(MALFORMED_STRUCTURE, "line_items", "line_items is null"))

    # 7. String-bound header fields the ShopifyOrder row must be able to store.
    #    name: absent ⇒ "" default; null crashes the NOT NULL CharField.
    if "name" in payload:
        name_defect = _bounded_text_defect(payload["name"], "name", 50, allow_none=False)
        if name_defect:
            errors.append(name_defect)
    if "order_number" in payload and payload["order_number"] is not None:
        # str()-coerced on BOTH the model and the event path — only length breaks it.
        number_defect = _bounded_text_defect(
            payload["order_number"], "order_number", 50, allow_none=True, require_str=False
        )
        if number_defect:
            errors.append(number_defect)
    if "financial_status" in payload:
        fs_defect = _bounded_text_defect(payload["financial_status"], "financial_status", 30, allow_none=False)
        if fs_defect:
            errors.append(fs_defect)

    # 8. Gateway extraction inputs (_extract_gateway): a truthy non-indexable
    #    payment_gateway_names crashes `gateways[0]`; a null fallback `gateway`
    #    crashes the NOT NULL CharField.
    gateway_names = payload.get("payment_gateway_names")
    if gateway_names:
        if not isinstance(gateway_names, list | str):
            errors.append(
                _defect(
                    MALFORMED_STRUCTURE,
                    "payment_gateway_names",
                    f"payment_gateway_names is not a list: {_short(gateway_names)}",
                )
            )
        else:
            gateway_defect = _bounded_text_defect(gateway_names[0], "payment_gateway_names[0]", 100, allow_none=False)
            if gateway_defect:
                errors.append(gateway_defect)
    elif "gateway" in payload:
        gateway_defect = _bounded_text_defect(payload["gateway"], "gateway", 100, allow_none=False)
        if gateway_defect:
            errors.append(gateway_defect)

    # 9. customer: null is the documented tolerated shape; a truthy non-dict
    #    crashes `customer.get(...)`. A null email inside the dict is ROUTINE
    #    (guest checkout) — normalized at the event build; a non-str non-null
    #    email is unemittable (customer_email: str at emission).
    customer = payload.get("customer")
    if customer and not isinstance(customer, dict):
        errors.append(_defect(MALFORMED_STRUCTURE, "customer", f"customer is not an object: {_short(customer)}"))
    elif isinstance(customer, dict):
        email = customer.get("email")
        if email is not None and not isinstance(email, str):
            errors.append(
                _defect(MALFORMED_STRUCTURE, "customer.email", f"customer email is not a string: {_short(email)}")
            )
        # customer.tags rides the projection's dimension enrichment
        # (_resolve_dimensions): a truthy non-str crashes .split() AFTER the
        # canonical order + event committed (Codex round-7).
        customer_tags = customer.get("tags")
        if customer_tags and not isinstance(customer_tags, str):
            errors.append(
                _defect(MALFORMED_STRUCTURE, "customer.tags", f"customer tags is not a string: {_short(customer_tags)}")
            )

    # 9b. Projection-consumed metadata (Codex round-7): the dimension
    #     enrichment (_resolve_dimensions, called UNGUARDED before every
    #     handler) reads these raw_payload fields AFTER the canonical order +
    #     event committed — a bad shape deterministically fails the projection
    #     instead of quarantining at ingress. Falsy values are skipped by the
    #     consumer and stay tolerated; str()-coerced fields (source_name,
    #     gateway names, shipping country/city, discount code VALUES) need no
    #     check.
    tags = payload.get("tags")
    if tags and not isinstance(tags, str):
        # tags_str.split(",") on a truthy non-str.
        errors.append(_defect(MALFORMED_STRUCTURE, "tags", f"tags is not a string: {_short(tags)}"))
    discount_codes = payload.get("discount_codes")
    if discount_codes:
        # discount_codes[0].get("code") — must be a list whose FIRST element is
        # an object (only [0] is read).
        if not isinstance(discount_codes, list):
            errors.append(
                _defect(
                    MALFORMED_STRUCTURE, "discount_codes", f"discount_codes is not a list: {_short(discount_codes)}"
                )
            )
        elif not isinstance(discount_codes[0], dict):
            errors.append(
                _defect(
                    MALFORMED_STRUCTURE,
                    "discount_codes[0]",
                    f"discount code entry is not an object: {_short(discount_codes[0])}",
                )
            )
    shipping_address = payload.get("shipping_address")
    if shipping_address and not isinstance(shipping_address, dict):
        # shipping.get(...) on a truthy non-dict (falsy takes the `or {}`).
        errors.append(
            _defect(
                MALFORMED_STRUCTURE,
                "shipping_address",
                f"shipping_address is not an object: {_short(shipping_address)}",
            )
        )
    referring_site = payload.get("referring_site")
    if referring_site and not isinstance(referring_site, str):
        # urlparse's failure is caught, but the fallback then runs
        # domain.upper() OUTSIDE the try — a truthy non-str crashes it.
        errors.append(
            _defect(MALFORMED_STRUCTURE, "referring_site", f"referring_site is not a string: {_short(referring_site)}")
        )

    # 10. created_at is RAW-stored into shopify_created_at when truthy (see
    #     _raw_stored_datetime_defect). updated_at is only parsed with a
    #     fallback (paid_date) and never stored raw — no constraint.
    date_defect = _raw_stored_datetime_defect(payload, "created_at")
    if date_defect:
        errors.append(date_defect)

    # 10b. Aggregate shipping bound (Codex round-3 P2): each shipping price can
    #      individually fit numeric(18,2) while their SUM overflows — the sum
    #      rides the event as total_shipping and becomes a SalesInvoiceLine
    #      unit_price in the projection, which would then deterministically
    #      fail to post a canonical order. Mirror the live aggregation exactly;
    #      only computable once the per-element checks passed.
    if not errors:
        shipping_total = Decimal("0")
        for shipping_line in payload.get("shipping_lines") or []:
            shipping_total += Decimal(str(shipping_line.get("price", "0")))
        if shipping_total.copy_abs() >= _MONEY_MAX:
            errors.append(
                _defect(
                    MALFORMED_MONEY,
                    "shipping_lines",
                    f"aggregate shipping amount exceeds the storable money magnitude: {shipping_total}",
                )
            )

    # 11. jsonb-unstorable values ANYWHERE (non-finite floats, NUL in strings/
    #     keys) poison the canonical raw_payload write. Runs LAST so a
    #     money-field NaN keeps its specific MALFORMED_MONEY primary code (this
    #     may add a duplicate entry for such fields — validation_errors
    #     deliberately records every detected defect).
    errors.extend(_jsonb_unstorable_defects(payload))

    return _verdict(errors, external_id, None)


def refund_aggregate_amount(payload) -> Decimal:
    """A5-PR2c: THE canonical refund aggregate — one pure helper shared by the
    validator's bounds below and by ``process_refund``'s amount, so the two can
    never drift into subtly different sums. Exact live semantics, unchanged:

    1. sum transaction ``amount`` only where ``kind == "refund"`` and
       ``status == "success"``;
    2. when that total is EXACTLY zero, fall back to
       ``sum(refund_line_items[].subtotal)``;
    3. no rounding, no alternative component treatment.

    Pure: no network, ORM, lock, settings or provider call. May be called only
    AFTER the per-element structural/money checks proved every relevant value
    parseable (``Decimal(str(...))`` on an unvalidated value would raise)."""
    total = Decimal("0")
    for txn in payload.get("transactions") or []:
        if txn.get("kind") == "refund" and txn.get("status") == "success":
            total += Decimal(str(txn.get("amount", "0")))
    if total == 0:
        for line in payload.get("refund_line_items") or []:
            total += Decimal(str(line.get("subtotal", "0")))
    return total


def validate_refund_payload(payload) -> PayloadVerdict:
    """Validate a refunds/create payload (webhook + poller backfill share it)."""
    if not isinstance(payload, dict):
        return _verdict(
            [_defect(MALFORMED_STRUCTURE, "payload", f"payload is not an object: {_short(payload)}")], None, None
        )

    errors: list[dict] = []

    external_id = trustworthy_external_id(payload.get("id"))
    if external_id is None:
        if payload.get("id") in (None, "", 0):
            errors.append(_defect(MISSING_EXTERNAL_ID, "id", "refund id is missing"))
        else:
            errors.append(
                _defect(MISSING_EXTERNAL_ID, "id", f"refund id is not trustworthy: {_short(payload.get('id'))}")
            )

    parent_external_id = trustworthy_external_id(payload.get("order_id"))
    if parent_external_id is None:
        if payload.get("order_id") in (None, "", 0):
            errors.append(_defect(MISSING_EXTERNAL_ID, "order_id", "order id is missing"))
        else:
            errors.append(
                _defect(
                    MISSING_EXTERNAL_ID, "order_id", f"order id is not trustworthy: {_short(payload.get('order_id'))}"
                )
            )

    # transactions: the refund loop iterates without a falsy guard — null/non-list
    # crashes it; elements are dicts with finite amounts.
    if "transactions" in payload:
        if payload["transactions"] is None:
            errors.append(_defect(MALFORMED_STRUCTURE, "transactions", "transactions is null"))
        else:
            errors.extend(_dict_list_defects(payload["transactions"], "transactions", ("amount",)))

    # refund_line_items: same iteration shape (the zero-amount fallback path).
    if "refund_line_items" in payload:
        if payload["refund_line_items"] is None:
            errors.append(_defect(MALFORMED_STRUCTURE, "refund_line_items", "refund_line_items is null"))
        else:
            errors.extend(_dict_list_defects(payload["refund_line_items"], "refund_line_items", ("subtotal",)))

    # Restock shape (Codex round-4 P2): the projection's restock consumers read
    # more than `subtotal` — for a return/cancel element,
    # `_refund_has_restocked_goods` / `_handle_refund_restock` compare quantity
    # with `> 0` / `<= 0` (a string or null raises TypeError) and a positive
    # quantity makes them `.get` into line_item (a non-object raises) — all
    # AFTER the canonical refund + event committed, deterministically failing
    # the projection. Non-restock elements keep today's tolerance (the
    # consumers skip them before reading anything else).
    if isinstance(payload.get("refund_line_items"), list):
        for index, element in enumerate(payload["refund_line_items"]):
            if not isinstance(element, dict):
                continue  # already reported by the shape check
            if element.get("restock_type") not in ("return", "cancel"):
                continue
            quantity = element.get("quantity", 0)
            if isinstance(quantity, bool) or not isinstance(quantity, int | float):
                errors.append(
                    _defect(
                        MALFORMED_STRUCTURE,
                        f"refund_line_items[{index}].quantity",
                        f"restock quantity is not a number: {_short(quantity)}",
                    )
                )
                continue
            if quantity > 0 and not isinstance(element.get("line_item", {}), dict):
                errors.append(
                    _defect(
                        MALFORMED_STRUCTURE,
                        f"refund_line_items[{index}].line_item",
                        f"restock line_item is not an object: {_short(element.get('line_item'))}",
                    )
                )

    # note: null is a ROUTINE Shopify shape (the poller's GraphQL mapper already
    # normalizes it with `r.get("note") or ""`; process_refund now applies the
    # same normalization at the shared ingress) — tolerated. Only an over-long
    # value is unstorable in the 255-char column.
    if "note" in payload:
        note_defect = _bounded_text_defect(payload["note"], "note", 255, allow_none=True)
        if note_defect:
            errors.append(note_defect)

    # created_at is RAW-stored into ShopifyRefund.shopify_created_at when truthy
    # (all three creation sites use `refund_date_str or now()`).
    date_defect = _raw_stored_datetime_defect(payload, "created_at")
    if date_defect:
        errors.append(date_defect)

    # Aggregate bound (Codex round-1 P2): each amount can individually fit
    # numeric(18,2) while their SUM overflows at the ShopifyRefund.amount write
    # (the same aggregate rides the zero-marker branch too) — a post-validation
    # overflow would loop instead of rejecting. The aggregate itself is the
    # shared canonical helper (refund_aggregate_amount — the exact sum
    # process_refund posts). Only computable once the per-element checks passed.
    #
    # A5-PR2c (founder decision, assessment finding C4): a storable aggregate
    # BELOW ZERO is invalid provider data from which no honest refund can be
    # constructed — the same PERMANENT class as the other MALFORMED_MONEY
    # defects. It previously produced a ShopifyRefund(status=ERROR) row that
    # was invisible to /finance/exceptions and /_health/alerts and whose
    # refund-id dedup foreclosed the corrected redelivery; as evidence it
    # pages, lists, re-sights, and is superseded by a corrected delivery.
    # Individual negative components stay tolerated when the exact aggregate
    # is non-negative — only the terminal negative TOTAL rejects.
    if not errors:
        total = refund_aggregate_amount(payload)
        if total.copy_abs() >= _MONEY_MAX:
            errors.append(
                _defect(
                    MALFORMED_MONEY,
                    "transactions",
                    f"aggregate refund amount exceeds the storable money magnitude: {total}",
                )
            )
        elif total < 0:
            errors.append(
                _defect(
                    MALFORMED_MONEY,
                    "refund_amount",
                    f"aggregate refund amount must be non-negative: {total}",
                )
            )

    # jsonb-unstorable values anywhere poison the canonical raw_payload write —
    # same rule as the order validator (runs last; see _jsonb_unstorable_defects).
    errors.extend(_jsonb_unstorable_defects(payload))

    return _verdict(errors, external_id, parent_external_id)
