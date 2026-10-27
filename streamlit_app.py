
"""gw2 account history dashboard."""

import json
import os
from datetime import datetime, time, timedelta, timezone
from html import escape
from pathlib import Path

import altair as alt
import pandas as pd
import requests
import streamlit as st
from sqlalchemy import text

from tracker_core import copper_to_str, make_engine


st.set_page_config(page_title="GW2 Account Tracker", layout="wide")

GREEN, RED, BLUE, GREY = "#18794e", "#c34452", "#4479a1", "#76818c"
PERIODS = ["1 day", "7 days", "30 days", "90 days", "365 days", "All time", "Custom"]
FREQUENCIES = {"Daily": "D", "Weekly": "W-SUN", "Monthly": "MS"}

WEALTH = {
    "Net worth": "liquid_net_worth",
    "Liquid gold": "liquid_gold",
    "Tradable items": "account_item_market_value",
    "Buy orders": "buy_order_committed",
    "Sell orders (net)": "sell_order_net_value",
}

COMPOSITION = {
    "Liquid gold": "liquid_gold",
    "Bank": "bank_market_value",
    "Material storage": "materials_market_value",
    "Shared inventory": "shared_inventory_market_value",
    "Character inventories": "character_inventory_market_value",
    "Delivery box items": "delivery_market_value",
    "Delivery box coins": "delivery_coins",
    "Buy orders": "buy_order_committed",
    "Sell orders (net)": "sell_order_net_value",
}

PROGRESSION = {
    "Achievement points": "achievement_points_estimate",
    "Mastery earned": "mastery_earned",
    "Skins": "skins_count",
    "WvW rank": "wvw_rank",
    "PvP rank": "pvp_rank",
}

# layout and typography

st.markdown("""
<style>
.block-container {
    max-width: 1420px;
    padding-top: 1.1rem;
    padding-bottom: 2.5rem;
}

h1 {
    font-size: 1.55rem !important;
    font-weight: 650 !important;
    letter-spacing: -0.025em;
}

h2 {
    font-size: 1.14rem !important;
    font-weight: 600 !important;
}

h3 {
    font-size: 1rem !important;
    font-weight: 600 !important;
}

[data-testid="stMetricValue"] {
    font-size: 1.32rem !important;
    font-weight: 600 !important;
}

[data-testid="stMetricLabel"] {
    font-size: .82rem !important;
}

[data-testid="stWidgetLabel"] p {
    font-size: .82rem !important;
}

.stTabs [data-baseweb="tab"] {
    font-size: .86rem;
    padding: .5rem .85rem;
}

div[data-testid="stVerticalBlock"] {
    gap: .75rem;
}

.pnl-label {
    font-size: .82rem;
    color: var(--text-color);
    opacity: .7;
    margin-bottom: .2rem;
}

.pnl-value {
    font-size: 1.3rem;
    font-weight: 650;
}
</style>
""", unsafe_allow_html=True)


# database and api

def database_url():
    if os.getenv("DATABASE_URL", "").strip():
        return os.environ["DATABASE_URL"].strip()

    try:
        if st.secrets.get("DATABASE_URL"):
            return str(st.secrets["DATABASE_URL"]).strip()
    except Exception:
        pass

    path = Path(__file__).resolve().parent / ".env"

    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.strip().partition("=")
            if sep and key.strip() == "DATABASE_URL":
                return value.strip().strip('"\'')

    return None


@st.cache_resource
def get_engine(url):
    return make_engine(url)


@st.cache_data(ttl=120, show_spinner=False)
def read_sql(sql, parameters, url):
    with get_engine(url).connect() as connection:
        return pd.read_sql(
            text(sql),
            connection,
            params=dict(parameters),
        )


def query(sql, params=None):
    return read_sql(
        sql,
        tuple(sorted((params or {}).items())),
        DB_URL,
    )


@st.cache_data(ttl=86400, show_spinner=False)
def item_lookup(ids):
    result = {}

    for offset in range(0, len(ids), 200):
        batch = ids[offset:offset + 200]

        try:
            response = requests.get(
                "https://api.guildwars2.com/v2/items",
                params={"ids": ",".join(map(str, batch))},
                timeout=12,
            )
            response.raise_for_status()
            result.update({
                int(row["id"]): row.get("name")
                for row in response.json()
            })
        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError,
        ):
            pass

    return {
        i: result.get(i) or f"Item {i}"
        for i in ids
    }


@st.cache_data(ttl=3600, show_spinner=False)
def market_prices(ids):
    prices = {}

    for offset in range(0, len(ids), 150):
        try:
            response = requests.get(
                "https://api.guildwars2.com/v2/commerce/prices",
                params={
                    "ids": ",".join(map(str, ids[offset:offset + 150]))
                },
                timeout=15,
            )
            response.raise_for_status()

            prices.update({
                int(row["id"]): int(
                    (row.get("sells") or {}).get("unit_price") or 0
                )
                for row in response.json()
            })

        except (
            requests.RequestException,
            ValueError,
            KeyError,
            TypeError,
        ):
            continue

    return prices


# formatting

def number(value):
    return 0 if value is None or pd.isna(value) else int(value)


def coins(value, signed=False):
    amount = number(value)
    return (
        ("+" if signed and amount > 0 else "")
        + copper_to_str(amount)
    )


def shade(value):
    if pd.isna(value):
        return ""

    colour = (
        GREEN if value > 0
        else RED if value < 0
        else GREY
    )

    return f"color:{colour};font-weight:600"


def pnl_metric(label, amount):
    amount = number(amount)
    colour = (
        GREEN if amount > 0
        else RED if amount < 0
        else GREY
    )

    st.markdown(
        f'<div class="pnl-label">{escape(label)}</div>'
        f'<div class="pnl-value" style="color:{colour}">'
        f'{escape(coins(amount, signed=True))}</div>',
        unsafe_allow_html=True,
    )


def metric_row(values):
    for column, (label, value, kind) in zip(
        st.columns(len(values)),
        values,
    ):
        with column:
            if kind == "pnl":
                pnl_metric(label, value)
            else:
                display = (
                    coins(value)
                    if kind == "coins"
                    else f"{number(value):,}"
                )
                st.metric(label, display)


def labelled_items(df):
    result = df.copy()

    if result.empty:
        result["item_name"] = pd.Series(dtype="str")
    else:
        ids = tuple(sorted(
            result["item_id"].dropna().astype(int).unique()
        ))
        result["item_name"] = (
            result["item_id"]
            .map(item_lookup(ids))
            .fillna("Unknown item")
        )

    return result


def item_filter(df, key):
    left, right = st.columns(2)

    search = left.text_input(
        "Item name or ID",
        key=f"{key}_search",
        placeholder="Search items",
    ).strip()

    items = (
        df[["item_id", "item_name"]]
        .drop_duplicates("item_id")
        .sort_values(["item_name", "item_id"])
    )

    if search:
        matches = (
            items["item_name"].str.contains(
                search,
                case=False,
                regex=False,
                na=False,
            )
            | items["item_id"].astype(str).eq(search)
        )
        items = items.loc[matches]

    labels = dict(zip(
        items["item_id"].astype(int),
        items["item_name"],
    ))

    widget_key = f"{key}_item"

    if st.session_state.get(widget_key) not in (None, *labels):
        st.session_state[widget_key] = None

    chosen = right.selectbox(
        "Select item",
        [None, *labels],
        format_func=lambda i: (
            "All matching items"
            if i is None
            else f"{labels[i]} ({i})"
        ),
        key=widget_key,
    )

    if search:
        df = df.loc[df["item_id"].isin(labels)].copy()

    return (
        df
        if chosen is None
        else df.loc[df["item_id"] == chosen].copy()
    )


# date controls

def preset_dates(period):
    today = datetime.now(timezone.utc).date()

    if period == "All time":
        return None, today

    days = int(period.split()[0])

    return (
        today - timedelta(days=days - 1),
        today,
    )


def apply_period(key):
    period = st.session_state[f"{key}_period"]

    if period != "Custom":
        (
            st.session_state[f"{key}_from"],
            st.session_state[f"{key}_to"],
        ) = preset_dates(period)


def manual_period(key):
    st.session_state[f"{key}_period"] = "Custom"


def reset_filters(key, default, additional=None):
    st.session_state[f"{key}_period"] = default
    apply_period(key)

    for name, value in (additional or {}).items():
        st.session_state[name] = value


def date_filter(key, default="30 days", reset=None):
    if f"{key}_period" not in st.session_state:
        st.session_state[f"{key}_period"] = default
        apply_period(key)

    with st.container(border=True):
        preset, start_col, end_col, reset_col = st.columns(
            [1.05, 1, 1, .72],
            vertical_alignment="bottom",
        )

        preset.selectbox(
            "Period",
            PERIODS,
            key=f"{key}_period",
            on_change=apply_period,
            args=(key,),
        )

        start = start_col.date_input(
            "From (UTC)",
            key=f"{key}_from",
            format="DD/MM/YYYY",
            on_change=manual_period,
            args=(key,),
        )

        end = end_col.date_input(
            "To (UTC)",
            key=f"{key}_to",
            format="DD/MM/YYYY",
            on_change=manual_period,
            args=(key,),
        )

        reset_col.button(
            "Reset",
            key=f"{key}_reset",
            use_container_width=True,
            on_click=reset_filters,
            args=(key, default, reset),
        )

    if start and end and start > end:
        st.error(
            "The start date must not be after the end date."
        )
        st.stop()

    return (
        datetime.combine(
            start,
            time.min,
            tzinfo=timezone.utc,
        ) if start else None,

        datetime.combine(
            end + timedelta(days=1),
            time.min,
            tzinfo=timezone.utc,
        ) if end else None,
    )


def date_where(column, window, clauses=()):
    parts, params = list(clauses), {}

    for key, operator, value in (
        ("start", ">=", window[0]),
        ("end", "<", window[1]),
    ):
        if value is not None:
            parts.append(f"{column} {operator} :{key}")
            params[key] = value

    return " AND ".join(parts) or "1=1", params


def fmt_dates(values, include_time=False):
    fmt = (
        "%d/%m/%Y %H:%M"
        if include_time
        else "%d/%m/%Y"
    )

    return (
        pd.to_datetime(
            values,
            utc=True,
            errors="coerce",
        )
        .dt.strftime(fmt)
        .fillna("—")
    )


# tables and charts

def show_table(data, formats=None, financial=(), height=420):
    styled = data.style.format(formats or {})

    if financial:
        styled = styled.map(
            shade,
            subset=list(financial),
        )

    st.dataframe(
        styled,
        hide_index=True,
        use_container_width=True,
        height=height,
    )


def download_csv(data, filename, key):
    st.download_button(
        "Export CSV",
        data.to_csv(index=False).encode("utf-8"),
        filename,
        mime="text/csv",
        key=key,
    )


def chart_controls(key, modes):
    a, b = st.columns([2, 1])

    return (
        a.selectbox(
            "Chart",
            [*modes, "None"],
            key=f"{key}_chart",
        ),
        b.selectbox(
            "Interval",
            list(FREQUENCIES),
            key=f"{key}_frequency",
        ),
    )


def time_chart(
    df,
    date_column,
    value_column,
    mode,
    interval,
    by_side=False,
):
    data = df.copy()

    data["date"] = pd.to_datetime(
        data[date_column],
        utc=True,
        errors="coerce",
    )

    data = data.dropna(subset=["date"])

    if data.empty:
        st.info("No dated records in this selection.")
        return

    groups = [
        pd.Grouper(
            key="date",
            freq=FREQUENCIES[interval],
        )
    ]

    if by_side:
        groups.append("side")

    data = (
        data.groupby(groups)[value_column]
        .sum()
        .reset_index(name="value")
    )

    monetary = mode in (
        "Profit / loss",
        "Cumulative profit",
        "Transaction value",
    )

    if monetary:
        data["value"] /= 10000

    if mode == "Cumulative profit":
        data["value"] = data["value"].cumsum()

    unit = "Gold" if monetary else "Items"

    tooltip = [
        alt.Tooltip(
            "date:T",
            title="Period",
            format="%d %b %Y",
        ),
        alt.Tooltip(
            "value:Q",
            title=unit,
            format=",.2f" if monetary else ",",
        ),
    ]

    base = alt.Chart(data).encode(
        x=alt.X("date:T", title=None)
    )

    if mode == "Cumulative profit":
        chart = base.mark_line(
            point=True,
            color=BLUE,
        ).encode(
            y=alt.Y("value:Q", title=unit),
            tooltip=tooltip,
        )

    elif by_side:
        chart = base.mark_bar().encode(
            y=alt.Y("value:Q", title=unit),
            color=alt.Color(
                "side:N",
                title="Side",
                scale=alt.Scale(
                    domain=["buys", "sells"],
                    range=[GREY, BLUE],
                ),
            ),
            tooltip=[*tooltip, "side:N"],
        )

    else:
        fill = (
            alt.condition(
                alt.datum.value >= 0,
                alt.value(GREEN),
                alt.value(RED),
            )
            if mode == "Profit / loss"
            else alt.value(BLUE)
        )

        chart = base.mark_bar().encode(
            y=alt.Y("value:Q", title=unit),
            color=fill,
            tooltip=tooltip,
        )

    st.altair_chart(
        chart.properties(height=275),
        use_container_width=True,
    )


def snapshot_history():
    history = query(
        "SELECT * FROM account_snapshots ORDER BY snapshot_at"
    )

    if not history.empty:
        history["snapshot_at"] = pd.to_datetime(
            history["snapshot_at"],
            utc=True,
        )

    return history


def history_chart(history, series, period, key):
    if history.empty:
        st.info("No historical snapshots.")
        return

    options = list(series)

    chosen = (
        st.selectbox(
            "Series",
            options,
            key=f"{key}_series",
        )
        if len(options) > 1
        else options[0]
    )

    data = history.copy()

    if period[0] is not None:
        data = data.loc[
            data["snapshot_at"] >= period[0]
        ]

    if period[1] is not None:
        data = data.loc[
            data["snapshot_at"] < period[1]
        ]

    if data.empty:
        st.info("No observations within this date range.")
        return

    money = series[chosen] in WEALTH.values()

    data["value"] = (
        pd.to_numeric(
            data[series[chosen]],
            errors="coerce",
        )
        / (10000 if money else 1)
    )

    graph = alt.Chart(data).mark_line(
        color=BLUE,
        point=True,
    ).encode(
        x=alt.X(
            "snapshot_at:T",
            title=None,
        ),
        y=alt.Y(
            "value:Q",
            title="Gold" if money else chosen,
            scale=alt.Scale(zero=False),
        ),
        tooltip=[
            alt.Tooltip(
                "snapshot_at:T",
                title="Recorded",
            ),
            alt.Tooltip(
                "value:Q",
                title=chosen,
                format=",.2f",
            ),
        ],
    ).properties(height=300)

    st.altair_chart(
        graph,
        use_container_width=True,
    )


# overview

def overview(latest):
    st.subheader("Overview")

    window = date_filter("overview")
    history = snapshot_history()

    if history.empty:
        st.info("No account history available.")
        return

    current_rows = (
        history
        if window[1] is None
        else history.loc[
            history["snapshot_at"] < window[1]
        ]
    )

    if current_rows.empty:
        st.info(
            "No account observations exist before "
            "the chosen end date."
        )
        return

    current = current_rows.iloc[-1]

    prior_rows = (
        history.iloc[:1]
        if window[0] is None
        else history.loc[
            history["snapshot_at"] < window[0]
        ].tail(1)
    )

    baseline = (
        prior_rows.iloc[-1]
        if not prior_rows.empty
        else None
    )

    def change(field):
        if (
            baseline is None
            or pd.isna(current[field])
            or pd.isna(baseline[field])
        ):
            return None

        return (
            number(current[field])
            - number(baseline[field])
        )

    value_fields = [
        "liquid_net_worth",
        "liquid_gold",
        "achievement_points_estimate",
        "total_playtime_seconds",
        "skins_count",
    ]

    changes = [
        change(field)
        for field in value_fields
    ]

    a, b, c, d, e = st.columns(5)

    a.metric(
        "Net worth",
        coins(current[value_fields[0]]),
        coins(changes[0], signed=True)
        if changes[0] is not None else None,
    )

    b.metric(
        "Liquid gold",
        coins(current[value_fields[1]]),
        coins(changes[1], signed=True)
        if changes[1] is not None else None,
    )

    c.metric(
        "Achievement points",
        f"{number(current[value_fields[2]]):,}",
        f"{changes[2]:+,}"
        if changes[2] is not None else None,
    )

    d.metric(
        "Playtime",
        f"{number(current[value_fields[3]]) / 3600:,.1f} h",
        f"{changes[3] / 3600:+,.1f} h"
        if changes[3] is not None else None,
    )

    e.metric(
        "Skins",
        f"{number(current[value_fields[4]]):,}",
        f"{changes[4]:+,}"
        if changes[4] is not None else None,
    )

    observed = pd.Timestamp(
        current["snapshot_at"]
    ).strftime("%d %b %Y %H:%M UTC")

    if baseline is None:
        st.caption(
            f"Values as at {observed}. Changes unavailable: "
            "no earlier comparison snapshot."
        )
    else:
        compared = pd.Timestamp(
            baseline["snapshot_at"]
        ).strftime("%d %b %Y %H:%M UTC")

        st.caption(
            f"Values as at {observed}. Figures below each "
            f"metric show the change since {compared}. "
            "All comparisons use actual recorded snapshots."
        )

    st.subheader("Account history")

    history_chart(
        history,
        {"Net worth": "liquid_net_worth"},
        window,
        "overview",
    )

    left, right = st.columns(2)

    with left:
        st.subheader("Progression")

        fields = {
            "Mastery earned": "mastery_earned",
            "Mastery unspent": "mastery_unspent",
            "Fractal level": "fractal_level",
            "WvW rank": "wvw_rank",
            "PvP rank": "pvp_rank",
            "Legendary items": "legendary_count",
            "Characters": "character_count",
            "Deaths": "total_deaths",
        }

        st.dataframe(
            pd.DataFrame([
                {
                    "Metric": name,
                    "Value": number(current[field]),
                }
                for name, field in fields.items()
            ]),
            hide_index=True,
            use_container_width=True,
        )

    with right:
        st.subheader("Collections")

        fields = {
            "Skins": "skins_count",
            "Dyes": "dyes_count",
            "Titles": "titles_count",
            "Minis": "minis_count",
            "Outfits": "outfits_count",
            "Gliders": "gliders_count",
            "Mount skins": "mount_skins_count",
            "Recipes": "recipes_count",
        }

        st.dataframe(
            pd.DataFrame([
                {
                    "Collection": name,
                    "Count": number(current[field]),
                }
                for name, field in fields.items()
            ]),
            hide_index=True,
            use_container_width=True,
        )


# net worth

def net_worth(latest):
    st.subheader("Net worth")

    metric_row([
        (name, latest[field], "coins")
        for name, field in WEALTH.items()
    ])

    st.caption(
        "Estimated account value using stored inventories "
        "and Trading Post market prices. Some items are not "
        "tradeable or cannot be priced."
    )

    st.subheader("Wealth history")

    window = date_filter(
        "wealth",
        default="All time",
    )

    history_chart(
        snapshot_history(),
        WEALTH,
        window,
        "wealth",
    )

    st.subheader("Wealth composition")

    breakdown = pd.DataFrame([
        {
            "Component": name,
            "Copper": number(latest[field]),
        }
        for name, field in COMPOSITION.items()
    ])

    breakdown["Gold"] = breakdown["Copper"] / 10000

    left, right = st.columns([1, 2])

    with left:
        st.dataframe(
            pd.DataFrame({
                "Component": breakdown["Component"],
                "Value": breakdown["Copper"].map(coins),
            }),
            hide_index=True,
            use_container_width=True,
            height=355,
        )

    with right:
        graph = alt.Chart(
            breakdown
        ).mark_bar(
            color=BLUE
        ).encode(
            x=alt.X(
                "Gold:Q",
                title="Gold",
            ),
            y=alt.Y(
                "Component:N",
                title=None,
                sort="-x",
            ),
            tooltip=[
                "Component:N",
                alt.Tooltip(
                    "Gold:Q",
                    format=",.2f",
                ),
            ],
        ).properties(height=335)

        st.altair_chart(
            graph,
            use_container_width=True,
        )


# progression

def progression(latest):
    st.subheader("Progression")

    metric_row([
        (
            "Achievement points",
            latest["achievement_points_estimate"],
            "count",
        ),
        ("Mastery earned", latest["mastery_earned"], "count"),
        ("Fractal level", latest["fractal_level"], "count"),
        ("WvW rank", latest["wvw_rank"], "count"),
        ("PvP rank", latest["pvp_rank"], "count"),
    ])

    st.subheader("Progression history")

    window = date_filter(
        "progression",
        default="All time",
    )

    history_chart(
        snapshot_history(),
        PROGRESSION,
        window,
        "progression",
    )

    a, b = st.columns(2)

    with a:
        st.subheader("Mastery by region")

        mastery = query("""
            SELECT region, earned, spent,
                   earned - spent AS unspent
            FROM mastery_snapshots
            WHERE snapshot_at = (
                SELECT MAX(snapshot_at)
                FROM mastery_snapshots
            )
            ORDER BY earned DESC, region
        """)

        st.dataframe(
            mastery,
            hide_index=True,
            use_container_width=True,
        )

    with b:
        wins = number(latest["pvp_wins"])
        losses = number(latest["pvp_losses"])

        st.subheader("PvP record")

        if wins + losses:
            st.write(
                f"Wins: {wins:,} · Losses: {losses:,} · "
                f"Win rate: "
                f"{100 * wins / (wins + losses):.1f}%"
            )
        else:
            st.write("No PvP results")

        count = query(
            "SELECT COUNT(*) AS count FROM pvp_matches"
        )

        st.caption(
            f"Retained PvP matches: "
            f"{number(count.iloc[0]['count']):,}"
        )


# wallet

def wallet():
    st.subheader("Wallet")

    window = date_filter(
        "wallet",
        reset={"wallet_search": ""},
    )

    end_filter = (
        "WHERE snapshot_at < :end"
        if window[1] else ""
    )

    start_filter = (
        "WHERE snapshot_at < :start"
        if window[0] else ""
    )

    order = "DESC" if window[0] else "ASC"

    params = {
        k: v
        for k, v in zip(("start", "end"), window)
        if v is not None
    }

    data = query(f"""
        WITH current_balance AS (
            SELECT DISTINCT ON (currency_id)
                   currency_id, value, snapshot_at
            FROM wallet_snapshots
            {end_filter}
            ORDER BY currency_id, snapshot_at DESC
        ),
        baseline AS (
            SELECT DISTINCT ON (currency_id)
                   currency_id, value, snapshot_at
            FROM wallet_snapshots
            {start_filter}
            ORDER BY currency_id, snapshot_at {order}
        )
        SELECT
            c.currency_id,
            COALESCE(
                m.name,
                'Currency ' || c.currency_id::text
            ) AS currency,
            c.value,
            b.value AS previous,
            c.snapshot_at AS current_at,
            b.snapshot_at AS baseline_at
        FROM current_balance c
        LEFT JOIN baseline b USING (currency_id)
        LEFT JOIN currency_metadata m
            ON m.currency_id = c.currency_id
        ORDER BY
            COALESCE(m.display_order, 999999),
            currency
    """, params)

    if data.empty:
        st.info(
            "No wallet observations exist before "
            "the selected end date."
        )
        return

    search = st.text_input(
        "Search currencies",
        placeholder="Currency name",
        key="wallet_search",
    )

    if search:
        data = data.loc[
            data["currency"].str.contains(
                search,
                case=False,
                regex=False,
                na=False,
            )
        ]

    data = data.copy()
    data["change"] = data["value"] - data["previous"]

    if data.empty:
        st.info("No matching currencies.")
        return

    def amount(row, field, signed=False):
        value = row[field]

        if pd.isna(value):
            return "—"

        # currency id 1 is coin, stored in copper

        if number(row["currency_id"]) == 1:
            return coins(value, signed=signed)

        return (
            f"{number(value):+,}"
            if signed
            else f"{number(value):,}"
        )

    display = pd.DataFrame({
        "Currency": data["currency"].values,
        "Balance": data.apply(
            amount,
            axis=1,
            field="value",
        ).values,
        "Change": data.apply(
            amount,
            axis=1,
            field="change",
            signed=True,
        ).values,
        "As of": fmt_dates(data["current_at"]).values,
        "Compared with": fmt_dates(
            data["baseline_at"]
        ).values,
    })

    changes = data["change"].reset_index(drop=True)

    styled = display.style.apply(
        lambda _: [
            shade(v)
            for v in changes
        ],
        axis=0,
        subset=["Change"],
    )

    st.dataframe(
        styled,
        hide_index=True,
        use_container_width=True,
        height=475,
    )

    st.caption(
        "Coin (currency ID 1) is shown in gold, silver and "
        "copper. Other currencies use units. Balances reflect "
        "the latest observation at or before the selected end "
        "date; changes compare against the last observation "
        "before the start date (the first record for All time). "
        "A dash indicates that no comparison is available."
    )


# characters

def character_valuations():
    states = query("""
        SELECT observed_at, raw_json
        FROM current_state
        WHERE state_key LIKE 'character:%:state'
    """)

    inventory, ids = {}, set()

    for row in states.itertuples(index=False):
        state = (
            json.loads(row.raw_json)
            if isinstance(row.raw_json, str)
            else row.raw_json
        )

        if not isinstance(state, dict) or not state.get("name"):
            continue

        bags = [
            slot
            for bag in state.get("bags") or []
            if isinstance(bag, dict)
            for slot in bag.get("inventory") or []
            if isinstance(slot, dict)
        ]

        gear = [
            item
            for item in state.get("equipment") or []
            if isinstance(item, dict)
        ]

        inventory[state["name"]] = (
            bags,
            gear,
            row.observed_at,
        )

        ids.update(
            int(item["id"])
            for item in bags + gear
            if item.get("id")
        )

    prices = (
        market_prices(tuple(sorted(ids)))
        if ids else {}
    )

    def estimated(items):
        records = [
            (
                int(item["id"]),
                int(item.get("count") or 1),
            )
            for item in items
            if item.get("id")
        ]

        matched = [
            (prices[i], qty)
            for i, qty in records
            if prices.get(i)
        ]

        return (
            coins(sum(
                price * qty
                for price, qty in matched
            ))
            if matched
            else "—"
        )

    return {
        name: {
            "Bag value": estimated(bags),
            "Equipment value": estimated(gear),
            "Valued at": pd.to_datetime(
                observed,
                utc=True,
            ).strftime("%d/%m/%Y"),
        }
        for name, (bags, gear, observed) in inventory.items()
    }


def characters():
    st.subheader("Characters")

    data = query("""
        SELECT
            character_name AS name,
            race, profession, level,
            age_seconds, deaths, created_at
        FROM character_snapshots
        WHERE snapshot_at = (
            SELECT MAX(snapshot_at)
            FROM character_snapshots
        )
        ORDER BY character_name
    """)

    search = st.text_input(
        "Find character",
        placeholder="Search by name",
        key="character_search",
    )

    if search:
        data = data.loc[
            data["name"].str.contains(
                search,
                case=False,
                regex=False,
                na=False,
            )
        ]

    if data.empty:
        st.info("No matching characters.")
        return

    data = data.copy()

    data["Playtime (hours)"] = (
        pd.to_numeric(
            data["age_seconds"],
            errors="coerce",
        ) / 3600
    )

    data["Created"] = fmt_dates(data["created_at"])

    display = data.rename(columns={
        "name": "Character",
        "race": "Race",
        "profession": "Profession",
        "level": "Level",
        "deaths": "Deaths",
    })

    columns = [
        "Character",
        "Race",
        "Profession",
        "Level",
        "Playtime (hours)",
        "Deaths",
        "Created",
    ]

    if st.checkbox(
        "Show estimated bag and equipment values",
        key="character_values",
    ):
        try:
            estimates = character_valuations()

            for field in (
                "Bag value",
                "Equipment value",
                "Valued at",
            ):
                display[field] = display["Character"].map(
                    lambda name: estimates.get(
                        name, {}
                    ).get(field, "—")
                )

            columns += [
                "Bag value",
                "Equipment value",
                "Valued at",
            ]

            st.caption(
                "Indicative listing prices for items with "
                "available market data. Bound items may not "
                "be saleable; unpriced items are excluded. "
                "These estimates are not added to account "
                "net worth."
            )

        except (
            ValueError,
            TypeError,
            KeyError,
            requests.RequestException,
        ):
            st.warning(
                "Valuation could not be loaded. "
                "Character details remain available."
            )

    st.dataframe(
        display[columns],
        hide_index=True,
        use_container_width=True,
        column_config={
            "Playtime (hours)": st.column_config.NumberColumn(
                format="%.1f"
            )
        },
    )


# trading post

def profit_table(df, grouping):
    if grouping == "Individual trades":
        return pd.DataFrame({
            "Sell date": fmt_dates(df["sell_date"]),
            "Item": df["item_name"],
            "Qty": df["quantity"],
            "Buy/unit": df["buy_price"],
            "Sell/unit": df["sell_price"],
            "Cost": df["cost"],
            "Net proceeds": df["proceeds"],
            "P/L": df["profit"],
            "ROI (%)": df["roi"],
        })

    data = df.copy()
    keys = []

    interval = {
        "Day": "D",
        "Week": "W-SUN",
        "Month": "M",
        "Day and item": "D",
    }.get(grouping)

    if interval:
        recorded = (
            pd.to_datetime(
                data["sell_date"],
                utc=True,
            )
            .dt.tz_localize(None)
        )

        data["Period"] = (
            recorded.dt.to_period(interval)
            .dt.start_time.dt.date
        )

        keys.append("Period")

    if grouping in ("Item", "Day and item"):
        keys.extend([
            "item_id",
            "item_name",
        ])

    grouped = (
        data.groupby(
            keys,
            dropna=False,
        )
        .agg(
            Qty=("quantity", "sum"),
            Matches=("id", "size"),
            Cost=("cost", "sum"),
            proceeds=("proceeds", "sum"),
            profit=("profit", "sum"),
        )
        .reset_index()
        .rename(columns={
            "proceeds": "Net proceeds",
            "profit": "P/L",
            "item_id": "Item ID",
            "item_name": "Item",
        })
    )

    grouped["ROI (%)"] = (
        100 * grouped["P/L"]
        / grouped["Cost"].replace(0, float("nan"))
    )

    grouped["ROI (%)"] = grouped["ROI (%)"].fillna(0)

    return grouped.sort_values(
        "Period" if "Period" in grouped else "P/L",
        ascending=False,
    )


def profit_loss():
    st.subheader("Realised profit & loss")

    window = date_filter(
        "pl",
        reset={
            "pl_search": "",
            "pl_item": None,
            "pl_result": "All",
        },
    )

    where, params = date_where(
        "sell_date",
        window,
    )

    df = query(
        "SELECT id, item_id, quantity, buy_price, "
        "sell_price, profit, buy_date, sell_date "
        f"FROM matched_flips WHERE {where} "
        "ORDER BY sell_date DESC NULLS LAST, id DESC",
        params,
    )

    df = item_filter(
        labelled_items(df),
        "pl",
    )

    result = st.selectbox(
        "Result",
        [
            "All",
            "Profit only",
            "Loss only",
            "Breakeven",
        ],
        key="pl_result",
    )

    if result != "All":
        mask = {
            "Profit only": df["profit"] > 0,
            "Loss only": df["profit"] < 0,
            "Breakeven": df["profit"] == 0,
        }[result]

        df = df.loc[mask].copy()

    df["cost"] = df["buy_price"] * df["quantity"]
    df["proceeds"] = df["cost"] + df["profit"]

    df["roi"] = (
        100 * df["profit"]
        / df["cost"].replace(0, float("nan"))
    ).fillna(0)

    metric_row([
        ("Realised P/L", df["profit"].sum(), "pnl"),
        ("Cost basis", df["cost"].sum(), "coins"),
        ("Net proceeds", df["proceeds"].sum(), "coins"),
        ("Matched quantity", df["quantity"].sum(), "count"),
        ("Matched rows", len(df), "count"),
    ])

    st.caption(
        "Realised P/L from stored FIFO matches, after "
        "Trading Post fees. Unmatched sales are excluded. "
        "Results below use the filters above."
    )

    if df.empty:
        st.info(
            "No matching trades. Try another date range or item."
        )
        return

    st.subheader("Historical analysis")

    mode, frequency = chart_controls(
        "pl",
        [
            "Profit / loss",
            "Cumulative profit",
            "Item volume",
        ],
    )

    if mode != "None":
        time_chart(
            df,
            "sell_date",
            "quantity" if mode == "Item volume" else "profit",
            mode,
            frequency,
        )

    st.subheader("Matched transactions")

    grouping = st.selectbox(
        "Table grouping",
        [
            "Individual trades",
            "Day",
            "Week",
            "Month",
            "Item",
            "Day and item",
        ],
        key="pl_group",
    )

    summary = profit_table(df, grouping)

    monetary = {
        column: coins
        for column in (
            "Buy/unit",
            "Sell/unit",
            "Cost",
            "Net proceeds",
        )
        if column in summary
    }

    show_table(
        summary,
        {
            **monetary,
            "P/L": lambda v: coins(v, signed=True),
            "ROI (%)": "{:+.1f}%",
        },
        financial=("P/L", "ROI (%)"),
    )

    st.caption(
        "Grouped ROI = total profit / total cost basis. "
        "Periods use UTC sale dates."
    )

    left, right = st.columns([1, 1])

    with left:
        download_csv(
            summary,
            "gw2_profit_loss_table.csv",
            "pl_export",
        )

    if grouping != "Individual trades":
        with right:
            download_csv(
                df,
                "gw2_profit_loss_trades.csv",
                "pl_raw_export",
            )


def transactions():
    st.subheader("Trading Post transactions")

    window = date_filter(
        "tx",
        reset={
            "tx_search": "",
            "tx_item": None,
            "tx_side": "All",
            "tx_state": "history",
        },
    )

    a, b = st.columns(2)

    side = a.selectbox(
        "Side",
        ["All", "buys", "sells"],
        key="tx_side",
    )

    state = b.selectbox(
        "State",
        ["history", "current", "All"],
        key="tx_state",
    )

    conditions, params = [], {}

    if side != "All":
        conditions.append("side = :side")
        params["side"] = side

    if state != "All":
        conditions.append("transaction_set = :state")
        params["state"] = state

    where, bounds = date_where(
        "COALESCE(purchased, created)",
        window,
        conditions,
    )

    df = query(
        "SELECT transaction_id, side, transaction_set, "
        "item_id, price, quantity, created, purchased "
        f"FROM tp_transactions WHERE {where} "
        "ORDER BY COALESCE(purchased, created) "
        "DESC NULLS LAST, transaction_id DESC",
        {**params, **bounds},
    )

    df = item_filter(
        labelled_items(df),
        "tx",
    )

    if df.empty:
        st.info(
            "No matching transactions. "
            "Try another date range or item."
        )
        return

    df["total"] = df["price"] * df["quantity"]

    metric_row([
        ("Transactions", len(df), "count"),
        ("Item quantity", df["quantity"].sum(), "count"),
        (
            "Buy value",
            df.loc[df["side"] == "buys", "total"].sum(),
            "coins",
        ),
        (
            "Sell value",
            df.loc[df["side"] == "sells", "total"].sum(),
            "coins",
        ),
    ])

    st.caption(
        "Values are gross order amounts. "
        "Current orders may not have been filled."
    )

    st.subheader("Transaction activity")

    mode, frequency = chart_controls(
        "tx",
        [
            "Item volume",
            "Transaction value",
        ],
    )

    if mode != "None":
        df["activity_date"] = df["purchased"].combine_first(
            df["created"]
        )

        time_chart(
            df,
            "activity_date",
            "quantity" if mode == "Item volume" else "total",
            mode,
            frequency,
            by_side=True,
        )

    st.subheader("Transaction history")

    display = pd.DataFrame({
        "Date": fmt_dates(
            df["purchased"].combine_first(df["created"]),
            include_time=True,
        ),
        "Item": df["item_name"],
        "Side": df["side"],
        "State": df["transaction_set"],
        "Qty": df["quantity"],
        "Unit price": df["price"].map(coins),
        "Total": df["total"].map(coins),
        "Transaction ID": df["transaction_id"],
    })

    show_table(display)

    download_csv(
        df,
        "gw2_transactions.csv",
        "tx_export",
    )


def by_item():
    st.subheader("Performance by item")

    window = date_filter(
        "item",
        default="All time",
        reset={
            "item_search": "",
            "item_item": None,
            "item_sort": "Highest profit",
        },
    )

    where, params = date_where(
        "sell_date",
        window,
    )

    df = query(
        "SELECT item_id, SUM(quantity)::bigint AS quantity, "
        "SUM(profit)::bigint AS profit, "
        "COUNT(*)::bigint AS matched_rows "
        f"FROM matched_flips WHERE {where} "
        "GROUP BY item_id",
        params,
    )

    df = item_filter(
        labelled_items(df),
        "item",
    )

    sorting = st.selectbox(
        "Sort by",
        [
            "Highest profit",
            "Biggest losses",
            "Most traded quantity",
            "Most matched rows",
        ],
        key="item_sort",
    )

    if df.empty:
        st.info("No matching items.")
        return

    column, ascending = {
        "Highest profit": ("profit", False),
        "Biggest losses": ("profit", True),
        "Most traded quantity": ("quantity", False),
        "Most matched rows": ("matched_rows", False),
    }[sorting]

    df = df.sort_values(
        column,
        ascending=ascending,
    ).copy()

    df["profit_per_unit"] = (
        df["profit"]
        / df["quantity"].replace(0, float("nan"))
    ).fillna(0)

    metric_row([
        ("Realised P/L", df["profit"].sum(), "pnl"),
        ("Items", len(df), "count"),
        ("Matched quantity", df["quantity"].sum(), "count"),
    ])

    st.subheader("Item comparison")

    a, b = st.columns([2, 1])

    mode = a.selectbox(
        "Chart",
        [
            "Profit / loss",
            "Item volume",
            "None",
        ],
        key="item_chart",
    )

    limit = b.selectbox(
        "Items to display",
        [10, 15, 25],
        index=1,
        key="item_limit",
    )

    if mode != "None":
        chart_data = df.head(limit).copy()

        chart_data["label"] = (
            chart_data["item_name"]
            + " ("
            + chart_data["item_id"].astype(str)
            + ")"
        )

        monetary = mode == "Profit / loss"

        chart_data["value"] = (
            chart_data["profit"] / 10000
            if monetary
            else chart_data["quantity"]
        )

        palette = (
            alt.condition(
                alt.datum.value >= 0,
                alt.value(GREEN),
                alt.value(RED),
            )
            if monetary
            else alt.value(BLUE)
        )

        graph = alt.Chart(
            chart_data
        ).mark_bar().encode(
            x=alt.X(
                "value:Q",
                title="Gold" if monetary else "Items",
            ),
            y=alt.Y(
                "label:N",
                title=None,
                sort=None,
            ),
            color=palette,
            tooltip=[
                "label:N",
                alt.Tooltip(
                    "value:Q",
                    title="Value",
                    format=",.2f",
                ),
            ],
        ).properties(
            height=max(275, limit * 25)
        )

        st.altair_chart(
            graph,
            use_container_width=True,
        )

    st.subheader("Item performance")

    display = df.rename(columns={
        "item_name": "Item",
        "item_id": "Item ID",
        "quantity": "Matched quantity",
        "profit": "Realised P/L",
        "matched_rows": "Matched rows",
        "profit_per_unit": "P/L per unit",
    })

    columns = [
        "Item",
        "Item ID",
        "Matched quantity",
        "Realised P/L",
        "Matched rows",
        "P/L per unit",
    ]

    show_table(
        display[columns],
        {
            "Realised P/L": lambda x: coins(x, signed=True),
            "P/L per unit": lambda x: coins(
                int(x),
                signed=True,
            ),
        },
        financial=(
            "Realised P/L",
            "P/L per unit",
        ),
        height=480,
    )

    download_csv(
        df,
        "gw2_item_performance.csv",
        "item_export",
    )


def trading_post(latest):
    st.subheader("Trading Post")

    metric_row([
        (
            "Cumulative FIFO profit",
            latest["realised_flip_profit"],
            "pnl",
        ),
        (
            "Matched flip rows",
            latest["matched_flip_rows"],
            "count",
        ),
    ])

    st.caption(
        "Cumulative figures reflect the most recent account "
        "snapshot; filters below affect the selected view."
    )

    st.divider()
    profit_loss()


# collector

def collector():
    st.subheader("Collector")

    jobs = query("""
        SELECT started_at, finished_at, status,
               runtime_seconds, endpoints_ok,
               endpoints_failed, message
        FROM job_runs
        ORDER BY id DESC
        LIMIT 30
    """)

    errors = query("""
        SELECT snapshot_at, endpoint, status_code, error
        FROM endpoint_errors
        ORDER BY id DESC
        LIMIT 250
    """)

    if not jobs.empty:
        recent = jobs.iloc[0]

        a, b, c, d = st.columns(4)

        a.metric(
            "Latest status",
            str(recent["status"]),
        )

        b.metric(
            "Runtime",
            f"{float(recent['runtime_seconds']):.1f}s"
            if pd.notna(recent["runtime_seconds"])
            else "—",
        )

        c.metric(
            "Endpoints OK",
            f"{number(recent['endpoints_ok']):,}",
        )

        d.metric(
            "Endpoints failed",
            f"{number(recent['endpoints_failed']):,}",
        )

    st.subheader("Recent runs")

    st.dataframe(
        jobs,
        hide_index=True,
        use_container_width=True,
    )

    st.subheader("Endpoint errors")

    if errors.empty:
        st.info("No endpoint errors recorded.")
    else:
        st.dataframe(
            errors,
            hide_index=True,
            use_container_width=True,
        )


# application

DB_URL = database_url()

if not DB_URL:
    st.error(
        "DATABASE_URL is missing. Configure it locally "
        "in .env or through Streamlit secrets."
    )
    st.stop()

try:
    latest_result = query("""
        SELECT *
        FROM account_snapshots
        ORDER BY snapshot_at DESC
        LIMIT 1
    """)
except Exception:
    st.error(
        "Unable to connect to PostgreSQL. "
        "Check DATABASE_URL, credentials and network access."
    )
    st.stop()

if latest_result.empty:
    st.warning(
        "No account snapshots are available. "
        "Run the collector and refresh the dashboard."
    )
    st.stop()

latest = latest_result.iloc[0]

head, refresh = st.columns(
    [5, 1],
    vertical_alignment="center",
)

with head:
    st.title("GW2 Account Tracker")

    observed = pd.to_datetime(
        latest["snapshot_at"],
        utc=True,
    )

    account = latest.get("account_name") or "GW2 account"

    st.caption(
        f"{account} · Latest snapshot "
        f"{observed:%d %b %Y %H:%M UTC} · "
        f"{number(latest['endpoints_ok'])} endpoints OK / "
        f"{number(latest['endpoints_failed'])} failed"
    )

with refresh:
    if st.button(
        "Refresh",
        use_container_width=True,
    ):
        read_sql.clear()
        st.rerun()

PAGES = [
    "Overview",
    "Net worth",
    "Progression",
    "Wallet",
    "Characters",
    "Trading Post",
    "Collector",
]

page = st.segmented_control(
    "Section",
    PAGES,
    default="Overview",
    key="page",
    label_visibility="collapsed",
) or "Overview"

st.divider()

{
    "Overview": lambda: overview(latest),
    "Net worth": lambda: net_worth(latest),
    "Progression": lambda: progression(latest),
    "Wallet": wallet,
    "Characters": characters,
    "Trading Post": lambda: trading_post(latest),
    "Collector": collector,
}[page]()
