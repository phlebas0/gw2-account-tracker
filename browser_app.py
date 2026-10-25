
"""local gw2 net worth and trading post dashboard."""

from datetime import datetime, time, timedelta, timezone
from html import escape

import altair as alt
import pandas as pd
import streamlit as st
from sqlalchemy import text

from local_app import ItemNameResolver, initial_database_url
from tracker_core import copper_to_str, make_engine

st.set_page_config(
    page_title="GW2 Account Tracker",
    layout="wide",
    initial_sidebar_state="collapsed",
)

GREEN, RED, BLUE, GREY = "#198754", "#c44b55", "#557a99", "#778391"
PERIODS = ["7 days", "30 days", "90 days", "365 days", "All time", "Custom"]
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
    "Materials": "materials_market_value",
    "Shared inventory": "shared_inventory_market_value",
    "Character inventory": "character_inventory_market_value",
    "Delivery items": "delivery_market_value",
    "Delivery coins": "delivery_coins",
    "Buy orders": "buy_order_committed",
    "Sell orders (net)": "sell_order_net_value",
}

st.markdown("""
<style>
.block-container {max-width:1480px; padding-top:1rem; padding-bottom:2rem}
html, body, [class*="st-"] {font-family:"Segoe UI",Arial,sans-serif}
h1 {font-size:1.55rem!important; font-weight:600!important}
h2 {font-size:1.12rem!important; font-weight:600!important}
h3 {font-size:.96rem!important; font-weight:600!important}
[data-testid="stMetricValue"] {font-size:1.25rem!important; font-weight:600!important}
[data-testid="stMetricLabel"], [data-testid="stWidgetLabel"] p {font-size:.8rem!important}
.stTabs [data-baseweb="tab"] {font-size:.85rem; padding:.45rem .8rem}
div[data-testid="stVerticalBlock"] {gap:.65rem}
.pnl-label {font-size:.78rem; color:#87919d; margin-bottom:.25rem}
.pnl-value {font-size:1.25rem; font-weight:600}
</style>
""", unsafe_allow_html=True)


@st.cache_resource
def engine(url):
    return make_engine(url)


@st.cache_resource
def item_resolver():
    return ItemNameResolver()


@st.cache_data(ttl=120, show_spinner=False)
def read_sql(sql, params, url):
    with engine(url).connect() as conn:
        return pd.read_sql(text(sql), conn, params=params)


def query(sql, params=None):
    return read_sql(sql, params or {}, database_url)


def number(value):
    return 0 if value is None or pd.isna(value) else int(value)


def coins(value, signed=False):
    amount = number(value)
    return ("+" if signed and amount > 0 else "") + copper_to_str(amount)


def colour(value):
    return GREEN if value > 0 else RED if value < 0 else GREY


def pnl_metric(label, value):
    amount = number(value)
    st.markdown(
        f'<div class="pnl-label">{escape(label)}</div>'
        f'<div class="pnl-value" style="color:{colour(amount)}">'
        f'{escape(coins(amount, signed=True))}</div>',
        unsafe_allow_html=True,
    )


def pnl_style(value):
    return f"color:{colour(value)};font-weight:600"


def metrics(values):
    for col, (label, value, kind) in zip(st.columns(len(values)), values):
        with col:
            if kind == "pnl":
                pnl_metric(label, value)
            else:
                shown = coins(value) if kind == "coins" else f"{number(value):,}"
                st.metric(label, shown)


def item_names(df):
    df = df.copy()
    if df.empty:
        df["item_name"] = pd.Series(dtype="str")
        return df
    ids = df["item_id"].dropna().astype(int).unique().tolist()
    try:
        names = item_resolver().get_many(ids)
    except Exception:
        names = {}
    df["item_name"] = df["item_id"].map(
        lambda value: str(names.get(int(value)) or f"Item {int(value)}")
        if pd.notna(value) else "Unknown item"
    )
    return df


def item_filter(df, key):
    items = (df[["item_id", "item_name"]].drop_duplicates("item_id")
             .sort_values(["item_name", "item_id"]))
    labels = dict(zip(items["item_id"].astype(int), items["item_name"]))
    selection = st.selectbox(
        "Item", [None, *labels],
        format_func=lambda i: "All items" if i is None else f"{labels[i]} ({i})",
        key=f"{key}_item",
    )
    return df if selection is None else df.loc[df["item_id"] == selection].copy()


def date_filter(key):
    period = st.selectbox("Period", PERIODS, index=1, key=f"{key}_period")
    if period == "All time":
        return None, None
    today = datetime.now(timezone.utc).date()
    if period == "Custom":
        a, b = st.columns(2)
        start = a.date_input("From", today - timedelta(days=29), key=f"{key}_from")
        end = b.date_input("To", today, key=f"{key}_to")
        if start > end:
            st.error("Start date must not precede end date.")
            st.stop()
    else:
        start, end = today - timedelta(days=int(period.split()[0]) - 1), today
    return (
        datetime.combine(start, time.min, tzinfo=timezone.utc),
        datetime.combine(end + timedelta(days=1), time.min, tzinfo=timezone.utc),
    )


def period_sql(column, dates, extra=()):
    clauses, params = list(extra), {}
    for suffix, operator, value in (("start", ">=", dates[0]), ("end", "<", dates[1])):
        if value is not None:
            clauses.append(f"{column} {operator} :{suffix}")
            params[suffix] = value
    return " AND ".join(clauses) or "1=1", params


def dates(series, clock=False):
    fmt = "%Y-%m-%d %H:%M" if clock else "%Y-%m-%d"
    return pd.to_datetime(series, utc=True, errors="coerce").dt.strftime(fmt).fillna("")


def export(df, filename, key):
    st.download_button("Export CSV", df.to_csv(index=False).encode(), filename,
                       mime="text/csv", key=key)


def table(df, formats=None, signed=()):
    styled = df.style.format(formats or {})
    if signed:
        styled = styled.map(pnl_style, subset=list(signed))
    st.dataframe(styled, hide_index=True, use_container_width=True, height=440)


def chart_options(key, choices):
    a, b = st.columns([2, 1])
    return (
        a.selectbox("Graph", [*choices, "None"], key=f"{key}_chart"),
        b.selectbox("Group by", list(FREQUENCIES), key=f"{key}_group"),
    )


def time_chart(df, time_column, measure, mode, frequency, split=False):
    data = df.copy()
    data["date"] = pd.to_datetime(data[time_column], utc=True, errors="coerce")
    data = data.dropna(subset=["date"])
    if data.empty:
        st.info("No dated records to chart.")
        return
    group = [pd.Grouper(key="date", freq=FREQUENCIES[frequency])]
    if split:
        group.append("side")
    data = data.groupby(group)[measure].sum().reset_index().rename(columns={measure: "value"})
    gold = mode in ("Profit / loss", "Cumulative profit", "Transaction value")
    if gold:
        data["value"] /= 10000
    if mode == "Cumulative profit":
        data["value"] = data["value"].cumsum()
    unit = "Gold" if gold else "Items"
    base = alt.Chart(data).encode(
        x=alt.X("date:T", title=None),
        tooltip=[alt.Tooltip("date:T", title="Period", format="%d %b %Y"),
                 alt.Tooltip("value:Q", title=unit, format=",.2f" if gold else ",")],
    )
    if mode == "Cumulative profit":
        chart = base.mark_line(color=BLUE, point=True).encode(y=alt.Y("value:Q", title=unit))
    elif split:
        chart = base.mark_bar().encode(
            y=alt.Y("value:Q", title=unit),
            color=alt.Color("side:N", scale=alt.Scale(domain=["buys", "sells"],
                                                      range=[GREY, BLUE])),
            tooltip=["side:N", alt.Tooltip("date:T", title="Period"),
                     alt.Tooltip("value:Q", title=unit, format=",.2f" if gold else ",")],
        )
    else:
        palette = (alt.condition(alt.datum.value >= 0, alt.value(GREEN), alt.value(RED))
                   if mode == "Profit / loss" else alt.value(BLUE))
        chart = base.mark_bar().encode(y=alt.Y("value:Q", title=unit), color=palette)
    st.altair_chart(chart.properties(height=275), use_container_width=True)


def show_wealth(latest):
    st.subheader("Account net worth")
    metrics([(label, latest[field], "coins") for label, field in WEALTH.items()])
    st.divider()
    st.markdown("#### Wealth history")
    a, b = st.columns([2, 1])
    series = a.selectbox("Series", list(WEALTH), key="wealth_series")
    period = b.selectbox("Period", ["30 days", "90 days", "365 days", "All time"],
                         index=3, key="wealth_period")
    history = query("SELECT snapshot_at, " + ", ".join(WEALTH.values()) +
                    " FROM account_snapshots ORDER BY snapshot_at")
    if not history.empty:
        history["snapshot_at"] = pd.to_datetime(history["snapshot_at"], utc=True)
        if period != "All time":
            cutoff = datetime.now(timezone.utc) - timedelta(days=int(period.split()[0]))
            history = history.loc[history["snapshot_at"] >= cutoff]
        history["gold"] = pd.to_numeric(history[WEALTH[series]], errors="coerce") / 10000
        if history.empty:
            st.info("No snapshots in this period.")
        else:
            graph = alt.Chart(history).mark_line(color=BLUE, point=True).encode(
                x=alt.X("snapshot_at:T", title=None),
                y=alt.Y("gold:Q", title="Gold", scale=alt.Scale(zero=False)),
                tooltip=[alt.Tooltip("snapshot_at:T", title="Snapshot"),
                         alt.Tooltip("gold:Q", title="Gold", format=",.2f")],
            ).properties(height=300)
            st.altair_chart(graph, use_container_width=True)
    else:
        st.info("No historical snapshots available.")

    st.markdown("#### Wealth composition")
    composition = pd.DataFrame([{"Component": name, "Copper": number(latest.get(field))}
                                for name, field in COMPOSITION.items()])
    composition["Gold"] = composition["Copper"] / 10000
    left, right = st.columns([1, 2])
    with left:
        display = composition[["Component"]].copy()
        display["Value"] = composition["Copper"].map(coins)
        st.dataframe(display, hide_index=True, use_container_width=True, height=355)
    with right:
        graph = alt.Chart(composition).mark_bar(color=BLUE).encode(
            x=alt.X("Gold:Q", title="Gold"),
            y=alt.Y("Component:N", title=None, sort="-x"),
            tooltip=["Component:N", alt.Tooltip("Gold:Q", format=",.2f")],
        ).properties(height=335)
        st.altair_chart(graph, use_container_width=True)


def show_profit_loss():
    st.subheader("Realised profit & loss")
    with st.container():
        a, b, c = st.columns([1, 3, 1])
        with a:
            window = date_filter("pl")
        where, params = period_sql("sell_date", window)
        df = query(f"SELECT id, item_id, quantity, buy_price, sell_price, profit, buy_date, sell_date "
                   f"FROM matched_flips WHERE {where} "
                   "ORDER BY sell_date DESC NULLS LAST, id DESC", params)
        df = item_names(df)
        with b:
            df = item_filter(df, "pl")
        result = c.selectbox("Result", ["All", "Profit", "Loss", "Breakeven"], key="pl_result")

    if result != "All":
        predicate = {"Profit": df["profit"] > 0, "Loss": df["profit"] < 0,
                     "Breakeven": df["profit"] == 0}[result]
        df = df.loc[predicate].copy()
    df["cost"] = df["buy_price"] * df["quantity"]
    df["net_proceeds"] = df["cost"] + df["profit"]
    df["roi"] = (100 * df["profit"] / df["cost"].replace(0, float("nan"))).fillna(0)
    metrics([("Realised P/L", df["profit"].sum(), "pnl"),
             ("Cost basis", df["cost"].sum(), "coins"),
             ("Net proceeds", df["net_proceeds"].sum(), "coins"),
             ("Matched volume", df["quantity"].sum(), "count"),
             ("Matched rows", len(df), "count")])
    st.caption("FIFO realised P/L after Trading Post fees. Unmatched sales are excluded.")
    if df.empty:
        st.info("No matching records.")
        return
    st.markdown("#### Historical analysis")
    mode, freq = chart_options("pl", ["Profit / loss", "Cumulative profit", "Item volume"])
    if mode != "None":
        time_chart(df, "sell_date", "quantity" if mode == "Item volume" else "profit", mode, freq)
    st.markdown("#### Matched transactions")
    display = df.rename(columns={"quantity": "Qty", "buy_price": "Buy/unit",
                                   "sell_price": "Sell/unit", "cost": "Cost",
                                   "net_proceeds": "Net proceeds", "profit": "P/L", "roi": "ROI (%)",
                                   "item_name": "Item"}).copy()
    display["Sell date"] = dates(df["sell_date"])
    fields = ["Sell date", "Item", "Qty", "Buy/unit", "Sell/unit", "Cost", "Net proceeds", "P/L", "ROI (%)"]
    table(display[fields], {**{k: coins for k in ("Buy/unit", "Sell/unit", "Cost", "Net proceeds")},
                            "P/L": lambda v: coins(v, signed=True), "ROI (%)": "{:+.1f}%"},
          signed=("P/L", "ROI (%)"))
    export(df, "gw2_profit_loss.csv", "pl_export")


def show_transactions():
    st.subheader("Trading Post transactions")
    a, b, c = st.columns(3)
    with a:
        window = date_filter("tx")
    side = b.selectbox("Side", ["All", "buys", "sells"], key="tx_side")
    state = c.selectbox("State", ["history", "current", "All"], key="tx_state")
    clauses = []
    params = {}
    if side != "All":
        clauses.append("side = :side")
        params["side"] = side
    if state != "All":
        clauses.append("transaction_set = :state")
        params["state"] = state
    where, bounds = period_sql("COALESCE(purchased, created)", window, clauses)
    params.update(bounds)
    df = query("SELECT transaction_id, side, transaction_set, item_id, price, quantity, "
               f"created, purchased FROM tp_transactions WHERE {where} "
               "ORDER BY COALESCE(purchased, created) DESC NULLS LAST, transaction_id DESC", params)
    df = item_filter(item_names(df), "tx")
    if df.empty:
        st.info("No matching transactions.")
        return
    df["total"] = df["price"] * df["quantity"]
    metrics([("Transactions", len(df), "count"), ("Item quantity", df["quantity"].sum(), "count"),
             ("Buy value", df.loc[df["side"] == "buys", "total"].sum(), "coins"),
             ("Sell value", df.loc[df["side"] == "sells", "total"].sum(), "coins")])
    st.caption("Values are gross Trading Post order values. Current orders may be unfilled.")
    st.markdown("#### Transaction activity")
    mode, freq = chart_options("tx", ["Item volume", "Transaction value"])
    if mode != "None":
        df["activity_date"] = df["purchased"].combine_first(df["created"])
        time_chart(df, "activity_date", "quantity" if mode == "Item volume" else "total",
                   mode, freq, split=True)
    st.markdown("#### Transaction history")
    display = pd.DataFrame({
        "Date": dates(df["purchased"].combine_first(df["created"]), clock=True),
        "Item": df["item_name"], "Side": df["side"], "State": df["transaction_set"],
        "Qty": df["quantity"], "Unit price": df["price"].map(coins),
        "Total": df["total"].map(coins), "Transaction ID": df["transaction_id"],
    })
    table(display)
    export(df, "gw2_transactions.csv", "tx_export")


def show_by_item():
    st.subheader("Performance by item")
    a, b, c = st.columns([1, 2, 1])
    with a:
        window = date_filter("item")
    where, params = period_sql("sell_date", window)
    df = query("SELECT item_id, SUM(quantity)::bigint AS quantity, "
               "SUM(profit)::bigint AS profit, COUNT(*)::bigint AS matched_rows "
               f"FROM matched_flips WHERE {where} GROUP BY item_id", params)
    df = item_names(df)
    with b:
        df = item_filter(df, "item")
    sort = c.selectbox("Sort by", ["Highest profit", "Biggest losses", "Most traded", "Most matches"],
                      key="item_sort")
    if df.empty:
        st.info("No matching items.")
        return
    column, ascending = {
        "Highest profit": ("profit", False), "Biggest losses": ("profit", True),
        "Most traded": ("quantity", False), "Most matches": ("matched_rows", False),
    }[sort]
    df = df.sort_values(column, ascending=ascending).copy()
    df["profit_per_unit"] = (df["profit"] / df["quantity"].replace(0, float("nan"))).fillna(0)
    metrics([("Total realised P/L", df["profit"].sum(), "pnl"),
             ("Items", len(df), "count"), ("Matched volume", df["quantity"].sum(), "count")])
    st.markdown("#### Item comparison")
    left, right = st.columns([2, 1])
    mode = left.selectbox("Graph", ["Profit / loss", "Item volume", "None"], key="item_chart")
    limit = right.selectbox("Items to display", [10, 15, 25], index=1, key="item_limit")
    if mode != "None":
        top = df.head(limit).copy()
        top["label"] = top["item_name"] + " (" + top["item_id"].astype(str) + ")"
        gold = mode == "Profit / loss"
        top["value"] = top["profit"] / 10000 if gold else top["quantity"]
        fill = (alt.condition(alt.datum.value >= 0, alt.value(GREEN), alt.value(RED))
                if gold else alt.value(BLUE))
        graph = alt.Chart(top).mark_bar().encode(
            x=alt.X("value:Q", title="Gold" if gold else "Items"),
            y=alt.Y("label:N", title=None, sort=None), color=fill,
            tooltip=["label:N", alt.Tooltip("value:Q", title="Gold" if gold else "Items", format=",.2f")],
        ).properties(height=max(280, limit * 25))
        st.altair_chart(graph, use_container_width=True)
    st.markdown("#### Item performance")
    display = df.rename(columns={"item_name": "Item", "item_id": "Item ID",
                                   "quantity": "Matched quantity", "profit": "Realised P/L",
                                   "matched_rows": "Matched rows", "profit_per_unit": "P/L per unit"})
    fields = ["Item", "Item ID", "Matched quantity", "Realised P/L", "Matched rows", "P/L per unit"]
    table(display[fields], {"Realised P/L": lambda v: coins(v, signed=True),
                            "P/L per unit": lambda v: coins(int(v), signed=True)},
          signed=("Realised P/L", "P/L per unit"))
    export(df, "gw2_item_performance.csv", "item_export")


# application

database_url = initial_database_url()
if not database_url:
    st.error("DATABASE_URL is not configured. Add it to your local .env file.")
    st.stop()

try:
    snapshot = query("SELECT * FROM account_snapshots ORDER BY snapshot_at DESC LIMIT 1")
except Exception:
    st.error("Unable to connect to PostgreSQL. Check your local DATABASE_URL and Supabase connection.")
    st.stop()

if snapshot.empty:
    st.warning("No account snapshots available.")
    st.stop()

latest = snapshot.iloc[0]
header, refresh = st.columns([5, 1])
with header:
    st.title("GW2 Account Tracker")
    observed = pd.to_datetime(latest["snapshot_at"], utc=True)
    st.caption(f"Last account snapshot: {observed:%d %B %Y, %H:%M UTC}")
with refresh:
    if st.button("Refresh data", use_container_width=True):
        read_sql.clear()
        st.rerun()

net_tab, tp_tab = st.tabs(["Net Worth", "Trading Post"])
with net_tab:
    show_wealth(latest)
with tp_tab:
    pl_tab, transactions_tab, items_tab = st.tabs(["Profit & Loss", "Transactions", "By Item"])
    with pl_tab:
        show_profit_loss()
    with transactions_tab:
        show_transactions()
    with items_tab:
        show_by_item()