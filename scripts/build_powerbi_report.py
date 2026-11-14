"""Generate the PBIR report pages and visuals for the Olist Power BI project.

The PBIR format stores one JSON file per page and per visual. Hand-writing twenty of those is
unreviewable, so the layout lives here as data and the files are generated. Re-run after editing
the layout:

    python scripts/build_powerbi_report.py

Then validate:

    python scripts/validate_pbir.py powerbi/Olist.Report

Every visual is specified in ``docs/powerbi-dashboards.md``; the measures it references are in
``docs/powerbi-measures.md`` and are verified against BigQuery by
``scripts/validate_powerbi_measures.py``.
"""

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

REPORT = Path("powerbi/Olist.Report")
DEFINITION = REPORT / "definition"
PAGES = DEFINITION / "pages"

SCHEMA_VISUAL = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"
    "visualContainer/1.0.0/schema.json"
)
SCHEMA_PAGE = (
    "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/page/1.0.0/schema.json"
)

#: The temporal tails are unusable: 329 orders in 2016 and 20 in Sep-Oct 2018, against ~6,300 in
#: a normal month. Every trend visual is filtered to the dense window.
DENSE_WINDOW_START = "2017-01-01T00:00:00"
DENSE_WINDOW_END = "2018-08-31T23:59:59"


# --------------------------------------------------------------------------------------------
# Expression helpers
# --------------------------------------------------------------------------------------------


def lit(value: str) -> dict[str, Any]:
    """A literal expression. Strings must already be single-quoted."""
    return {"expr": {"Literal": {"Value": value}}}


def measure(entity: str, prop: str) -> dict[str, Any]:
    return {
        "field": {
            "Measure": {
                "Expression": {"SourceRef": {"Entity": entity}},
                "Property": prop,
            }
        },
        "queryRef": f"{entity}.{prop}",
        "nativeQueryRef": prop,
    }


def column(entity: str, prop: str, *, active: bool = False) -> dict[str, Any]:
    projection: dict[str, Any] = {
        "field": {
            "Column": {
                "Expression": {"SourceRef": {"Entity": entity}},
                "Property": prop,
            }
        },
        "queryRef": f"{entity}.{prop}",
        "nativeQueryRef": prop,
    }
    if active:
        projection["active"] = True
    return projection


def solid(color: str) -> dict[str, Any]:
    return {"solid": {"color": lit(f"'{color}'")}}


def container_style(*, background: str = "#FFFFFF", radius: int = 6) -> dict[str, Any]:
    return {
        "background": [
            {
                "properties": {
                    "show": lit("true"),
                    "color": solid(background),
                    "transparency": lit("0D"),
                }
            }
        ],
        "border": [
            {
                "properties": {
                    "show": lit("true"),
                    "color": solid("#E1E4E8"),
                    "radius": lit(f"{radius}D"),
                }
            }
        ],
        "title": [
            {
                "properties": {
                    "show": lit("true"),
                    "fontSize": lit("12D"),
                    "fontColor": solid("#1F3864"),
                    "alignment": lit("'left'"),
                }
            }
        ],
    }


# --------------------------------------------------------------------------------------------
# Visual builders
# --------------------------------------------------------------------------------------------


def card(name: str, title: str, entity: str, prop: str, x: int, y: int, w: int = 290) -> dict:
    return {
        "$schema": SCHEMA_VISUAL,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "height": 100, "width": w, "tabOrder": 0},
        "visual": {
            "visualType": "card",
            "query": {"queryState": {"Values": {"projections": [measure(entity, prop)]}}},
            "objects": {
                "labels": [
                    {
                        "properties": {
                            "fontSize": lit("28D"),
                            "color": solid("#1F3864"),
                            "bold": lit("true"),
                        }
                    }
                ],
                "categoryLabels": [
                    {
                        "properties": {
                            "show": lit("true"),
                            "fontSize": lit("11D"),
                            "color": solid("#605E5C"),
                        }
                    }
                ],
            },
            "visualContainerObjects": container_style(),
            "drillFilterOtherVisuals": True,
        },
    }


def line_chart(
    name: str,
    title: str,
    category: tuple[str, str],
    series: list[tuple[str, str]],
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    sort_by: tuple[str, str] | None = None,
) -> dict:
    projections = [column(*category)]
    if sort_by:
        projections.append(column(*sort_by))
    return {
        "$schema": SCHEMA_VISUAL,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "height": h, "width": w, "tabOrder": 0},
        "visual": {
            "visualType": "lineChart",
            "query": {
                "queryState": {
                    "Category": {"projections": projections},
                    "Y": {"projections": [measure(*s) for s in series]},
                },
                "sortDefinition": {
                    "sort": [
                        {
                            "field": {
                                "Column": {
                                    "Expression": {"SourceRef": {"Entity": category[0]}},
                                    "Property": category[1],
                                }
                            },
                            "direction": "Ascending",
                        }
                    ]
                },
            },
            "objects": {
                "legend": [
                    {"properties": {"show": lit("true"), "position": lit("'Top'")}}
                ],
                "categoryAxis": [
                    {"properties": {"show": lit("true"), "fontSize": lit("10D")}}
                ],
                "valueAxis": [
                    {"properties": {"show": lit("true"), "fontSize": lit("10D")}}
                ],
            },
            "visualContainerObjects": container_style(),
            "drillFilterOtherVisuals": True,
        },
    }


def bar_chart(
    name: str,
    title: str,
    category: tuple[str, str],
    value: tuple[str, str],
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    top_n: int | None = None,
    horizontal: bool = True,
) -> dict:
    visual: dict[str, Any] = {
        "visualType": "barChart" if horizontal else "columnChart",
        "query": {
            "queryState": {
                "Category": {"projections": [column(*category)]},
                "Y": {"projections": [measure(*value)]},
            },
            "sortDefinition": {
                "sort": [
                    {
                        "field": {
                            "Measure": {
                                "Expression": {"SourceRef": {"Entity": value[0]}},
                                "Property": value[1],
                            }
                        },
                        "direction": "Descending",
                    }
                ]
            },
        },
        "objects": {
            "categoryAxis": [
                {"properties": {"show": lit("true"), "fontSize": lit("10D")}}
            ],
            "valueAxis": [{"properties": {"show": lit("true"), "fontSize": lit("10D")}}],
            "labels": [{"properties": {"show": lit("false")}}],
        },
        "visualContainerObjects": container_style(),
        "drillFilterOtherVisuals": True,
    }
    if top_n is not None:
        visual["query"]["queryState"]["Category"]["projections"][0]["nativeQueryRef"] = (
            f"{category[1]} (Top {top_n})"
        )
    return {
        "$schema": SCHEMA_VISUAL,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "height": h, "width": w, "tabOrder": 0},
        "visual": visual,
    }


def slicer(
    name: str,
    title: str,
    entity: str,
    prop: str,
    x: int,
    y: int,
    w: int,
    h: int,
    *,
    mode: str = "Dropdown",
) -> dict:
    return {
        "$schema": SCHEMA_VISUAL,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "height": h, "width": w, "tabOrder": 0},
        "visual": {
            "visualType": "slicer",
            "query": {"queryState": {"Values": {"projections": [column(entity, prop)]}}},
            "objects": {
                "data": [{"properties": {"mode": lit(f"'{mode}'")}}],
                "header": [
                    {
                        "properties": {
                            "show": lit("true"),
                            "fontSize": lit("11D"),
                            "fontColor": solid("#1F3864"),
                        }
                    }
                ],
            },
            "visualContainerObjects": container_style(),
            "drillFilterOtherVisuals": True,
        },
    }


def text_box(name: str, text: str, x: int, y: int, w: int, h: int, size: int = 20) -> dict:
    return {
        "$schema": SCHEMA_VISUAL,
        "name": name,
        "position": {"x": x, "y": y, "z": 1000, "height": h, "width": w, "tabOrder": 0},
        "visual": {
            "visualType": "textbox",
            "objects": {
                "general": [
                    {
                        "properties": {
                            "paragraphs": [
                                {
                                    "textRuns": [
                                        {
                                            "value": text,
                                            "textStyle": {
                                                "fontSize": f"{size}pt",
                                                "fontWeight": "bold",
                                                "color": "#1F3864",
                                            },
                                        }
                                    ]
                                }
                            ]
                        }
                    }
                ]
            },
            "visualContainerObjects": {
                "background": [{"properties": {"show": lit("false")}}],
                "border": [{"properties": {"show": lit("false")}}],
            },
        },
    }


# --------------------------------------------------------------------------------------------
# Page definitions
# --------------------------------------------------------------------------------------------


def page_executive() -> list[dict]:
    return [
        text_box("txtExecTitle", "Executive Overview", 20, 16, 700, 50, 22),
        card("visTotalRevenue", "Total Revenue", "fact_orders", "Total Revenue", 20, 80),
        card("visTotalOrders", "Total Orders", "fact_orders", "Total Orders", 330, 80),
        card("visAOV", "Average Order Value", "fact_orders", "Average Order Value", 640, 80),
        card(
            "visFreightShare",
            "Freight Share of Revenue",
            "fact_orders",
            "Freight Share of Revenue",
            950,
            80,
        ),
        line_chart(
            "visRevenueTrend",
            "Monthly revenue trend",
            ("dim_date", "year_month"),
            [("fact_orders", "Total Revenue"), ("fact_orders", "Revenue 3M Rolling")],
            20,
            200,
            620,
            260,
            sort_by=("dim_date", "year_month_sort"),
        ),
        bar_chart(
            "visRevenueByCategory",
            "Revenue by category (top 15)",
            ("dim_products", "product_category"),
            ("fact_order_items", "Category Revenue"),
            660,
            200,
            600,
            260,
            top_n=15,
        ),
        bar_chart(
            "visRevenueByState",
            "Revenue by state",
            ("dim_customers", "customer_state"),
            ("fact_orders", "Total Revenue"),
            20,
            480,
            620,
            220,
        ),
        line_chart(
            "visRevenueVsFreight",
            "Revenue vs freight",
            ("dim_date", "year_month"),
            [("fact_orders", "Total Revenue"), ("fact_orders", "Total Freight")],
            660,
            480,
            600,
            220,
            sort_by=("dim_date", "year_month_sort"),
        ),
        slicer("visSlicerYear", "Year", "dim_date", "year", 20, 200, 0, 0),
    ]


def page_logistics() -> list[dict]:
    return [
        text_box("txtLogTitle", "Logistics Performance", 20, 16, 700, 50, 22),
        card("visLateRate", "Late Rate %", "fact_orders", "Late Rate %", 20, 80),
        card("visLateOrders", "Late Orders", "fact_orders", "Late Orders", 330, 80),
        card(
            "visAvgDeliveryDays",
            "Average Delivery Days",
            "fact_orders",
            "Average Delivery Days",
            640,
            80,
        ),
        card(
            "visAvgDelayDays",
            "Average Delay (late only)",
            "fact_orders",
            "Average Delay Days (late only)",
            950,
            80,
        ),
        line_chart(
            "visLateRateTrend",
            "Late rate trend",
            ("dim_date", "year_month"),
            [("fact_orders", "Late Rate %"), ("fact_orders", "Late Rate 3M Rolling")],
            20,
            200,
            620,
            260,
            sort_by=("dim_date", "year_month_sort"),
        ),
        bar_chart(
            "visLateRateByState",
            "Late rate by customer state",
            ("dim_customers", "customer_state"),
            ("fact_orders", "Late Rate %"),
            660,
            200,
            600,
            260,
        ),
        bar_chart(
            "visLateRateByCategory",
            "Late rate by category",
            ("dim_products", "product_category"),
            ("fact_order_items", "Category Late Rate %"),
            20,
            480,
            620,
            220,
            top_n=15,
        ),
        bar_chart(
            "visDeliveryDaysByState",
            "Average delivery days by state",
            ("dim_customers", "customer_state"),
            ("fact_orders", "Average Delivery Days"),
            660,
            480,
            600,
            220,
        ),
    ]


def page_customer() -> list[dict]:
    return [
        text_box("txtCustTitle", "Customer Intelligence", 20, 16, 700, 50, 22),
        card("visUniqueCustomers", "Unique Customers", "fact_orders", "Unique Customers", 20, 80),
        card(
            "visRepeatRate",
            "Repeat Purchase Rate %",
            "fact_orders",
            "Repeat Purchase Rate %",
            330,
            80,
        ),
        card(
            "visAvgReview",
            "Average Review Score",
            "fact_orders",
            "Average Review Score",
            640,
            80,
        ),
        card("visLowScoreRate", "Low Score Rate %", "fact_orders", "Low Score Rate %", 950, 80),
        bar_chart(
            "visReviewDistribution",
            "Review score distribution",
            ("fact_orders", "review_score"),
            ("fact_orders", "Total Orders"),
            20,
            200,
            620,
            260,
            horizontal=False,
        ),
        bar_chart(
            "visRevenuePerCustomerByState",
            "Revenue per customer by state",
            ("dim_customers", "customer_state"),
            ("fact_orders", "Revenue per Customer"),
            660,
            200,
            600,
            260,
        ),
        bar_chart(
            "visLowScoreByCategory",
            "Low score orders by category",
            ("dim_products", "product_category"),
            ("fact_order_items", "Category Items"),
            20,
            480,
            620,
            220,
            top_n=15,
        ),
        bar_chart(
            "visOrdersPerCustomer",
            "Orders per customer",
            ("dim_customers", "customer_state"),
            ("fact_orders", "Orders per Customer"),
            660,
            480,
            600,
            220,
        ),
    ]


PAGE_SPECS: list[tuple[str, str, list[dict]]] = [
    ("pageExecutive", "Executive Overview", page_executive()),
    ("pageLogistics", "Logistics Performance", page_logistics()),
    ("pageCustomer", "Customer Intelligence", page_customer()),
]


# --------------------------------------------------------------------------------------------
# Writers
# --------------------------------------------------------------------------------------------


def write_json(path: Path, payload: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def page_json(name: str, display: str) -> dict:
    return {
        "$schema": SCHEMA_PAGE,
        "name": name,
        "displayName": display,
        "displayOption": "FitToPage",
        "height": 720,
        "width": 1280,
        "objects": {
            "background": [
                {
                    "properties": {
                        "color": solid("#F5F6F8"),
                        "transparency": lit("0D"),
                    }
                }
            ]
        },
    }


def main() -> int:
    if PAGES.exists():
        shutil.rmtree(PAGES)
    PAGES.mkdir(parents=True, exist_ok=True)

    total_visuals = 0
    for name, display, visuals in PAGE_SPECS:
        write_json(PAGES / name / "page.json", page_json(name, display))
        for visual in visuals:
            # Slicers with zero size are placeholders that were not laid out; skip them.
            pos = visual["position"]
            if pos["width"] == 0 or pos["height"] == 0:
                continue
            write_json(PAGES / name / "visuals" / visual["name"] / "visual.json", visual)
            total_visuals += 1

    write_json(
        PAGES / "pages.json",
        {
            "$schema": (
                "https://developer.microsoft.com/json-schemas/fabric/item/report/definition/"
                "pagesMetadata/1.0.0/schema.json"
            ),
            "pageOrder": [name for name, _, _ in PAGE_SPECS],
            "activePageName": PAGE_SPECS[0][0],
        },
    )

    print(f"wrote {len(PAGE_SPECS)} pages and {total_visuals} visuals to {PAGES}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
