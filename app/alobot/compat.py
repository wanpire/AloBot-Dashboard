"""Does the AloBot database this project is looking at have the columns this
project reads? Recorded for the AloBot commit in `ALOBOT_COMMIT`; when AloBot
adds a migration, the pin moves and this list is re-checked.

Only columns this project READS are listed. AloBot may add columns freely;
what breaks a page is a column that went away or was renamed, and this is
what turns that into a named boot-time message instead of a 500 at 3am.
"""

from __future__ import annotations

from sqlalchemy import MetaData

REQUIRED_COLUMNS: dict[str, frozenset[str]] = {
    "payments": frozenset(
        {
            "id", "telegram_id", "method", "status", "auto_approved", "purpose", "target_username",
            "amount", "group_name", "service_id", "ibsng_username", "receipt_file_id",
            "discount_code_id", "original_amount", "invoice_number", "created_at", "resolved_at",
        }
    ),
    "vpn_users": frozenset(
        {"id", "telegram_id", "ibsng_username", "ibsng_group", "service_id", "is_trial", "expires_at", "created_at"}
    ),
    "bot_users": frozenset({"id", "telegram_id", "username", "first_seen_at", "is_blocked"}),
    "admin_users": frozenset({"id", "telegram_id", "level", "created_at"}),
    "resellers": frozenset({"id", "telegram_id", "balance", "commission_percent", "created_at"}),
    "services": frozenset(
        {"id", "title", "category", "location_id", "duration_months", "user_count", "group_name", "price", "is_active", "sort_order"}
    ),
    "service_locations": frozenset({"id", "title", "flag_emoji", "is_active", "sort_order"}),
    "groups": frozenset({"id", "ibsng_group_id", "name", "price", "is_trial", "trial_category", "synced_at"}),
    "discount_codes": frozenset(
        {"id", "code", "percent", "usage_limit", "used_count", "categories", "is_active", "is_public", "created_at"}
    ),
    "discount_code_usages": frozenset(
        {"id", "discount_code_id", "code", "percent", "payment_id", "telegram_id", "original_amount", "amount", "used_at"}
    ),
    "tutorial_platforms": frozenset({"id", "label", "is_active", "sort_order"}),
    "tutorial_protocols": frozenset({"id", "label", "is_active", "sort_order"}),
    "tutorial_guides": frozenset(
        {"id", "platform_id", "protocol_id", "category", "location_id", "body_html", "media_type", "is_active", "sort_order"}
    ),
    "download_links": frozenset({"id", "platform_id", "protocol_id", "url"}),
    "openvpn_profiles": frozenset({"id", "name", "category", "location_id", "platform_id", "file_type", "is_active"}),
    "app_config": frozenset({"key", "value"}),
}


def check(metadata: MetaData, required: dict[str, frozenset[str]] | None = None) -> list[str]:
    """Problems as sentences naming table.column; empty means compatible."""
    required = REQUIRED_COLUMNS if required is None else required
    problems: list[str] = []
    for table_name, columns in required.items():
        table = metadata.tables.get(table_name)
        if table is None:
            problems.append(f"AloBot table {table_name} is missing")
            continue
        present = {c.name for c in table.columns}
        for column in sorted(columns - present):
            problems.append(f"AloBot column {table_name}.{column} is missing")
    return problems
