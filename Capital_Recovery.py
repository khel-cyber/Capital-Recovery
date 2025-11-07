
import streamlit as st
import pandas as pd
from datetime import datetime, timedelta
from pathlib import Path
import matplotlib.pyplot as plt
from io import BytesIO
import smtplib
from email.message import EmailMessage
import base64

# --------------------------
# Core logic (ported from notebook)
# --------------------------

OUTPUT_DIR = Path.cwd() / "capital_recovery_output"
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
EXCEL_PATH = OUTPUT_DIR / "capital_recovery_full_log.xlsx"


def process_day(record_date, capital_remaining, initial_capital, revenue, operating_costs_list,
                recovery_plan="fixed_days", recovery_days=365, recovery_percentage=None):
    total_operating_costs = round(sum(operating_costs_list), 2)
    net_profit_before_recovery = round(revenue - total_operating_costs, 2)
    allocation = 0.0

    if net_profit_before_recovery <= 0 or capital_remaining <= 0:
        allocation = 0.0
    else:
        if recovery_plan == "fixed_days":
            allocation = round(initial_capital / recovery_days, 2)
        elif recovery_plan == "percent_of_profit":
            if recovery_percentage is None:
                raise ValueError("recovery_percentage required for percent_of_profit plan")
            allocation = round(net_profit_before_recovery * (recovery_percentage / 100.0), 2)
        elif recovery_plan == "all_profit":
            allocation = round(net_profit_before_recovery, 2)
        else:
            raise ValueError("Unknown recovery_plan")

        allocation = min(allocation, net_profit_before_recovery, capital_remaining)

    profit_after_recovery = round(net_profit_before_recovery - allocation, 2)
    capital_remaining_after = round(capital_remaining - allocation, 2)
    recovered_flag = capital_remaining_after <= 0

    return {
        "date": pd.to_datetime(record_date).date(),
        "revenue": revenue,
        "operating_costs": total_operating_costs,
        "net_profit_before_recovery": net_profit_before_recovery,
        "allocated_to_recovery": allocation,
        "profit_after_recovery": profit_after_recovery,
        "capital_remaining_after": capital_remaining_after,
        "recovered_flag": recovered_flag
    }


class BusinessTracker:
    def __init__(self, name, initial_capital, recovery_plan="fixed_days",
                 recovery_days=365, recovery_percentage=None):
        self.name = name
        self.initial_capital = float(initial_capital)
        self.recovery_plan = recovery_plan
        self.recovery_days = recovery_days
        self.recovery_percentage = recovery_percentage
        self.records = []
        self.capital_remaining = float(initial_capital)

    def add_entry(self, date, revenue, operating_costs_list):
        rec = process_day(date, self.capital_remaining, self.initial_capital,
                          revenue, operating_costs_list,
                          recovery_plan=self.recovery_plan,
                          recovery_days=self.recovery_days,
                          recovery_percentage=self.recovery_percentage)
        rec["cumulative_recovered"] = round(self.initial_capital - rec["capital_remaining_after"], 2)
        rec["capital_start_of_day"] = round(self.capital_remaining, 2)
        self.records.append(rec)
        self.capital_remaining = max(0.0, rec["capital_remaining_after"])
        return rec

    def load_from_csv(self, csv_path, date_col="date", revenue_col="revenue", costs_col="operating_costs"):
        df = pd.read_csv(csv_path)
        for _, row in df.iterrows():
            date = row[date_col]
            revenue = float(row[revenue_col])
            costs_raw = row[costs_col] if costs_col in row.index else 0.0
            if pd.isna(costs_raw):
                costs_list = [0.0]
            elif isinstance(costs_raw, str) and ";" in costs_raw:
                costs_list = [float(x.strip()) for x in costs_raw.split(";") if x.strip()]
            else:
                costs_list = [float(costs_raw)]
            self.add_entry(date, revenue, costs_list)
        return pd.DataFrame(self.records)

    def to_dataframe(self):
        if not self.records:
            return pd.DataFrame(columns=[
                "date", "capital_start_of_day", "revenue", "operating_costs",
                "net_profit_before_recovery", "allocated_to_recovery", "profit_after_recovery",
                "cumulative_recovered", "capital_remaining_after", "recovered_flag"
            ])
        df = pd.DataFrame(self.records)
        ordered_cols = [
            "date", "capital_start_of_day", "revenue", "operating_costs",
            "net_profit_before_recovery", "allocated_to_recovery", "profit_after_recovery",
            "cumulative_recovered", "capital_remaining_after", "recovered_flag"
        ]
        return df[ordered_cols]

    def save_to_excel_writer(self, writer):
        df = self.to_dataframe()
        sheet_name = self.name[:31]
        df.to_excel(writer, sheet_name=sheet_name, index=False)
        return df

    def plot_summary(self):
        df = self.to_dataframe()
        if df.empty:
            return None
        df = df.sort_values("date")

        fig1, ax1 = plt.subplots()
        ax1.plot(df["date"], df["net_profit_before_recovery"], marker="o")
        ax1.set_title(f"Net Profit Before Recovery - {self.name}")
        ax1.set_xlabel("Date")
        ax1.set_ylabel("Net Profit ($)")
        fig1.tight_layout()

        fig2, ax2 = plt.subplots()
        ax2.plot(df["date"], df["cumulative_recovered"], marker="o")
        ax2.set_title(f"Cumulative Recovered Capital - {self.name}")
        ax2.set_xlabel("Date")
        ax2.set_ylabel("Cumulative Recovered ($)")
        fig2.tight_layout()

        return fig1, fig2


# --------------------------
# Utilities
# --------------------------

def save_all_businesses_to_excel(business_trackers, excel_path=EXCEL_PATH):
    with pd.ExcelWriter(excel_path, engine="openpyxl") as writer:
        for bt in business_trackers:
            bt.save_to_excel_writer(writer)
    return excel_path


def send_email_report(smtp_host, smtp_port, username, password,
                      to_addrs, subject, body, attachments=None, use_tls=True):
    msg = EmailMessage()
    msg["From"] = username
    msg["To"] = to_addrs if isinstance(to_addrs, str) else ", ".join(to_addrs)
    msg["Subject"] = subject
    msg.set_content(body)

    if attachments:
        for path in attachments:
            path = Path(path)
            if not path.exists():
                continue
            with open(path, "rb") as f:
                data = f.read()
            msg.add_attachment(data, maintype="application", subtype="octet-stream", filename=path.name)

    try:
        if use_tls:
            server = smtplib.SMTP(smtp_host, smtp_port, timeout=30)
            server.starttls()
        else:
            server = smtplib.SMTP_SSL(smtp_host, smtp_port, timeout=30)
        server.login(username, password)
        server.send_message(msg)
        server.quit()
        return True
    except Exception as e:
        st.error(f"Failed to send email: {e}")
        return False


# --------------------------
# Streamlit UI
# --------------------------

st.set_page_config(page_title="Capital Recovery Tracker", layout="wide")
st.title("Capital Recovery Tracker — Streamlit")

# Keep trackers in session state
if "businesses" not in st.session_state:
    st.session_state.businesses = {}

# Sidebar: create/manage businesses
st.sidebar.header("Manage Businesses")
with st.sidebar.form("create_business_form"):
    name = st.text_input("Business name")
    initial_capital = st.number_input("Initial capital ($)", min_value=0.0, value=10000.0, step=100.0)
    plan = st.selectbox("Recovery plan", ["fixed_days", "percent_of_profit", "all_profit"])
    recovery_days = None
    recovery_percentage = None
    if plan == "fixed_days":
        recovery_days = st.number_input("Days to recover (for fixed_days)", min_value=1, value=365)
    elif plan == "percent_of_profit":
        recovery_percentage = st.number_input("Percent of profit to allocate (e.g., 25)", min_value=0.0, max_value=100.0, value=25.0)

    create = st.form_submit_button("Create / Update Business")
    if create and name:
        bt = BusinessTracker(name, initial_capital, recovery_plan=plan,
                             recovery_days=recovery_days if recovery_days else 365,
                             recovery_percentage=recovery_percentage)
        st.session_state.businesses[name] = bt
        st.sidebar.success(f"Business '{name}' created/updated.")

# Select a business to work on
if st.session_state.businesses:
    selected = st.sidebar.selectbox("Choose business", list(st.session_state.businesses.keys()))
    bt = st.session_state.businesses[selected]

    st.sidebar.markdown("---")
    st.sidebar.subheader("Add Entry / Upload CSV")
    with st.sidebar.form("add_entry_form"):
        date_input = st.date_input("Entry date", value=datetime.today())
        revenue = st.number_input("Revenue ($)", min_value=0.0, value=0.0, step=10.0)
        costs_input = st.text_input("Operating costs (comma or ; separated)", value="0")
        add_entry = st.form_submit_button("Add entry")
        if add_entry:
            # parse costs
            raw = costs_input.replace(";", ",")
            costs = [float(x.strip()) for x in raw.split(",") if x.strip()]
            bt.add_entry(date_input, float(revenue), costs)
            st.sidebar.success("Entry added.")

    with st.sidebar.form("upload_csv_form"):
        uploaded = st.file_uploader("Upload CSV to add multiple entries", type=["csv"])
        up_button = st.form_submit_button("Upload and process")
        if up_button and uploaded is not None:
            # save to temp buffer and pass to loader
            df_uploaded = pd.read_csv(uploaded)
            # write to a temp csv file path-like object
            temp_path = OUTPUT_DIR / f"{selected}_uploaded.csv"
            df_uploaded.to_csv(temp_path, index=False)
            bt.load_from_csv(temp_path)
            st.sidebar.success("CSV processed and entries added.")

    st.sidebar.markdown("---")
    if st.sidebar.button("Save all businesses to Excel"):
        save_all_businesses_to_excel(list(st.session_state.businesses.values()), EXCEL_PATH)
        st.sidebar.success(f"Saved to {EXCEL_PATH}")

    # Email scaffold
    with st.sidebar.expander("Email report (scaffold)"):
        smtp_host = st.text_input("SMTP host", value="smtp.example.com")
        smtp_port = st.number_input("SMTP port", value=587)
        smtp_user = st.text_input("SMTP username")
        smtp_pass = st.text_input("SMTP password", type="password")
        to_addrs = st.text_input("To (comma separated)")
        email_subject = st.text_input("Subject", value="Capital Recovery Report")
        email_body = st.text_area("Body", value="Please find the attached report.")
        if st.button("Send email report"):
            attachments = []
            save_all_businesses_to_excel(list(st.session_state.businesses.values()), EXCEL_PATH)
            attachments.append(EXCEL_PATH)
            success = send_email_report(smtp_host, smtp_port, smtp_user, smtp_pass,
                                        [a.strip() for a in to_addrs.split(",") if a.strip()],
                                        email_subject, email_body, attachments=attachments)
            if success:
                st.sidebar.success("Email sent (or attempted).")

else:
    st.info("No businesses yet. Create one in the sidebar.")

# Main area: show selected business details and charts
if st.session_state.businesses:
    st.header(f"Business: {bt.name}")

    df = bt.to_dataframe()
    st.subheader("Records")
    if df.empty:
        st.write("No records yet. Add entries via the sidebar or upload a CSV.")
    else:
        st.dataframe(df)

        st.subheader("Charts")
        figs = bt.plot_summary()
        if figs:
            fig1, fig2 = figs
            st.pyplot(fig1)
            st.pyplot(fig2)

        # Allow user to download the business sheet only
        to_download = df.copy()
        buf = BytesIO()
        to_download.to_csv(buf, index=False)
        buf.seek(0)
        b64 = base64.b64encode(buf.read()).decode()
        href = f"data:file/csv;base64,{b64}"
        st.markdown(f"[Download CSV for this business]({href})")

