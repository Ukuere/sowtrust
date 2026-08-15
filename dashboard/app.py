"""
Sowtrust Global — CEO Command Console v6.0
Run: streamlit run dashboard/app.py
"""
import sqlite3
import os
import sys
import hashlib
import numpy as np
import pandas as pd
import plotly.express as px
import plotly.graph_objects as go
import streamlit as st
from datetime import datetime, timedelta

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))
from config.settings import config

# ─────────────────────────────────────────────────────────
# PAGE CONFIG
# ─────────────────────────────────────────────────────────
st.set_page_config(
    page_title="Sowtrust — CEO Console",
    layout="wide",
    page_icon="🌾",
    initial_sidebar_state="expanded",
)

st.markdown("""
<style>
  [data-testid="stAppViewContainer"] { background: #0a0f1a; }
  [data-testid="stSidebar"] { background: #060b13; border-right: 1px solid #1a2740; }
  .metric-card {
    background: linear-gradient(135deg,#0d1f2d,#112233);
    border: 1px solid #1e3a5f; border-radius: 12px;
    padding: 20px; text-align: center; margin-bottom: 8px;
  }
  .metric-card .val { font-size: 2rem; font-weight: 800; color: #4ade80; }
  .metric-card .lbl { font-size: 0.8rem; color: #94a3b8; margin-top: 4px; }
  .section-bar {
    background: linear-gradient(90deg,#0d3321,transparent);
    border-left: 4px solid #4ade80; border-radius: 4px;
    padding: 10px 16px; margin: 20px 0 12px 0;
  }
  .section-bar h4 { color: #4ade80; margin: 0; font-size: 1rem; }
  .tag-ok   { background:#064e3b; color:#4ade80; padding:2px 8px; border-radius:9px; font-size:.75rem; }
  .tag-warn { background:#431407; color:#fb923c; padding:2px 8px; border-radius:9px; font-size:.75rem; }
  .tag-lock { background:#1e3a5f; color:#60a5fa; padding:2px 8px; border-radius:9px; font-size:.75rem; }
  div[data-testid="stDataFrame"] { border: 1px solid #1e3a5f; border-radius: 8px; }
</style>
""", unsafe_allow_html=True)

# ─────────────────────────────────────────────────────────
# AUTH GATE
# ─────────────────────────────────────────────────────────
if "authenticated" not in st.session_state:
    st.session_state.authenticated = False

if not st.session_state.authenticated:
    st.markdown("<h1 style='color:#4ade80;text-align:center;margin-top:80px'>🌾 Sowtrust CEO Console</h1>", unsafe_allow_html=True)
    col = st.columns([1, 2, 1])[1]
    with col:
        pwd = st.text_input("Enter Admin Password", type="password")
        if st.button("Login", use_container_width=True):
            if hashlib.sha256(pwd.encode()).hexdigest() == hashlib.sha256(config.DASHBOARD_PASSWORD.encode()).hexdigest():
                st.session_state.authenticated = True
                st.rerun()
            else:
                st.error("Invalid password.")
    st.stop()

# ─────────────────────────────────────────────────────────
# DATABASE ENGINE
# ─────────────────────────────────────────────────────────
@st.cache_resource
def get_conn():
    conn = sqlite3.connect(config.DATABASE_PATH, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    return conn


def rdf(sql, params=()):
    try:
        return pd.read_sql(sql, get_conn(), params=params)
    except Exception:
        return pd.DataFrame()


def run_sql(sql, params=()):
    c = get_conn()
    try:
        c.execute(sql, params)
        c.commit()
        return True
    except Exception as e:
        st.error(f"DB Error: {e}")
        return False


def safe_df(df, cols):
    """Return only the columns that actually exist in df, in the given order."""
    if df.empty:
        return pd.DataFrame()
    available = [c for c in cols if c in df.columns]
    if not available:
        return pd.DataFrame()
    return df[available]


def get_product_names(farmers_df=None):
    """
    Products are now dynamic, not config.CROPS. Read them from the products
    table first, then fall back to farmer crop values for older databases.
    """
    products = rdf("SELECT name FROM products ORDER BY name ASC")
    names = []
    if not products.empty and "name" in products.columns:
        names.extend(products["name"].dropna().astype(str).str.strip().tolist())
    if farmers_df is not None and not farmers_df.empty and "crop" in farmers_df.columns:
        names.extend(farmers_df["crop"].dropna().astype(str).str.strip().tolist())
    names = sorted({name for name in names if name})
    return names or ["Maize", "Rice", "Cassava", "Yam", "Soybeans", "Palm Oil", "Groundnut"]


# ─────────────────────────────────────────────────────────
# LOAD DATA
# ─────────────────────────────────────────────────────────
def load_data():
    farmers = rdf("SELECT * FROM farmers ORDER BY created_at DESC")
    agents = rdf("SELECT * FROM agents ORDER BY recruits DESC")
    escrow = rdf("SELECT * FROM escrow_ledger ORDER BY locked_at DESC")
    requests = rdf("SELECT * FROM buyer_requests ORDER BY created_at DESC")
    audit = rdf("SELECT * FROM audit_log ORDER BY created_at DESC LIMIT 200")
    logistics = rdf("SELECT * FROM logistics_log ORDER BY created_at DESC")
    for df, col in [
        (farmers, "price"),
        (farmers, "balance"),
        (escrow, "amount"),
        (escrow, "service_fee"),
    ]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce").fillna(0.0)
    return farmers, agents, escrow, requests, audit, logistics


farmers_df, agents_df, escrow_df, requests_df, audit_df, logistics_df = load_data()

# ─────────────────────────────────────────────────────────
# SIDEBAR
# ─────────────────────────────────────────────────────────
with st.sidebar:
    st.markdown("<h2 style='color:#4ade80;text-align:center'>🌾 Sowtrust</h2>", unsafe_allow_html=True)
    st.markdown("<p style='color:#64748b;text-align:center;font-size:.8rem'>CEO Console v6.0</p>", unsafe_allow_html=True)
    st.divider()
    page = st.radio(
        "Navigation",
        [
            "📊 Overview",
            "💰 Escrow & Revenue",
            "📦 Buyer Demands",
            "🚚 Logistics",
            "👥 Agent Network",
            "👨‍🌾 Farmer Registry",
            "🤖 Market Intelligence",
            "📋 Audit Log",
            "⚙️ Admin Tools",
        ],
    )
    st.divider()
    if st.button("🔄 Refresh Data", use_container_width=True):
        st.cache_resource.clear()
        st.rerun()
    if st.button("🚪 Logout", use_container_width=True):
        st.session_state.authenticated = False
        st.rerun()

# ─────────────────────────────────────────────────────────
# PAGE HEADER
# ─────────────────────────────────────────────────────────
st.markdown(
    f"""
<div style="background:linear-gradient(135deg,#064e3b,#0a0f1a,#1e3a5f);
            padding:1.5rem 2rem;border-radius:12px;border:1px solid #166534;margin-bottom:1.5rem">
  <h1 style="color:#4ade80;margin:0;font-size:1.8rem">🌾 Sowtrust — {page[2:]}</h1>
  <p style="color:#64748b;margin:4px 0 0 0;font-size:.85rem">{datetime.now().strftime("%A, %d %B %Y  %H:%M")}</p>
</div>
""",
    unsafe_allow_html=True,
)

# ─────────────────────────────────────────────────────────
# OVERVIEW
# ─────────────────────────────────────────────────────────
if page == "📊 Overview":
    locked = escrow_df[escrow_df["status"] == "ESCROW_LOCKED"]["amount"].sum() if not escrow_df.empty else 0
    revenue = escrow_df["service_fee"].sum() if not escrow_df.empty else 0
    delivered = len(escrow_df[escrow_df["status"] == "DELIVERED"]) if not escrow_df.empty else 0

    c1, c2, c3, c4, c5 = st.columns(5)
    for col, val, label in [
        (c1, len(farmers_df), "👨‍🌾 Farmers"),
        (c2, len(agents_df), "👥 Agents"),
        (c3, len(escrow_df), "📦 Total Orders"),
        (c4, f"₦{locked:,.0f}", "🔒 Escrow Locked"),
        (c5, f"₦{revenue:,.0f}", "💰 Revenue Earned"),
    ]:
        col.markdown(
            f'<div class="metric-card"><div class="val">{val}</div><div class="lbl">{label}</div></div>',
            unsafe_allow_html=True,
        )

    st.divider()
    cl, cr = st.columns([2, 1])

    with cl:
        st.markdown('<div class="section-bar"><h4>📈 Escrow Volume Over Time</h4></div>', unsafe_allow_html=True)
        if not escrow_df.empty and "locked_at" in escrow_df.columns:
            ts = escrow_df.copy()
            ts["date"] = pd.to_datetime(ts["locked_at"], errors="coerce").dt.date
            daily = ts.groupby("date")["amount"].sum().reset_index()
            fig = px.area(
                daily,
                x="date",
                y="amount",
                color_discrete_sequence=["#4ade80"],
                labels={"amount": "NGN", "date": "Date"},
            )
            fig.update_layout(
                paper_bgcolor="#0a0f1a",
                plot_bgcolor="#0d1117",
                font_color="#94a3b8",
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig, use_container_width=True)
        else:
            st.info("No transaction data yet.")

    with cr:
        st.markdown('<div class="section-bar"><h4>🌾 Crop Distribution</h4></div>', unsafe_allow_html=True)
        if not farmers_df.empty and "crop" in farmers_df.columns:
            dist = farmers_df["crop"].value_counts().reset_index()
            dist.columns = ["crop", "count"]
            fig2 = px.pie(
                dist,
                names="crop",
                values="count",
                color_discrete_sequence=px.colors.sequential.Greens_r,
            )
            fig2.update_layout(
                paper_bgcolor="#0a0f1a",
                font_color="#94a3b8",
                margin=dict(l=0, r=0, t=0, b=0),
                showlegend=True,
            )
            st.plotly_chart(fig2, use_container_width=True)
        else:
            st.info("No farmer data yet.")

    st.markdown('<div class="section-bar"><h4>📦 Order Status Breakdown</h4></div>', unsafe_allow_html=True)
    if not escrow_df.empty and "status" in escrow_df.columns:
        status_count = escrow_df["status"].value_counts().reset_index()
        status_count.columns = ["status", "count"]
        fig3 = px.bar(
            status_count,
            x="status",
            y="count",
            color="status",
            color_discrete_map={
                "ESCROW_LOCKED": "#60a5fa",
                "DELIVERED": "#4ade80",
                "DISPUTED": "#fb923c",
                "EXPIRED": "#64748b",
            },
            labels={"count": "Orders", "status": "Status"},
        )
        fig3.update_layout(
            paper_bgcolor="#0a0f1a",
            plot_bgcolor="#0d1117",
            font_color="#94a3b8",
            showlegend=False,
            margin=dict(l=0, r=0, t=10, b=0),
        )
        st.plotly_chart(fig3, use_container_width=True)
    else:
        st.info("No order data yet.")

# ─────────────────────────────────────────────────────────
# ESCROW & REVENUE
# ─────────────────────────────────────────────────────────
elif page == "💰 Escrow & Revenue":
    st.markdown('<div class="section-bar"><h4>🔒 Funds Currently in Escrow</h4></div>', unsafe_allow_html=True)
    locked = escrow_df[escrow_df["status"] == "ESCROW_LOCKED"] if not escrow_df.empty else pd.DataFrame()

    if not locked.empty:
        display = safe_df(
            locked,
            [
                "txn_id",
                "farmer_phone",
                "buyer_phone",
                "crop",
                "quantity_bags",
                "amount",
                "service_fee",
                "locked_at",
                "expires_at",
            ],
        )
        st.dataframe(display, use_container_width=True, hide_index=True)

        with st.expander("🔓 Admin — Manual Fund Release"):
            tid = st.selectbox("Select TXN ID", locked["txn_id"].unique())
            reason = st.text_input("Reason for manual release")
            if st.button("✅ Confirm Release", type="primary"):
                if reason:
                    farmer_p = locked[locked["txn_id"] == tid]["farmer_phone"].values[0]
                    amount_v = locked[locked["txn_id"] == tid]["amount"].values[0]
                    fee_v = locked[locked["txn_id"] == tid]["service_fee"].values[0]
                    net = amount_v - fee_v
                    run_sql(
                        "UPDATE escrow_ledger SET status='DELIVERED',released_at=datetime('now') WHERE txn_id=?",
                        (tid,),
                    )
                    run_sql("UPDATE farmers SET balance=balance+? WHERE phone=?", (net, farmer_p))
                    run_sql(
                        "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                        ("ADMIN", "MANUAL_RELEASE", f"TXN:{tid} REASON:{reason}"),
                    )
                    st.success(f"Released! NGN {net:,.0f} credited to farmer.")
                    st.rerun()
                else:
                    st.warning("Please enter a reason.")
    else:
        st.success("✅ No funds currently locked in escrow.")

    st.divider()
    st.markdown('<div class="section-bar"><h4>💰 Revenue Summary</h4></div>', unsafe_allow_html=True)
    if not escrow_df.empty:
        delivered = escrow_df[escrow_df["status"] == "DELIVERED"]
        c1, c2, c3 = st.columns(3)
        c1.metric("Total GMV", f"₦{escrow_df['amount'].sum():,.0f}")
        c2.metric("Revenue (Service Fees)", f"₦{escrow_df['service_fee'].sum():,.0f}")
        c3.metric("Completed Trades", len(delivered))
        display = safe_df(
            escrow_df,
            ["txn_id", "crop", "amount", "service_fee", "status", "locked_at", "released_at"],
        )
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("No revenue data yet.")

# ─────────────────────────────────────────────────────────
# BUYER DEMANDS
# ─────────────────────────────────────────────────────────
elif page == "📦 Buyer Demands":
    st.markdown('<div class="section-bar"><h4>📦 Open Buyer Requests & Matching</h4></div>', unsafe_allow_html=True)
    if not requests_df.empty:
        open_req = requests_df[requests_df["status"] == "OPEN"]
        st.dataframe(open_req, use_container_width=True, hide_index=True)

        if not open_req.empty and not farmers_df.empty:
            st.markdown('<div class="section-bar"><h4>🤝 Auto-Match Engine</h4></div>', unsafe_allow_html=True)
            for _, req in open_req.iterrows():
                matches = farmers_df[
                    (farmers_df["crop"].str.lower() == req["crop"].lower())
                    & (farmers_df["price"] <= (req["max_price"] or 999999999))
                    & (farmers_df["kyc_status"] == "VERIFIED")
                ].sort_values("price")
                if not matches.empty:
                    best = matches.iloc[0]
                    st.success(
                        f"✅ **Match:** {req['crop']} ({req['qty_bags']} bags) — "
                        f"Buyer: `{req['buyer_phone']}` ↔ "
                        f"Farmer: **{best['name']}** @ ₦{best['price']:,.0f}/bag ({best['location']})"
                    )
                else:
                    st.warning(f"⚠️ No verified match for {req['crop']} from buyer `{req['buyer_phone']}`")
    else:
        st.info("No buyer requests yet.")

# ─────────────────────────────────────────────────────────
# LOGISTICS
# ─────────────────────────────────────────────────────────
elif page == "🚚 Logistics":
    st.markdown('<div class="section-bar"><h4>🚚 Active Shipments</h4></div>', unsafe_allow_html=True)
    if not logistics_df.empty:
        st.dataframe(logistics_df, use_container_width=True, hide_index=True)
        in_transit = logistics_df[logistics_df["status"] == "IN_TRANSIT"]
        if not in_transit.empty:
            with st.expander("✅ Mark Shipment as Delivered"):
                lid = st.selectbox("Select Logistics ID", in_transit["logistics_id"].unique())
                if st.button("Confirm Delivery", type="primary"):
                    run_sql(
                        "UPDATE logistics_log SET status='DELIVERED',delivery_timestamp=datetime('now') WHERE logistics_id=?",
                        (lid,),
                    )
                    st.success("Shipment marked as delivered.")
                    st.rerun()
    else:
        st.info("No logistics records yet.")

# ─────────────────────────────────────────────────────────
# AGENT NETWORK
# ─────────────────────────────────────────────────────────
elif page == "👥 Agent Network":
    st.markdown('<div class="section-bar"><h4>👥 Field Agent Performance</h4></div>', unsafe_allow_html=True)
    if not agents_df.empty:
        fig = px.bar(
            agents_df.sort_values("recruits", ascending=False),
            x="name",
            y="recruits",
            color="recruits",
            color_continuous_scale="Greens",
            labels={"recruits": "Verified Farmers", "name": "Agent"},
        )
        fig.update_layout(
            paper_bgcolor="#0a0f1a",
            plot_bgcolor="#0d1117",
            font_color="#94a3b8",
            margin=dict(l=0, r=0, t=10, b=0),
            coloraxis_showscale=False,
        )
        st.plotly_chart(fig, use_container_width=True)

        display = safe_df(
            agents_df,
            ["name", "phone", "location", "recruits", "balance", "is_active", "created_at"],
        )
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("No agents registered yet.")

# ─────────────────────────────────────────────────────────
# FARMER REGISTRY  (previously broken — now fixed and safe)
# ─────────────────────────────────────────────────────────
elif page == "👨‍🌾 Farmer Registry":
    st.markdown('<div class="section-bar"><h4>👨‍🌾 Farmer Database</h4></div>', unsafe_allow_html=True)

    col1, col2 = st.columns(2)
    product_names = get_product_names(farmers_df)
    crop_filter = col1.selectbox("Filter by Crop", ["All"] + product_names)
    kyc_filter = col2.selectbox("Filter by KYC", ["All", "VERIFIED", "PENDING", "SUSPENDED"])

    df = farmers_df.copy()
    if crop_filter != "All" and "crop" in df.columns:
        df = df[df["crop"] == crop_filter]
    if kyc_filter != "All" and "kyc_status" in df.columns:
        df = df[df["kyc_status"] == kyc_filter]

    if not df.empty:
        display = safe_df(
            df,
            [
                "member_uuid",
                "name",
                "phone",
                "crop",
                "location",
                "price",
                "balance",
                "credit_score",
                "kyc_status",
                "created_at",
            ],
        )
        st.dataframe(display, use_container_width=True, hide_index=True)
    else:
        st.info("No farmers registered yet.")

    with st.expander("⚙️ Manage Farmer"):
        phone_in = st.text_input("Farmer Phone")
        action = st.selectbox("Action", ["VERIFY KYC", "SUSPEND", "REACTIVATE"])
        if st.button("Apply", type="primary") and phone_in:
            mapping = {
                "VERIFY KYC": ("kyc_status", "VERIFIED"),
                "SUSPEND": ("kyc_status", "SUSPENDED"),
                "REACTIVATE": ("is_active", 1),
            }
            col_name, val = mapping[action]
            run_sql(f"UPDATE farmers SET {col_name}=? WHERE phone=?", (val, phone_in))
            run_sql(
                "INSERT INTO audit_log(actor,action,details) VALUES(?,?,?)",
                ("ADMIN", action, f"Phone:{phone_in}"),
            )
            st.success(f"Action '{action}' applied to {phone_in}")
            st.rerun()

# ─────────────────────────────────────────────────────────
# MARKET INTELLIGENCE
# ─────────────────────────────────────────────────────────
elif page == "🤖 Market Intelligence":
    st.markdown('<div class="section-bar"><h4>📈 Price Forecast & Market Simulation</h4></div>', unsafe_allow_html=True)
    product_names = get_product_names(farmers_df)
    crop_sel = st.selectbox("Select Crop to Analyse", product_names)

    np.random.seed(42)
    dates = pd.date_range(end=datetime.today(), periods=30)
    base = {
        "Maize": 145000,
        "Rice": 380000,
        "Cassava": 65000,
        "Yam": 250000,
        "Soybeans": 430000,
        "Palm Oil": 320000,
        "Groundnut": 290000,
    }
    prices = base.get(crop_sel, 150000) + np.cumsum(np.random.normal(200, 2500, 30))

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=dates,
            y=prices,
            mode="lines+markers",
            line=dict(color="#4ade80", width=2),
            fill="tozeroy",
            fillcolor="rgba(74,222,128,0.08)",
            name="Market Price",
        )
    )
    future_dates = pd.date_range(start=dates[-1] + timedelta(days=1), periods=7)
    future_prices = prices[-1] + np.cumsum(np.random.normal(300, 2000, 7))
    fig.add_trace(
        go.Scatter(
            x=future_dates,
            y=future_prices,
            mode="lines",
            line=dict(color="#fb923c", width=2, dash="dot"),
            name="7-Day Forecast",
        )
    )
    fig.update_layout(
        paper_bgcolor="#0a0f1a",
        plot_bgcolor="#0d1117",
        font_color="#94a3b8",
        legend=dict(bgcolor="#0d1117"),
        margin=dict(l=0, r=0, t=10, b=0),
        yaxis_title="NGN per Bag",
        xaxis_title="Date",
    )
    st.plotly_chart(fig, use_container_width=True)

    c1, c2, c3 = st.columns(3)
    c1.metric("Current Price", f"₦{prices[-1]:,.0f}")
    c2.metric(
        "7-Day Forecast",
        f"₦{future_prices[-1]:,.0f}",
        delta=f"₦{future_prices[-1] - prices[-1]:+,.0f}",
    )
    c3.metric("30-Day High", f"₦{max(prices):,.0f}")

    if not farmers_df.empty:
        st.divider()
        st.markdown('<div class="section-bar"><h4>📊 Active Price Listings</h4></div>', unsafe_allow_html=True)
        crop_farmers = farmers_df[(farmers_df["crop"] == crop_sel) & (farmers_df["price"] > 0)]
        if not crop_farmers.empty:
            fig2 = px.bar(
                crop_farmers.sort_values("price"),
                x="name",
                y="price",
                color="location",
                labels={"price": "Price (NGN)", "name": "Farmer"},
                color_discrete_sequence=px.colors.sequential.Greens_r,
            )
            fig2.update_layout(
                paper_bgcolor="#0a0f1a",
                plot_bgcolor="#0d1117",
                font_color="#94a3b8",
                margin=dict(l=0, r=0, t=10, b=0),
            )
            st.plotly_chart(fig2, use_container_width=True)

# ─────────────────────────────────────────────────────────
# AUDIT LOG
# ─────────────────────────────────────────────────────────
elif page == "📋 Audit Log":
    st.markdown('<div class="section-bar"><h4>📋 Full Audit Trail</h4></div>', unsafe_allow_html=True)
    if not audit_df.empty:
        action_filter = st.selectbox(
            "Filter by Action", ["All"] + sorted(audit_df["action"].unique().tolist())
        )
        df = audit_df if action_filter == "All" else audit_df[audit_df["action"] == action_filter]
        st.dataframe(df, use_container_width=True, hide_index=True)
    else:
        st.info("No audit entries yet.")

# ─────────────────────────────────────────────────────────
# ADMIN TOOLS
# ─────────────────────────────────────────────────────────
elif page == "⚙️ Admin Tools":
    st.markdown('<div class="section-bar"><h4>⚙️ Platform Administration</h4></div>', unsafe_allow_html=True)

    with st.expander("📊 Platform Governance & Compliance"):
        st.markdown(
            f"""
| Setting | Value |
|---|---|
| Service Fee | {config.SERVICE_FEE_PERCENT}% on all successful transactions |
| Escrow Period | {config.ESCROW_EXPIRY_HOURS} hours post-delivery verification |
| Security | SHA-256 PIN hashing |
| USSD Session TTL | {config.USSD_SESSION_TTL} seconds |
| Database | ACID-compliant (WAL mode) |
        """
        )

    with st.expander("📤 Export Data"):
        if not farmers_df.empty:
            st.download_button(
                "⬇️ Export Farmers CSV",
                farmers_df.to_csv(index=False),
                "farmers_export.csv",
                "text/csv",
            )
        if not escrow_df.empty:
            st.download_button(
                "⬇️ Export Escrow Ledger CSV",
                escrow_df.to_csv(index=False),
                "escrow_export.csv",
                "text/csv",
            )

    with st.expander("🧹 Expire Stale Escrows"):
        count = rdf(
            "SELECT COUNT(*) as n FROM escrow_ledger WHERE status='ESCROW_LOCKED' AND expires_at < datetime('now')"
        )
        n = count["n"].values[0] if not count.empty else 0
        st.warning(f"{n} escrow(s) past expiry.")
        if st.button("Run Expiry Job") and n > 0:
            run_sql(
                "UPDATE escrow_ledger SET status='EXPIRED' WHERE status='ESCROW_LOCKED' AND expires_at < datetime('now')"
            )
            st.success(f"Marked {n} escrows as EXPIRED.")
            st.rerun()
