import streamlit as st
import pandas as pd
import datetime
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import json
from dotenv import load_dotenv

# Page Config
st.set_page_config(page_title="Inventory vs Production", layout="wide")


# ── Utility Functions ──────────────────────────────────────────────────────────

def safe_to_numeric(series, strip_chars='₹,'):
    """Convert a Series to numeric, stripping currency symbols and commas."""
    cleaned = series.astype(str)
    for ch in strip_chars:
        cleaned = cleaned.str.replace(ch, '', regex=False)
    return pd.to_numeric(cleaned.str.strip(), errors='coerce')


def safe_to_date(series, fmt='%m/%d/%Y'):
    """Convert a Series to Python date objects."""
    return pd.to_datetime(series, format=fmt, errors='coerce').dt.date


def highlight_mismatch(row):
    """Highlight mismatched Inventory vs Production values in red."""
    colors = [''] * len(row)
    inv_val = row['Inventory']
    prod_val = row['Production']

    col_map = {col: idx for idx, col in enumerate(row.index)}
    inv_idx = col_map['Inventory']
    prod_idx = col_map['Production']

    if pd.isna(inv_val) or pd.isna(prod_val):
        colors[inv_idx] = 'color: #ff0000'
        colors[prod_idx] = 'color: #ff0000'
    elif isinstance(inv_val, (int, float)) and isinstance(prod_val, (int, float)):
        if abs(inv_val - prod_val) > 0.01:
            colors[inv_idx] = 'color: #ff0000'
            colors[prod_idx] = 'color: #ff0000'

    return colors


def render_inv_prod_tab(header, inv_filtered, prod_filtered, inv_filters,
                        prod_column):
    """
    Render a reconciliation tab for Inventory vs Production.

    Args:
        header: Tab header text
        inv_filtered: Date-filtered inventory DataFrame
        prod_filtered: Date-filtered production DataFrame
        inv_filters: Dict of {column: value} filters to apply to inventory
        prod_column: Production column to compare against Inventory Quantity
    """
    st.header(header)

    # Apply inventory filters
    mask = pd.Series(True, index=inv_filtered.index)
    for col, val in inv_filters.items():
        mask &= (inv_filtered[col] == val)
    inv_tab = inv_filtered.loc[mask]

    prod_tab = prod_filtered.copy()

    # Merge on Date + Label / Coffee Type
    merged = inv_tab.merge(
        prod_tab,
        left_on=['Date', 'Label'],
        right_on=['Date', 'Coffee Type'],
        how='outer'
    )[['Date', 'Label', 'Quantity', 'Coffee Type', prod_column]]

    merged = merged.rename(columns={'Quantity': 'Inventory', prod_column: 'Production'})
    merged = merged.round(2)

    # Detailed Comparison
    st.subheader("Detailed Comparison")
    st.dataframe(
        merged.style
        .apply(highlight_mismatch, axis=1)
        .format({"Inventory": "{:.2f}", "Production": "{:.2f}"}),
        use_container_width=True
    )

    # Totals side-by-side
    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Inventory Total")
        total_inv = inv_tab.groupby('Label')['Quantity'].sum().reset_index()
        total_inv = total_inv.rename(columns={'Quantity': 'Inventory'})
        st.dataframe(total_inv.style.format({"Inventory": "{:.2f}"}), use_container_width=True)

    with col2:
        st.subheader("Production Total")
        total_prod = prod_tab.groupby('Coffee Type')[prod_column].sum().reset_index()
        total_prod = total_prod.rename(columns={prod_column: 'Production'})
        st.dataframe(total_prod.style.format({"Production": "{:.2f}"}), use_container_width=True)

    # Variance
    st.subheader("Variance")
    validation = total_inv.merge(
        total_prod,
        left_on='Label',
        right_on='Coffee Type',
        how='outer'
    ).fillna(0)

    validation['Difference'] = (validation['Inventory'] - validation['Production']).abs()
    validation = validation.round(2)

    st.dataframe(
        validation.style
        .format({"Inventory": "{:.2f}", "Production": "{:.2f}", "Difference": "{:.2f}"}),
        use_container_width=True
    )


def _check_required_columns(df, required_cols, sheet_name):
    """Check that required columns exist in a DataFrame. Returns list of missing cols."""
    missing = [c for c in required_cols if c not in df.columns]
    if missing:
        st.error(f"'{sheet_name}' sheet is missing columns: {missing}")
    return missing


# ── Sidebar Navigation ────────────────────────────────────────────────────────

st.sidebar.header("Overview")
if st.sidebar.button("Refresh Data", key="refresh_data_top"):
    st.cache_data.clear()


# ── GSpread Authentication ─────────────────────────────────────────────────────

@st.cache_resource
def get_gspread_client():
    scope = ["https://www.googleapis.com/auth/spreadsheets.readonly", "https://www.googleapis.com/auth/drive"]

    # 1. Check if we are running on Streamlit Cloud
    try:
        if "gcp_service_account" in st.secrets:
            # Streamlit Cloud provides secrets as a dictionary automatically
            creds_dict = st.secrets["gcp_service_account"]
        else:
            creds_dict = None
    except Exception:
        # st.secrets access fails if no secrets.toml exists (local dev)
        creds_dict = None

    if creds_dict is None:
        # Fallback for local development
        load_dotenv()
        creds_json_str = os.getenv("GOOGLE_CREDENTIALS_DICT")
        if creds_json_str:
            creds_dict = json.loads(creds_json_str)
        elif os.path.exists("credentials.json"):
            # Fallback to credentials.json file
            with open("credentials.json") as f:
                creds_dict = json.load(f)
        else:
            st.error("Google Credentials not found. Please set GOOGLE_CREDENTIALS_DICT in .env or provide credentials.json in the root directory.")
            st.stop()

    credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(credentials)
    return client

client = get_gspread_client()

app_mode = st.sidebar.radio("Go to", ["Inventory vs Production", "Dispatch vs Full Tracker", "Packets and Packing Materials"])


# ══════════════════════════════════════════════════════════════════════════════
#  MODE 1: Inventory vs Production
# ══════════════════════════════════════════════════════════════════════════════

if app_mode == "Inventory vs Production":
    st.title("Inventory vs Production Reconciliation")

    # --- Data Loading (parallel) ---
    @st.cache_data
    def load_inv_prod_data():
        def fetch_inventory():
            sh = client.open_by_key("1JTeE3dwYJj6cXGFF-MUWsmyX3aD3sHwh4dBR0KEgMVQ")
            return pd.DataFrame(sh.worksheet("Coffees").get_all_records())

        def fetch_production():
            sh = client.open_by_key("1C8nwOXF1u944Km_5DcG34ntvmuWx9JBPhdyvsZT2bC8")
            return pd.DataFrame(sh.worksheet("Coffee").get_all_records())

        inventory = pd.DataFrame()
        production = pd.DataFrame()

        with ThreadPoolExecutor(max_workers=2) as executor:
            inv_future = executor.submit(fetch_inventory)
            prod_future = executor.submit(fetch_production)

            try:
                inventory = inv_future.result()
            except Exception as e:
                st.error(f"Error loading 'Nandanvan Inventory': {e}")

            try:
                production = prod_future.result()
            except Exception as e:
                st.error(f"Error loading 'Nandanvan Production': {e}")

        return inventory, production

    try:
        inventory_df, production_df = load_inv_prod_data()
        if inventory_df.empty or production_df.empty:
            st.error("Data could not be loaded. Please check the logs/errors above.")
            st.stop()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    # --- Column Guards ---
    inv_required = ['Date', 'Quantity', 'Inventory Category', 'Transaction Type', 'Sold to Customer', 'Beans', 'Label']
    prod_required = ['Date', 'Coffee Type', 'Raw Coffee Taken', 'Roasted Output After SortOut (kg)', 'Lilmore Output (kg)']

    if _check_required_columns(inventory_df, inv_required, 'Inventory') or \
       _check_required_columns(production_df, prod_required, 'Production'):
        st.stop()

    # --- Preprocessing ---
    inventory_df['Date'] = safe_to_date(inventory_df['Date'])
    production_df['Date'] = safe_to_date(production_df['Date'])

    # Drop rows with invalid dates
    inventory_df = inventory_df.dropna(subset=['Date'])
    production_df = production_df.dropna(subset=['Date'])

    if inventory_df.empty or production_df.empty:
        st.error("Data is empty after date parsing. Please check the date format in the sheets.")
        st.stop()

    # Convert numeric columns
    inventory_df['Quantity'] = safe_to_numeric(inventory_df['Quantity'])

    for col in ['Raw Coffee Taken', 'Roasted Output After SortOut (kg)', 'Lilmore Output (kg)']:
        production_df[col] = safe_to_numeric(production_df[col])

    # Create "is L'Lmore" column in inventory
    inventory_df["is L'Lmore"] = inventory_df['Beans'].str.contains("L'Lmore", na=False)

    # --- Sidebar Filters ---
    st.sidebar.header("Filters")
    st.sidebar.markdown("<br>" * 10, unsafe_allow_html=True)  # Spacing for date picker

    min_date = min(inventory_df['Date'].min(), production_df['Date'].min())
    max_date = max(inventory_df['Date'].max(), production_df['Date'].max())

    if isinstance(min_date, pd.Timestamp): min_date = min_date.date()
    if isinstance(max_date, pd.Timestamp): max_date = max_date.date()

    start_date = st.sidebar.date_input("Start Date", min_date, key="inv_start_date")
    end_date = st.sidebar.date_input("End Date", max_date, key="inv_end_date")

    # Filter by date
    inv_filtered = inventory_df[(inventory_df['Date'] >= start_date) & (inventory_df['Date'] <= end_date)].copy()
    prod_filtered = production_df[(production_df['Date'] >= start_date) & (production_df['Date'] <= end_date)].copy()

    # --- Tabs (deduplicated via helper) ---
    tab1, tab2, tab3 = st.tabs(["Green Beans", "Roasted Beans", "Lilmore"])

    with tab1:
        render_inv_prod_tab(
            header="Green Beans Stock Out vs Raw Coffee Taken",
            inv_filtered=inv_filtered,
            prod_filtered=prod_filtered,
            inv_filters={
                'Inventory Category': 'Green Beans',
                'Sold to Customer': 'No',
                'Transaction Type': 'Stock Out',
            },
            prod_column='Raw Coffee Taken',
        )

    with tab2:
        render_inv_prod_tab(
            header="Roasted Beans Stock In vs Roasted Output After SortOut",
            inv_filtered=inv_filtered,
            prod_filtered=prod_filtered,
            inv_filters={
                'Inventory Category': 'Roasted Beans',
                "is L'Lmore": False,
                'Transaction Type': 'Stock In',
            },
            prod_column='Roasted Output After SortOut (kg)',
        )

    with tab3:
        render_inv_prod_tab(
            header="Lilmore Stock In vs Lilmore Output",
            inv_filtered=inv_filtered,
            prod_filtered=prod_filtered,
            inv_filters={
                'Inventory Category': 'Roasted Beans',
                "is L'Lmore": True,
                'Transaction Type': 'Stock In',
            },
            prod_column='Lilmore Output (kg)',
        )


# ══════════════════════════════════════════════════════════════════════════════
#  MODE 2: Dispatch vs Full Tracker
# ══════════════════════════════════════════════════════════════════════════════

elif app_mode == "Dispatch vs Full Tracker":
    st.title("Dispatch vs Full Tracker Reconciliation")

    # --- Data Loading (parallel) ---
    @st.cache_data
    def load_dispatch_data():
        client = get_gspread_client()

        def fetch_dispatch():
            sh = client.open_by_key("1RfXzquQLqPWh8neSDhbNkQfR2vLJlM0gutRTlZK8Jbg")
            orders = pd.DataFrame(sh.worksheet("Orders").get_all_records())
            items = pd.DataFrame(sh.worksheet("Items").get_all_records())
            return orders, items

        def fetch_fulltracker():
            sh = client.open_by_key("1wrAHd2f7GcEtrtEpiV4TYYsI81f5tbQtUrzGIjAX55o")
            orders = pd.DataFrame(sh.worksheet("Orders").get_all_records())
            items = pd.DataFrame(sh.worksheet("Items").get_all_records())
            return orders, items

        dispatch_orders = pd.DataFrame()
        dispatch_items = pd.DataFrame()
        fulltracker_orders = pd.DataFrame()
        fulltracker_items = pd.DataFrame()

        with ThreadPoolExecutor(max_workers=2) as executor:
            disp_future = executor.submit(fetch_dispatch)
            ft_future = executor.submit(fetch_fulltracker)

            try:
                dispatch_orders, dispatch_items = disp_future.result()
            except Exception as e:
                st.error(f"Error loading 'Nandanvan Dispatch': {e}")

            try:
                fulltracker_orders, fulltracker_items = ft_future.result()
            except Exception as e:
                st.error(f"Error loading 'Full Tracking': {e}")

        return dispatch_orders, dispatch_items, fulltracker_orders, fulltracker_items

    try:
        dispatch_orders, dispatch_items, fulltracker_orders, fulltracker_items = load_dispatch_data()
        if dispatch_orders.empty or fulltracker_orders.empty:
            st.error("Data could not be loaded. Please ensure 'Nandanvan Dispatch' and 'Full Tracking' sheets are shared with the service account and contain 'Orders' and 'Items' worksheets.")
            st.stop()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    # --- Preprocessing ---
    dispatch_orders['Date'] = safe_to_date(dispatch_orders['Date'])
    fulltracker_orders['Dispatch Date'] = safe_to_date(fulltracker_orders['Dispatch Date'])

    # Clean Invoice Amount columns
    if 'Invoice Amount' in dispatch_orders.columns:
        dispatch_orders['Invoice Amount'] = safe_to_numeric(dispatch_orders['Invoice Amount']).round(2)

    if 'Invoice Amount' in fulltracker_orders.columns:
        fulltracker_orders['Invoice Amount'] = safe_to_numeric(fulltracker_orders['Invoice Amount']).round(2)

    # --- Sidebar Filters ---
    st.sidebar.header("Filters")
    st.sidebar.markdown("<br>" * 10, unsafe_allow_html=True)

    dispatch_dates = dispatch_orders['Date'].dropna()
    fulltracker_dates = fulltracker_orders['Dispatch Date'].dropna()

    min_date_disp = dispatch_dates.min() if len(dispatch_dates) > 0 else datetime.date.today()
    max_date_disp = dispatch_dates.max() if len(dispatch_dates) > 0 else datetime.date.today()
    min_date_ft = fulltracker_dates.min() if len(fulltracker_dates) > 0 else datetime.date.today()
    max_date_ft = fulltracker_dates.max() if len(fulltracker_dates) > 0 else datetime.date.today()

    min_date = min(min_date_disp, min_date_ft)
    max_date = max(max_date_disp, max_date_ft)

    start_date = st.sidebar.date_input("Start Date", min_date, key="disp_start_date")
    end_date = st.sidebar.date_input("End Date", max_date, key="disp_end_date")

    # --- Filtering and Merging ---
    dispatch_orders_filtered = dispatch_orders[
        (dispatch_orders['Date'] >= start_date) &
        (dispatch_orders['Date'] <= end_date)
    ].copy()

    fulltracker_orders_filtered = fulltracker_orders[
        (fulltracker_orders['Dispatch Date'] >= start_date) &
        (fulltracker_orders['Dispatch Date'] <= end_date) &
        (fulltracker_orders['PO UID'].str.startswith('YT', na=False)) &
        (fulltracker_orders['Status'] != 'Record Sales')
    ].copy()

    # Merge on PO UID
    merged_df = fulltracker_orders_filtered.merge(
        dispatch_orders_filtered,
        on='PO UID',
        how='outer',
        suffixes=('_fulltracker', '_dispatch')
    )

    # Build final display dataframe
    final_df = pd.DataFrame()
    final_df['PO UID'] = merged_df['PO UID']

    # Full Tracker columns
    final_df['Fulltracker Dispatch Date'] = merged_df.get('Dispatch Date', pd.NA)
    final_df['Fulltracker Invoice Number'] = merged_df.get('Invoice Number_fulltracker', pd.NA)
    final_df['Fulltracker Invoice Amount'] = merged_df.get('Invoice Amount_fulltracker', pd.NA)
    final_df['Fulltracker Total Qty (Kg)'] = merged_df.get('Total Qty (Kg)', pd.NA)
    final_df['Fulltracker SKU'] = merged_df.get('SKU', pd.NA)
    final_df['Fulltracker Packets Qty'] = merged_df.get('Qty (No.)', pd.NA)

    # Dispatch columns
    final_df['Dispatch Date'] = merged_df.get('Date', pd.NA)
    final_df['Dispatch Invoice Number'] = merged_df.get('Invoice Number_dispatch', pd.NA)
    final_df['Dispatch Invoice Amount'] = merged_df.get('Invoice Amount_dispatch', pd.NA)
    final_df['Dispatch Total Qty (Kg)'] = merged_df.get('Quantity (Kg)', pd.NA)
    final_df['Dispatch SKU'] = merged_df.get('Product (SKU)', pd.NA)
    final_df['Dispatch Packets Qty'] = merged_df.get('No of Packets', pd.NA)

    # Convert numeric columns (Packets Qty can be comma-separated strings, keep as-is)
    for col in ['Fulltracker Invoice Amount', 'Dispatch Invoice Amount',
                'Fulltracker Total Qty (Kg)', 'Dispatch Total Qty (Kg)']:
        if col in final_df.columns:
            final_df[col] = pd.to_numeric(final_df[col], errors='coerce')

    # --- Display ---
    st.subheader("Reconciliation Table")
    st.write("Select columns to display:")

    available_columns = final_df.columns.tolist()
    selected_columns = st.multiselect("Columns", available_columns, default=available_columns)

    if selected_columns:
        # Optimised highlighting with pre-computed column index map
        def highlight_reconciliation_mismatch(row):
            colors = [''] * len(row)
            col_map = {col: idx for idx, col in enumerate(row.index)}

            def mark_red(*col_names):
                for c in col_names:
                    if c in col_map:
                        colors[col_map[c]] = 'color: #ff0000'

            # Invoice Number
            if 'Fulltracker Invoice Number' in col_map and 'Dispatch Invoice Number' in col_map:
                ft_inv = row['Fulltracker Invoice Number']
                d_inv = row['Dispatch Invoice Number']
                if pd.isna(ft_inv) or pd.isna(d_inv) or ft_inv != d_inv:
                    mark_red('Fulltracker Invoice Number', 'Dispatch Invoice Number')

            # Invoice Amount
            if 'Fulltracker Invoice Amount' in col_map and 'Dispatch Invoice Amount' in col_map:
                ft_amt = row['Fulltracker Invoice Amount']
                d_amt = row['Dispatch Invoice Amount']
                if pd.isna(ft_amt) or pd.isna(d_amt):
                    mark_red('Fulltracker Invoice Amount', 'Dispatch Invoice Amount')
                elif isinstance(ft_amt, (int, float)) and isinstance(d_amt, (int, float)):
                    if abs(ft_amt - d_amt) > 0.01:
                        mark_red('Fulltracker Invoice Amount', 'Dispatch Invoice Amount')

            # Total Qty
            if 'Fulltracker Total Qty (Kg)' in col_map and 'Dispatch Total Qty (Kg)' in col_map:
                ft_qty = row['Fulltracker Total Qty (Kg)']
                d_qty = row['Dispatch Total Qty (Kg)']
                if pd.isna(ft_qty) or pd.isna(d_qty):
                    mark_red('Fulltracker Total Qty (Kg)', 'Dispatch Total Qty (Kg)')
                elif isinstance(ft_qty, (int, float)) and isinstance(d_qty, (int, float)):
                    if abs(ft_qty - d_qty) > 0.01:
                        mark_red('Fulltracker Total Qty (Kg)', 'Dispatch Total Qty (Kg)')

            # SKU
            if 'Fulltracker SKU' in col_map and 'Dispatch SKU' in col_map:
                ft_sku = row['Fulltracker SKU']
                d_sku = row['Dispatch SKU']
                if pd.isna(ft_sku) or pd.isna(d_sku):
                    mark_red('Fulltracker SKU', 'Dispatch SKU')
                else:
                    ft_sku_set = set(s.strip() for s in str(ft_sku).replace('\n', ',').split(',') if s.strip())
                    d_sku_set = set(s.strip() for s in str(d_sku).replace('\n', ',').split(',') if s.strip())
                    if ft_sku_set != d_sku_set:
                        mark_red('Fulltracker SKU', 'Dispatch SKU')

            # Packets Qty
            if 'Fulltracker Packets Qty' in col_map and 'Dispatch Packets Qty' in col_map:
                ft_pkt = row['Fulltracker Packets Qty']
                d_pkt = row['Dispatch Packets Qty']
                if pd.isna(ft_pkt) or pd.isna(d_pkt):
                    mark_red('Fulltracker Packets Qty', 'Dispatch Packets Qty')
                elif isinstance(ft_pkt, (int, float)) and isinstance(d_pkt, (int, float)):
                    if abs(ft_pkt - d_pkt) > 0.01:
                        mark_red('Fulltracker Packets Qty', 'Dispatch Packets Qty')
                elif isinstance(ft_pkt, str) or isinstance(d_pkt, str):
                    ft_qty_list = [s.strip() for s in str(ft_pkt).replace('\n', ',').split(',') if s.strip()]
                    d_qty_list = [s.strip() for s in str(d_pkt).replace('\n', ',').split(',') if s.strip()]
                    if Counter(ft_qty_list) != Counter(d_qty_list):
                        mark_red('Fulltracker Packets Qty', 'Dispatch Packets Qty')

            return colors

        # Build format dict for numeric columns
        format_dict = {}
        for col in selected_columns:
            if col in ['Fulltracker Invoice Amount', 'Dispatch Invoice Amount',
                       'Fulltracker Total Qty (Kg)', 'Dispatch Total Qty (Kg)',
                       'Fulltracker Packets Qty', 'Dispatch Packets Qty']:
                if col in final_df.columns and pd.api.types.is_numeric_dtype(final_df[col]):
                    format_dict[col] = '{:.2f}'

        st.dataframe(
            final_df[selected_columns].style
            .apply(highlight_reconciliation_mismatch, axis=1)
            .format(format_dict, na_rep='')
            .set_properties(**{'white-space': 'pre-wrap'}),
            use_container_width=True
        )
    else:
        st.write("Please select at least one column.")

    # --- SKU Totals Comparison ---
    st.divider()
    st.subheader("SKU Totals Variance")

    # Get PO UIDs from filtered orders
    ft_pouids = fulltracker_orders_filtered['PO UID'].unique()
    disp_pouids = dispatch_orders_filtered['PO UID'].unique()

    # Filter Items tables
    if not fulltracker_items.empty and not dispatch_items.empty:
        ft_items_filtered = fulltracker_items[fulltracker_items['PO UID'].isin(ft_pouids)].copy()
        disp_items_filtered = dispatch_items[dispatch_items['PO UID'].isin(disp_pouids)].copy()

        # ── BUG FIX: Convert to numeric BEFORE aggregation ──
        # Values arrive as strings from Google Sheets; without conversion
        # .sum() concatenates strings instead of adding numbers.
        for col in ['Quantity', 'Total Kg']:
            if col in ft_items_filtered.columns:
                ft_items_filtered[col] = safe_to_numeric(ft_items_filtered[col]).fillna(0)
            if col in disp_items_filtered.columns:
                disp_items_filtered[col] = safe_to_numeric(disp_items_filtered[col]).fillna(0)
    else:
        st.warning("Items data not available. Skipping SKU Totals Variance.")
        ft_items_filtered = pd.DataFrame(columns=['SKU', 'Quantity', 'Total Kg'])
        disp_items_filtered = pd.DataFrame(columns=['SKU', 'Quantity', 'Total Kg'])

    # Group by SKU and aggregate
    fulltracker_items_total_sku = ft_items_filtered.groupby(['SKU']).agg(
        {'Quantity': 'sum', 'Total Kg': 'sum'}
    ).reset_index()
    dispatch_items_total_sku = disp_items_filtered.groupby(['SKU']).agg(
        {'Quantity': 'sum', 'Total Kg': 'sum'}
    ).reset_index()

    # Merge
    sku_merge = fulltracker_items_total_sku.merge(
        dispatch_items_total_sku,
        on='SKU',
        how='outer',
        suffixes=('_FullTracker', '_Dispatch')
    ).fillna(0)

    # Rename columns
    sku_merge = sku_merge.rename(columns={
        'Quantity_FullTracker': 'FullTracker Quantity',
        'Total Kg_FullTracker': 'FullTracker Total Kg',
        'Quantity_Dispatch': 'Dispatch Quantity',
        'Total Kg_Dispatch': 'Dispatch Total Kg'
    })

    # Ensure numeric (safety net after merge fillna)
    for col in ['FullTracker Quantity', 'FullTracker Total Kg', 'Dispatch Quantity', 'Dispatch Total Kg']:
        sku_merge[col] = pd.to_numeric(sku_merge[col], errors='coerce').fillna(0)

    # Variance
    sku_merge['Quantity Variance'] = (sku_merge['FullTracker Quantity'] - sku_merge['Dispatch Quantity']).abs()
    sku_merge['Total Kg Variance'] = (sku_merge['FullTracker Total Kg'] - sku_merge['Dispatch Total Kg']).abs()

    # Highlight with optimised col_map
    def highlight_sku_mismatch(row):
        colors = [''] * len(row)
        col_map = {col: idx for idx, col in enumerate(row.index)}

        if row['Quantity Variance'] > 0.01:
            for c in ['FullTracker Quantity', 'Dispatch Quantity', 'Quantity Variance']:
                colors[col_map[c]] = 'color: #ff0000'

        if row['Total Kg Variance'] > 0.01:
            for c in ['FullTracker Total Kg', 'Dispatch Total Kg', 'Total Kg Variance']:
                colors[col_map[c]] = 'color: #ff0000'

        return colors

    st.dataframe(
        sku_merge.style
        .apply(highlight_sku_mismatch, axis=1)
        .format({
            "FullTracker Quantity": "{:.2f}",
            "FullTracker Total Kg": "{:.2f}",
            "Dispatch Quantity": "{:.2f}",
            "Dispatch Total Kg": "{:.2f}",
            "Quantity Variance": "{:.2f}",
            "Total Kg Variance": "{:.2f}"
        }),
        use_container_width=True
    )

    # --- Invoice Amount Totals Validation ---
    st.divider()
    st.subheader("Invoice Amount Totals Variance")

    ft_total_invoice = safe_to_numeric(fulltracker_orders_filtered['Invoice Amount']).fillna(0).sum()
    disp_total_invoice = safe_to_numeric(dispatch_orders_filtered['Invoice Amount']).fillna(0).sum()
    invoice_variance = abs(ft_total_invoice - disp_total_invoice)

    invoice_comparison = pd.DataFrame({
        'Source': ['FullTracker', 'Dispatch', 'Variance'],
        'Total Invoice Amount': [ft_total_invoice, disp_total_invoice, invoice_variance]
    })

    def highlight_invoice_variance(row):
        colors = [''] * len(row)
        if row['Source'] == 'Variance' and row['Total Invoice Amount'] > 0.01:
            colors[1] = 'color: #ff0000'
        elif row['Source'] in ['FullTracker', 'Dispatch'] and invoice_variance > 0.01:
            colors[1] = 'color: #ff0000'
        return colors

    st.dataframe(
        invoice_comparison.style
        .apply(highlight_invoice_variance, axis=1)
        .format({"Total Invoice Amount": "{:.2f}"}),
        use_container_width=True
    )

    # --- Quantity and Packets Totals Validation ---
    st.divider()
    st.subheader("Quantity and Packets Totals Variance")

    if not ft_items_filtered.empty:
        ft_total_qty = safe_to_numeric(ft_items_filtered['Total Kg']).fillna(0).sum()
        ft_total_packets = safe_to_numeric(ft_items_filtered['Quantity']).fillna(0).sum()
    else:
        ft_total_qty = 0.0
        ft_total_packets = 0.0

    if not disp_items_filtered.empty:
        disp_total_qty = safe_to_numeric(disp_items_filtered['Total Kg']).fillna(0).sum()
        disp_total_packets = safe_to_numeric(disp_items_filtered['Quantity']).fillna(0).sum()
    else:
        disp_total_qty = 0.0
        disp_total_packets = 0.0

    qty_variance = abs(ft_total_qty - disp_total_qty)
    packets_variance = abs(ft_total_packets - disp_total_packets)

    qty_comparison = pd.DataFrame({
        'Source': ['FullTracker', 'Dispatch', 'Variance'],
        'Total Quantity (Kg)': [ft_total_qty, disp_total_qty, qty_variance],
        'Total Packets': [ft_total_packets, disp_total_packets, packets_variance]
    })

    def highlight_qty_variance(row):
        colors = [''] * len(row)

        if row['Source'] == 'Variance' and row['Total Quantity (Kg)'] > 0.01:
            colors[1] = 'color: #ff0000'
        elif row['Source'] in ['FullTracker', 'Dispatch'] and qty_variance > 0.01:
            colors[1] = 'color: #ff0000'

        if row['Source'] == 'Variance' and row['Total Packets'] > 0.01:
            colors[2] = 'color: #ff0000'
        elif row['Source'] in ['FullTracker', 'Dispatch'] and packets_variance > 0.01:
            colors[2] = 'color: #ff0000'

        return colors

    st.dataframe(
        qty_comparison.style
        .apply(highlight_qty_variance, axis=1)
        .format({"Total Quantity (Kg)": "{:.2f}", "Total Packets": "{:.2f}"}),
        use_container_width=True
    )


# ══════════════════════════════════════════════════════════════════════════════
#  MODE 3: Packets and Packing Materials
# ══════════════════════════════════════════════════════════════════════════════

elif app_mode == "Packets and Packing Materials":
    st.title("Packets and Packing Materials Inventory")

    # --- Data Loading ---
    @st.cache_data
    def load_packed_materials_data():
        client = get_gspread_client()
        try:
            sh_inv = client.open_by_key("1JTeE3dwYJj6cXGFF-MUWsmyX3aD3sHwh4dBR0KEgMVQ")
            packets_data = sh_inv.worksheet("Packets").get_all_records()
            pm_data = sh_inv.worksheet("Packing Materials").get_all_records()

            packets_df = pd.DataFrame(packets_data)
            packing_materials_df = pd.DataFrame(pm_data)

        except Exception as e:
            st.error(f"Error loading data: {e}")
            return pd.DataFrame(), pd.DataFrame()

        return packets_df, packing_materials_df

    try:
        packets_df, packing_materials_df = load_packed_materials_data()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    # --- Preprocessing ---
    if not packets_df.empty and 'Date' in packets_df.columns:
        packets_df['Date'] = safe_to_date(packets_df['Date'])

    if not packing_materials_df.empty and 'Date' in packing_materials_df.columns:
        packing_materials_df['Date'] = safe_to_date(packing_materials_df['Date'])

    # --- Sidebar Filters ---
    st.sidebar.markdown("<br>" * 2, unsafe_allow_html=True)
    st.sidebar.header("Packets and Packing Materials Filters")

    # Get Date Range
    all_dates = pd.Series(dtype='object')
    if not packets_df.empty and 'Date' in packets_df.columns:
        all_dates = pd.concat([all_dates, packets_df['Date']])
    if not packing_materials_df.empty and 'Date' in packing_materials_df.columns:
        all_dates = pd.concat([all_dates, packing_materials_df['Date']])

    all_dates = all_dates.dropna()

    if not all_dates.empty:
        min_date = all_dates.min()
        max_date = all_dates.max()
    else:
        min_date = datetime.date.today()
        max_date = datetime.date.today()

    # Separate max dates for each dataset
    packets_max_date = packets_df['Date'].dropna().max() if not packets_df.empty and 'Date' in packets_df.columns else max_date
    pm_max_date = packing_materials_df['Date'].dropna().max() if not packing_materials_df.empty and 'Date' in packing_materials_df.columns else max_date

    start_date = st.sidebar.date_input("Start Date", min_date, key="pm_start_date")
    end_date = st.sidebar.date_input("End Date", max_date, key="pm_end_date")

    # Fixed balance start date: always Oct 31, 2025
    balance_start_date = datetime.date(2025, 10, 31)

    # Filter Data (user-selected date range for Stock In / Stock Out columns)
    if not packets_df.empty and 'Date' in packets_df.columns:
        packets_filtered = packets_df[(packets_df['Date'] >= start_date) & (packets_df['Date'] <= end_date)].copy()
    else:
        packets_filtered = pd.DataFrame()

    if not packing_materials_df.empty and 'Date' in packing_materials_df.columns:
        pm_filtered = packing_materials_df[(packing_materials_df['Date'] >= start_date) & (packing_materials_df['Date'] <= end_date)].copy()
    else:
        pm_filtered = pd.DataFrame()

    # Balance-filtered data: always from Oct 31 to user-selected end date (ignores start_date)
    if not packets_df.empty and 'Date' in packets_df.columns:
        packets_balance_filtered = packets_df[(packets_df['Date'] >= balance_start_date) & (packets_df['Date'] <= end_date)].copy()
    else:
        packets_balance_filtered = pd.DataFrame()

    if not packing_materials_df.empty and 'Date' in packing_materials_df.columns:
        pm_balance_filtered = packing_materials_df[(packing_materials_df['Date'] >= balance_start_date) & (packing_materials_df['Date'] <= end_date)].copy()
    else:
        pm_balance_filtered = pd.DataFrame()

    # --- Tabs ---
    tab1, tab2 = st.tabs(["Packets", "Packing Materials"])

    # --- Tab 1: Packets ---
    with tab1:
        st.header("Packets: Stock In vs Stock Out")

        if not packets_filtered.empty:
            packets_filtered['Quantity'] = safe_to_numeric(packets_filtered['Quantity']).fillna(0)

            try:
                packets_pivot = packets_filtered.pivot_table(
                    index=['Packet SKU', 'Packet Name'],
                    columns='Transaction Type',
                    values='Quantity',
                    aggfunc='sum',
                    fill_value=0
                ).reset_index()
            except KeyError as e:
                st.error(f"Missing required columns for pivoting: {e}")
                packets_pivot = pd.DataFrame(columns=['Packet SKU', 'Packet Name', 'Transaction Type', 'Quantity'])
            except Exception as e:
                st.error(f"Error processing Packets data: {e}")
                packets_pivot = pd.DataFrame(columns=['Packet SKU', 'Packet Name', 'Transaction Type', 'Quantity'])

            for col in ['Stock In', 'Stock Out']:
                if col not in packets_pivot.columns:
                    packets_pivot[col] = 0.0

            packets_pivot = packets_pivot.fillna(0)

            # Calculate Balance from fixed start date (Oct 31, 2025) to end_date
            if not packets_balance_filtered.empty:
                packets_balance_copy = packets_balance_filtered.copy()
                packets_balance_copy['Quantity'] = safe_to_numeric(packets_balance_copy['Quantity']).fillna(0)
                try:
                    balance_pivot = packets_balance_copy.pivot_table(
                        index=['Packet SKU', 'Packet Name'],
                        columns='Transaction Type',
                        values='Quantity',
                        aggfunc='sum',
                        fill_value=0
                    ).reset_index()
                    for col in ['Stock In', 'Stock Out']:
                        if col not in balance_pivot.columns:
                            balance_pivot[col] = 0.0
                    balance_pivot['Balance'] = balance_pivot['Stock In'] - balance_pivot['Stock Out']
                    # Merge balance into main pivot
                    packets_pivot = packets_pivot.drop(columns=['Balance'], errors='ignore')
                    packets_pivot = packets_pivot.merge(
                        balance_pivot[['Packet SKU', 'Packet Name', 'Balance']],
                        on=['Packet SKU', 'Packet Name'],
                        how='left'
                    ).fillna(0)
                except Exception:
                    packets_pivot['Balance'] = packets_pivot['Stock In'] - packets_pivot['Stock Out']
            else:
                packets_pivot['Balance'] = packets_pivot['Stock In'] - packets_pivot['Stock Out']

            # Convert numeric columns to int
            for col in ['Stock In', 'Stock Out', 'Balance']:
                if col in packets_pivot.columns:
                    packets_pivot[col] = packets_pivot[col].astype(int)

            # Filter by Packet Name
            unique_packets = sorted(packets_pivot['Packet Name'].unique())
            selected_packets = st.multiselect("Select Packets", unique_packets, default=unique_packets)

            if selected_packets:
                packets_pivot = packets_pivot[packets_pivot['Packet Name'].isin(selected_packets)]

            # Reorder columns
            cols_order = ['Packet SKU', 'Packet Name', 'Stock In', 'Stock Out', 'Balance']
            remaining_cols = [c for c in packets_pivot.columns if c not in cols_order]
            final_cols = cols_order + remaining_cols

            st.caption(f"_Balance is calculated from **{balance_start_date.strftime('%d %b %Y')}** to **{end_date.strftime('%d %b %Y')}**_")
            st.dataframe(packets_pivot[final_cols], use_container_width=True)

        else:
            st.info("No Packets data for selected date range.")

    # --- Tab 2: Packing Materials ---
    with tab2:
        st.header("Packing Materials: Category Wise Stock In vs Stock Out")

        if not pm_filtered.empty:
            pm_filtered['Quantity'] = safe_to_numeric(pm_filtered['Quantity']).fillna(0)

            try:
                pm_pivot = pm_filtered.pivot_table(
                    index=['Packing Materials Category', 'Packing Material Name'],
                    columns='Transaction Type',
                    values='Quantity',
                    aggfunc='sum',
                    fill_value=0
                ).reset_index()
            except KeyError as e:
                st.error(f"Missing required columns for pivoting: {e}")
                pm_pivot = pd.DataFrame(columns=['Packing Materials Category', 'Packing Material Name', 'Transaction Type', 'Quantity'])
            except Exception as e:
                st.error(f"Error processing Packing Materials data: {e}")
                pm_pivot = pd.DataFrame(columns=['Packing Materials Category', 'Packing Material Name', 'Transaction Type', 'Quantity'])

            for col in ['Stock In', 'Stock Out']:
                if col not in pm_pivot.columns:
                    pm_pivot[col] = 0.0

            pm_pivot = pm_pivot.fillna(0)

            # Calculate Balance from fixed start date (Oct 31, 2025) to end_date
            if not pm_balance_filtered.empty:
                pm_balance_copy = pm_balance_filtered.copy()
                pm_balance_copy['Quantity'] = safe_to_numeric(pm_balance_copy['Quantity']).fillna(0)
                try:
                    pm_balance_pivot = pm_balance_copy.pivot_table(
                        index=['Packing Materials Category', 'Packing Material Name'],
                        columns='Transaction Type',
                        values='Quantity',
                        aggfunc='sum',
                        fill_value=0
                    ).reset_index()
                    for col in ['Stock In', 'Stock Out']:
                        if col not in pm_balance_pivot.columns:
                            pm_balance_pivot[col] = 0.0
                    pm_balance_pivot['Balance'] = pm_balance_pivot['Stock In'] - pm_balance_pivot['Stock Out']
                    # Merge balance into main pivot
                    pm_pivot = pm_pivot.merge(
                        pm_balance_pivot[['Packing Materials Category', 'Packing Material Name', 'Balance']],
                        on=['Packing Materials Category', 'Packing Material Name'],
                        how='left'
                    ).fillna(0)
                except Exception:
                    pm_pivot['Balance'] = pm_pivot['Stock In'] - pm_pivot['Stock Out']
            else:
                pm_pivot['Balance'] = pm_pivot['Stock In'] - pm_pivot['Stock Out']

            # Convert numeric columns to int
            for col in ['Stock In', 'Stock Out', 'Balance']:
                if col in pm_pivot.columns:
                    pm_pivot[col] = pm_pivot[col].astype(int)

            # Filter by Packing Material Name
            unique_pm = sorted(pm_pivot['Packing Material Name'].unique())
            selected_pm = st.multiselect("Select Packing Materials", unique_pm, default=unique_pm)

            if selected_pm:
                pm_pivot = pm_pivot[pm_pivot['Packing Material Name'].isin(selected_pm)]

            # Sort by Category
            pm_pivot = pm_pivot.sort_values(by=['Packing Materials Category', 'Packing Material Name'])

            # Reorder columns
            cols_order = ['Packing Materials Category', 'Packing Material Name', 'Stock In', 'Stock Out', 'Balance']
            remaining_cols = [c for c in pm_pivot.columns if c not in cols_order]
            final_cols = cols_order + remaining_cols

            st.caption(f"_Balance is calculated from **{balance_start_date.strftime('%d %b %Y')}** to **{end_date.strftime('%d %b %Y')}**_")
            st.dataframe(pm_pivot[final_cols], use_container_width=True)

            # --- Average Monthly Consumption Table ---
            st.divider()
            st.header("Packing Materials: Average Monthly Consumption")
            st.caption(f"_Based on Stock Out from **{start_date.strftime('%d %b %Y')}** to **{end_date.strftime('%d %b %Y')}**_")

            pm_stockout = pm_filtered[pm_filtered['Transaction Type'] == 'Stock Out'].copy()

            if not pm_stockout.empty:
                pm_stockout['Quantity'] = safe_to_numeric(pm_stockout['Quantity']).fillna(0)

                num_days = (end_date - start_date).days
                num_months = max(num_days / 30.44, 1)

                monthly_consumption = pm_stockout.groupby(
                    ['Packing Materials Category', 'Packing Material Name']
                )['Quantity'].sum().reset_index()
                monthly_consumption = monthly_consumption.rename(columns={'Quantity': 'Total Stock Out'})
                monthly_consumption['Avg Monthly Consumption'] = (monthly_consumption['Total Stock Out'] / num_months).astype(int)
                monthly_consumption['Total Stock Out'] = monthly_consumption['Total Stock Out'].astype(int)

                if selected_pm:
                    monthly_consumption = monthly_consumption[monthly_consumption['Packing Material Name'].isin(selected_pm)]

                monthly_consumption = monthly_consumption.sort_values(by=['Packing Materials Category', 'Packing Material Name'])

                st.dataframe(monthly_consumption, use_container_width=True)
                st.caption(f"_Number of months in range: **{num_months:.1f}**_")
            else:
                st.info("No Stock Out data available for the selected date range to calculate average consumption.")

            # --- Stock In Hand & Days Stock Will Last (STATIC) ---
            st.divider()
            st.header("Packing Materials: Stock In Hand & Days Stock Will Last")

            pm_static_filtered = packing_materials_df[
                (packing_materials_df['Date'] >= balance_start_date)
            ].copy() if not packing_materials_df.empty and 'Date' in packing_materials_df.columns else pd.DataFrame()

            if not pm_static_filtered.empty:
                pm_static_filtered['Quantity'] = safe_to_numeric(pm_static_filtered['Quantity']).fillna(0)
                static_max_date = pm_max_date

                try:
                    static_pivot = pm_static_filtered.pivot_table(
                        index=['Packing Materials Category', 'Packing Material Name'],
                        columns='Transaction Type',
                        values='Quantity',
                        aggfunc='sum',
                        fill_value=0
                    ).reset_index()

                    for col in ['Stock In', 'Stock Out']:
                        if col not in static_pivot.columns:
                            static_pivot[col] = 0

                    static_pivot['Stock In Hand'] = static_pivot['Stock In'] - static_pivot['Stock Out']

                    # Days Stock Will Last = (Stock In Hand / Avg Monthly Consumption) * 30
                    static_num_days = max((static_max_date - balance_start_date).days, 1)
                    static_num_months = max(static_num_days / 30.44, 1)
                    static_pivot['Avg Monthly Consumption'] = (static_pivot['Stock Out'] / static_num_months)
                    static_pivot['Days Stock Will Last'] = static_pivot.apply(
                        lambda row: int((row['Stock In Hand'] / row['Avg Monthly Consumption']) * 30) if row['Avg Monthly Consumption'] > 0 else 0, axis=1
                    )

                    for col in ['Stock In Hand', 'Days Stock Will Last']:
                        static_pivot[col] = static_pivot[col].astype(int)

                    if selected_pm:
                        static_pivot = static_pivot[static_pivot['Packing Material Name'].isin(selected_pm)]

                    static_pivot = static_pivot.sort_values(by=['Packing Materials Category', 'Packing Material Name'])

                    static_cols = ['Packing Materials Category', 'Packing Material Name', 'Stock In Hand', 'Days Stock Will Last']

                    st.caption(f"_Static data from **{balance_start_date.strftime('%d %b %Y')}** to **{static_max_date.strftime('%d %b %Y')}** · Not affected by date filters_")
                    st.dataframe(static_pivot[static_cols], use_container_width=True)

                except Exception as e:
                    st.error(f"Error calculating Stock In Hand: {e}")
            else:
                st.info("No data available from Oct 31, 2025 onwards.")

        else:
            st.info("No Packing Materials data for selected date range.")