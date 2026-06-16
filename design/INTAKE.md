# Paytek Intake — Developer Guide

Conversational merchant onboarding. Two LLM calls per user turn: one structured extraction, one streamed reply.

---

## Key files

| File | Purpose |
|---|---|
| `schema.py` | `DraftUpdates` — the Pydantic model the extraction LLM populates |
| `prompts.py` | `build_extraction_system_prompt()`, `reply_system_prompt()`, `INTAKE_REQUIRED_LABELS` |
| `session.py` | State machine (`collecting → offering_optional → confirming → done`), validation, sanitization |
| `endpoints.py` | HTTP handlers, streaming |
| `src/dbtek/domain/constants.py` | `MerchantJsonFields.required()` — drives which fields must be collected |
| `src/dbtek/domain/enums.py` | All enum definitions — single source of truth for allowed values |

---

## Current required fields

Defined in `MerchantJsonFields.required()`. The session blocks submission until all are present in the draft.

| Field | What the assistant asks for |
|---|---|
| `account_name` | Business / account name |
| `enterprise_identifier` | Org internal ID (any alphanumeric format) |
| `merchant_type` | SMB \| Mid-Market \| Enterprise \| Aggregator |
| `location_address` | Street, city, state code, postal code, country code |
| `contact_info` | Contact email address |
| `payout_account` | Account number and routing number |
| `account_data` | Business profile, description, merchant country, currency codes |
| `projected_monthly_volume` | Projected monthly volume (nested under `account_data.merchant_account_setup`) |

---

## Nested model structure (`DraftUpdates`)

```
DraftUpdates
├── account_name, enterprise_identifier, merchant_type          # scalars
├── processing_model, primary_platform_id                       # optional scalars
├── contact_info         (_ContactInfo)
│   └── email, phone
├── location_address     (_LocationAddress)
│   └── line1, city, state_or_province_code, postal_code, country_code
├── payout_account       (_PayoutAccount)
│   └── account_number, routing_number, account_type, owner_name, financial_institution_name
└── account_data         (_AccountData)
    ├── merchant_account_setup       (_MerchantAccountSetup)
    │   └── business_profile, business_description, merchant_country, projected_monthly_volume ★
    └── payment_methods_and_currency (_PaymentMethodsAndCurrency)
        └── point_of_sale_currency_code, settlement_currency_code
```

The extraction prompt is **generated** from this structure — field names and nested subfields are introspected at import time from `DraftUpdates.model_fields`. You do not edit the prompt text directly.

---

## Enum constraints (`_FIELD_ENUM_MAP` in `prompts.py`)

Enum values are derived from `src/dbtek/domain/enums.py` at generation time.

| Field path | Enum class |
|---|---|
| `merchant_type` | `MerchantType` |
| `processing_model` | `ProcessingModel` |
| `primary_platform_id` | `PrimaryPlatformId` |
| `payout_account.account_type` | `AccountType` |

---

## Sample intake prompt (all required fields in one message)

Paste this into the chat to pre-fill every required field and skip straight to the confirm step:

```
My business is Acme Payments LLC, enterprise ID ENT-001, merchant type SMB.
Address: 100 Main St, Austin, TX 78701, US.
Contact email: ops@acme.com.
Payout account number 987654321, routing number 021000021.
Business profile: tech_saas, description: "Payment processing platform", merchant country: US,
POS currency: USD, settlement currency: USD.
Projected monthly volume: 100000.
```

---

## How to add a new field

### Simple scalar (e.g. `tax_id`)

1. **`src/dbtek/domain/constants.py`** — add constant; add to `required()` if mandatory
2. **`schema.py`** — add `tax_id: Optional[str] = None` to `DraftUpdates`
3. **`prompts.py`** — add to `_FIELD_ENUM_MAP` if enum-constrained; add to `INTAKE_REQUIRED_LABELS` if required

The extraction prompt updates automatically. Nothing else to touch.

### Nested object (e.g. `bank_info`)

Same steps, plus:

1. **`schema.py`** — define a new `_BankInfo(BaseModel)` with its subfields; add `bank_info: Optional[_BankInfo] = None` to `DraftUpdates`

Nested subfield names appear in the extraction prompt automatically via `_nested_subfields()`.

2. **`prompts.py`** — if a subfield has enum constraints, register the dotted path:
```python
_FIELD_ENUM_MAP = {
    ...
    "bank_info.account_category": BankAccountCategory,
}
```

### New enum (e.g. `PrimaryPlatformId` style)

1. **`src/dbtek/domain/enums.py`** — add the enum class
2. **`_FIELD_ENUM_MAP`** — register the field → enum mapping
3. Any tool schema that uses the values should derive them via `[e.value for e in MyEnum]`

---

## What you do NOT need to touch

- `extraction_system_prompt` — auto-generated; never edit the string directly
- `build_create_merchant_prompt()` — serializes the full draft; no changes needed for new fields
- `session.py` `_missing()` — reads `MerchantJsonFields.required()` dynamically
- `offering_optional` enum values in `reply_system_prompt()` — derived from enum classes

Only edit `session.py` if the new field needs custom **sanitization** (e.g. normalizing casing like `account_type` does in `_sanitize_draft()`), or if it is a **nested mandatory field** — `_missing()` only checks top-level fields from `MerchantJsonFields.required()`, so a nested required field (like `projected_monthly_volume` ★) needs its own path check appended after that loop.
