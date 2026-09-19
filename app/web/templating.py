from __future__ import annotations

from pathlib import Path
from typing import Any

from fastapi import Request
from fastapi.templating import Jinja2Templates

from app.core.config import get_settings
from app.web import format as fmt
from app.web.nav import page_label, visible_nav

templates = Jinja2Templates(directory=str(Path(__file__).parent / "templates"))
templates.env.filters.update(
    {
        "fa": fmt.fa_digits,
        "fa_number": fmt.fa_number,
        "toman": fmt.toman_from_rial,
        "jalali": fmt.jalali_date,
        "jalali_dt": fmt.jalali_datetime,
        "toman_amount": lambda v: f"{fmt.fa_number(int(v))} تومان" if v is not None else "—",
    }
)


def render(request: Request, name: str, status_code: int = 200, **context: Any):
    operator = getattr(request.state, "operator", None)
    settings = get_settings()
    base = {
        "operator": operator,
        "nav": visible_nav(operator.role) if operator else [],
        "page_id": context.pop("page_id", None),
        "app_version": settings.app_version,
        "env_name": settings.env_name.value,
        "page_label": page_label,
    }
    return templates.TemplateResponse(request, name, {**base, **context}, status_code=status_code)
