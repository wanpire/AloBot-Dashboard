# UI guide

The contract for every screen still to be migrated. The review queue
(`app/web/templates/payments.html`) is the worked example; when this file and
that file disagree, that file is right and this one needs fixing.

## The shape of a screen

```jinja
{% extends "base_v2.html" %}
{% import "_ui.html" as ui with context %}

{% block content %}
{% call ui.card("عنوان") %}
  …
{% endcall %}
{% endblock %}
```

`with context` is not optional. The macros use the app's filters
(`fa_number`, `toman`, `jalali_dt`) and without it they render empty.

Available blocks: `page_title`, `page_heading`, `page_actions`, `content`,
`extra_head`, `extra_scripts`.

## Which macro, when

| You are showing | Use | Not |
|---|---|---|
| A page's title and its top-right actions | `page_header` with a `{% call %}` body | an `<h1>` of your own |
| Any panel of content | `card` | a bare `<div class="card">` |
| A headline number | `stat_tile` | a card with a big `<div>` in it |
| Rows of records | `table` + `row` + `cell` + `actions_cell` | `<table class="table-responsive">` |
| A status, a queue, a disposition | `state_badge` | a coloured `<span>` |
| Anything clickable | `btn` | `<button class="btn …">` |
| One input with its label | `field` | `<label>` + `<input>` |
| Why a submit failed | `form_errors` | an alert you wrote |
| A list with nothing in it | `empty_state` | a row saying "خالی" |
| Something just happened | `toast` | an alert you wrote |
| A control that must not be pressed by accident | `confirm_button` | a JavaScript `confirm()` |
| More rows than fit | `pagination` | hand-written prev/next links |

**No screen writes a framework class.** No `btn-primary`, no `badge bg-red`,
no `col-md-6` inside a component. Layout classes on your own wrapper divs are
fine; component classes are the library's business, so that when Tabler
renames one it moves in a single file. A test checks the migrated screens for
escaped `btn-` and `badge bg-`.

## Attributes: how to attach anything

Jinja calls cannot take `**{"hx-get": …}`, so every macro takes two doors:

```jinja
{{ ui.btn("تایید", tone="primary", data_testid="approve") }}          {# underscores become dashes #}
{{ ui.btn("تایید", attrs={"hx-post": "/x", "hx-target": "#row-7"}) }}  {# anything at all #}
{{ ui.btn("تایید", class_="w-100") }}                                  {# adds to the macro's own classes #}
```

Use `attrs` for `hx-*`, and keywords for `data-testid`, `aria-*`, `id`, `dir`.
Both land on the element the macro renders, never on a wrapper.

## HTMX and Bootstrap together

There is no HTMX in this panel **yet**: no `hx-*` attribute and no route
returns a partial. When that changes, these are the rules.

- **Dropdowns, collapses, modals, offcanvas survive a swap.** Bootstrap binds
  them with delegated events, so markup HTMX brings in works without help.
- **Tooltips and popovers do not.** They are instantiated per element. The
  shell already reinstates them on every swap through `htmx.onLoad`; do not
  write your own initialiser.
- **A swap target keeps its real `id`.** `data-testid` is additive and never
  a replacement: tests select on the testid, HTMX selects on the id, and the
  two must not be conflated.
- **A partial must render standalone and inside the page.** Write it so it
  begins at the element the swap replaces, with no assumption about what wraps
  it, and include it from the full page rather than duplicating it.

## Right to left

The panel is Persian. Every direction is logical, never physical.

| Use | Never |
|---|---|
| `ms-*` / `me-*` | `ml-*` / `mr-*` |
| `ps-*` / `pe-*` | `pl-*` / `pr-*` |
| `text-start` / `text-end` | `text-left` / `text-right` |
| `border-start` / `border-end` | `border-left` / `border-right` |
| `offcanvas-start` (the sidebar's side) | `offcanvas-right` |

A test rejects hardcoded sides in `_ui.html`. Latin strings - a card number, a
username, a reference, a status constant - get `dir="ltr"` on their own
element so they do not scramble the sentence around them.

## Numbers and dates

Never print a raw number or a raw timestamp.

| Value | Filter |
|---|---|
| Any integer an operator reads | `| fa_number` |
| Rial stored in this project's tables | `| toman` |
| Toman from AloBot's tables | `| toman_amount` |
| A timestamp | `| jalali_dt` |
| A telegram id or other bare id | `| string | fa` |

## Phones

The panel is used on a phone. The `table` macro stacks rows into cards below
md and each cell shows its column name, which is why `cell` takes the header
text. Nothing scrolls sideways: a browser test walks every section at 390px
and fails on a single pixel of horizontal overflow.

## Naming

- `data-testid`: kebab-case, scoped by screen. `review-queue-row`,
  `payment-approve-btn`, `device-rotate-confirm`.
- Ids used as swap targets: kebab-case with the record's id, `claim-7`.
- Own CSS classes: prefixed `ui-`, defined in `app/web/static/shell.css`, and
  only where Tabler genuinely has nothing. There are three so far.

## What a migration may not change

The route, the query, the ordering, the pagination arithmetic, any form
action, any field name, any role guard, any operator-facing word, and any
existing `data-testid`. If the redesign seems to need one of those, it is a
separate change and it needs its own go-ahead.

The way to prove it: render the screen before and after with the same data and
compare the form actions, the field names, the testids, the count of Persian
digits and the count of Jalali dates. For the review queue those were 14, 5,
5, 116 and 4, identical on both sides.
