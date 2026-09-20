# AloBot migration draft — discount code expiry and per-user limit

**Status: written down, deliberately NOT applied.** This belongs to Phase 7
(`docs/PLAN.md`), where changes to AloBot's schema get their own go-ahead,
their own run of AloBot's test suite, and a rollback path. Nothing in this
project's Phase 5 alters AloBot's schema — the dashboard's discount editor
manages only the columns AloBot already has.

## Why it is wanted

AloBot's `discount_codes` has `usage_limit` (a total across everybody) and
`is_active`, and nothing else that bounds a code. Two gaps show up as soon as
codes are managed from a screen rather than by hand:

1. **No expiry.** A campaign code stays live until someone remembers to
   switch it off. Every one of the reference shop's 33 production codes had
   an expiry; ours cannot express one.
2. **No per-customer limit.** `usage_limit` is shared, so "one per customer"
   cannot be said at all — a single customer can spend the whole quota.

## The change

```sql
ALTER TABLE discount_codes
    ADD COLUMN expires_at     timestamptz NULL,
    ADD COLUMN per_user_limit integer     NULL CHECK (per_user_limit IS NULL OR per_user_limit >= 1);
```

Both nullable with no default, so every existing row keeps exactly today's
behaviour: no expiry, no per-customer cap.

## What AloBot must change alongside it

`app/services/discounts.py`'s `validate_discount_code` gains two checks,
after the existing "active / within usage limit / applies to category" ones:

- `expires_at IS NOT NULL AND expires_at <= now()` → refuse with a Persian
  reason of its own (the existing `DiscountCodeInvalidError` carries the
  message the buyer sees).
- `per_user_limit IS NOT NULL` → count that buyer's rows in
  `discount_code_usages` for this code and refuse at the limit. Count the
  usage rows, not a column: the reference project's counter column drifted
  from its own usage table in production.

`_is_within_usage_limit` is the natural home for the second check, beside the
total limit it already enforces.

## Order on the day

1. AloBot migration + `validate_discount_code` changes, with tests, in
   AloBot's repo — its own PR and go-ahead.
2. Then flip this project's `discounts.expiry_enabled` feature flag so the
   dashboard's create/edit form shows the two fields and writes them.

Until step 1 lands, the form hides both fields rather than accepting a value
it cannot store.
