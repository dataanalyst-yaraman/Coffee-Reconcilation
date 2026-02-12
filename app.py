import streamlit as st
import pandas as pd
import datetime
from collections import Counter
import gspread
from oauth2client.service_account import ServiceAccountCredentials
import os
import json
from dotenv import load_dotenv

# Page Config
st.set_page_config(page_title="Inventory vs Production", layout="wide")

# --- Sidebar Navigation ---
st.sidebar.header("Overview")
if st.sidebar.button("Refresh Data", key="refresh_data_top"):
    st.cache_data.clear()

# --- GSpread Authentication ---
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
        if not creds_json_str:
            st.error("Google Credentials not found in environment variables (GOOGLE_CREDENTIALS_DICT).")
            st.stop()
        creds_dict = json.loads(creds_json_str)

    credentials = ServiceAccountCredentials.from_json_keyfile_dict(creds_dict, scope)
    client = gspread.authorize(credentials)
    return client

client = get_gspread_client()

app_mode = st.sidebar.radio("Go to", ["Inventory vs Production", "Dispatch vs Full Tracker"])

if app_mode == "Inventory vs Production":
    st.title("Inventory vs Production Reconciliation")


    # --- Data Loading ---
    @st.cache_data
    def load_inv_prod_data():
        # Inventory Sheet
        try:
            sh_inv = client.open_by_key("1JTeE3dwYJj6cXGFF-MUWsmyX3aD3sHwh4dBR0KEgMVQ")
            inventory = pd.DataFrame(sh_inv.worksheet("Coffees").get_all_records())
        except Exception as e:
            st.error(f"Error loading 'Nandanvan Inventory': {e}")
            return pd.DataFrame(), pd.DataFrame() # Return empty to handle gracefully
        
        # Production Sheet
        try:
            sh_prod = client.open_by_key("1C8nwOXF1u944Km_5DcG34ntvmuWx9JBPhdyvsZT2bC8")
            production = pd.DataFrame(sh_prod.worksheet("Coffee").get_all_records())
        except Exception as e:
            st.error(f"Error loading 'Nandanvan Production': {e}")
            return inventory, pd.DataFrame()

        return inventory, production
        


    try:
        inventory_df, production_df = load_inv_prod_data()
        if inventory_df.empty or production_df.empty:
            st.error("Data could not be loaded. Please check the logs/errors above.")
            st.stop()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    # --- Preprocessing ---
    # Convert dates
    inventory_df['Date'] = pd.to_datetime(inventory_df['Date'], format='%m/%d/%Y', errors='coerce').dt.date
    production_df['Date'] = pd.to_datetime(production_df['Date'], format='%m/%d/%Y', errors='coerce').dt.date

    # Drop rows with invalid dates
    inventory_df = inventory_df.dropna(subset=['Date'])
    production_df = production_df.dropna(subset=['Date'])

    if inventory_df.empty or production_df.empty:
        st.error("Data is empty after date parsing. Please check the date format in the sheets.")
        st.stop()

    # Create "is L'Lmore" column in inventory
    inventory_df["is L'Lmore"] = inventory_df['Beans'].str.contains("L'Lmore", na=False)

    # --- Sidebar Filters ---
    st.sidebar.header("Filters")

    # Date Filter
    st.sidebar.markdown("<br>" * 10, unsafe_allow_html=True) # Add significant spacing to prevent date picker cropping
    min_date_inv = inventory_df['Date'].min()
    max_date_inv = inventory_df['Date'].max()
    min_date_prod = production_df['Date'].min()
    max_date_prod = production_df['Date'].max()

    min_date = min(min_date_inv, min_date_prod)
    max_date = max(max_date_inv, max_date_prod)

    # Ensure they are python date objects for streamlit
    if isinstance(min_date, pd.Timestamp): min_date = min_date.date()
    if isinstance(max_date, pd.Timestamp): max_date = max_date.date()

    start_date = st.sidebar.date_input("Start Date", min_date, key="inv_start_date")
    end_date = st.sidebar.date_input("End Date", max_date, key="inv_end_date")

    # Filter Data by Date (both are date objects now)
    inv_filtered = inventory_df[(inventory_df['Date'] >= start_date) & (inventory_df['Date'] <= end_date)]
    prod_filtered = production_df[(production_df['Date'] >= start_date) & (production_df['Date'] <= end_date)]

    # --- Tabs ---
    tab1, tab2, tab3 = st.tabs(["Green Beans", "Roasted Beans", "Lilmore"])

    # --- Tab 1: Green Beans vs Raw Coffee ---
    with tab1:
        st.header("Green Beans Stock Out vs Raw Coffee Taken")
        
        # Logic from notebook:
        # Inventory: Category='Green Beans', Sold to Customer='No', Transaction Type='Stock Out'
        inv_gb = inv_filtered[
            (inv_filtered['Inventory Category'] == 'Green Beans') & 
            (inv_filtered["Sold to Customer"] == 'No') & 
            (inv_filtered['Transaction Type'] == 'Stock Out')
        ]
        
        # Production: Just the date filtered data
        prod_gb = prod_filtered.copy()
        
        # Merge
        # Note: Notebook merges on ['Date', 'Label'] (Inventory) and ['Date', 'Coffee Type'] (Production)
        merged_gb = inv_gb.merge(
            prod_gb, 
            left_on=['Date', 'Label'], 
            right_on=['Date', 'Coffee Type'], 
            how='outer'
        )[['Date', 'Label', 'Quantity', 'Coffee Type', 'Raw Coffee Taken']]
        
        # Rename columns and round
        merged_gb = merged_gb.rename(columns={'Quantity': 'Inventory', 'Raw Coffee Taken': 'Production'})
        merged_gb = merged_gb.round(2)
        
        # Display Merged Data
        st.subheader("Detailed Comparison")
        
        # Apply red text to mismatched or null values
        def highlight_detailed_mismatch(row):
            colors = [''] * len(row)
            inv_val = row['Inventory']
            prod_val = row['Production']
            
            # Find column indices
            inv_idx = row.index.get_loc('Inventory')
            prod_idx = row.index.get_loc('Production')
            
            # Check if values don't match or if either is null
            if pd.isna(inv_val) or pd.isna(prod_val):
                colors[inv_idx] = 'color: #ff0000'
                colors[prod_idx] = 'color: #ff0000'
            elif isinstance(inv_val, (int, float)) and isinstance(prod_val, (int, float)):
                if abs(inv_val - prod_val) > 0.01:
                    colors[inv_idx] = 'color: #ff0000'
                    colors[prod_idx] = 'color: #ff0000'
            
            return colors
        
        st.dataframe(
            merged_gb.style
            .apply(highlight_detailed_mismatch, axis=1)
            .format({"Inventory": "{:.2f}", "Production": "{:.2f}"}), 
            use_container_width=True
        )
        
        # Totals
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Inventory Total")
            total_inv_gb = inv_gb.groupby('Label')['Quantity'].sum().reset_index()
            total_inv_gb = total_inv_gb.rename(columns={'Quantity': 'Inventory'}) # Rounding handled by style
            st.dataframe(total_inv_gb.style.format({"Inventory": "{:.2f}"}), use_container_width=True)
            
        with col2:
            st.subheader("Production Total")
            total_prod_gb = prod_gb.groupby('Coffee Type')['Raw Coffee Taken'].sum().reset_index()
            total_prod_gb = total_prod_gb.rename(columns={'Raw Coffee Taken': 'Production'}) # Rounding handled by style
            st.dataframe(total_prod_gb.style.format({"Production": "{:.2f}"}), use_container_width=True)

        # Variance Check
        st.subheader("Variance")
        # Merge totals for comparison
        validation_gb = total_inv_gb.merge(
            total_prod_gb, 
            left_on='Label', 
            right_on='Coffee Type', 
            how='outer'
        ).fillna(0)
        
        validation_gb['Difference'] = (validation_gb['Inventory'] - validation_gb['Production']).abs()
        validation_gb = validation_gb.round(2)
        
        st.dataframe(
            validation_gb.style
            .format({"Inventory": "{:.2f}", "Production": "{:.2f}", "Difference": "{:.2f}"}), 
            use_container_width=True
        )


    # --- Tab 2: Roasted Beans Stock vs Sort Out ---
    with tab2:
        st.header("Roasted Beans Stock In vs Roasted Output After SortOut")
        
        # Logic from notebook:
        # Inventory: Category='Roasted Beans', is L'Lmore=False, Transaction Type='Stock In'
        inv_rb = inv_filtered[
            (inv_filtered['Inventory Category'] == 'Roasted Beans') & 
            (inv_filtered["is L'Lmore"] == False) & 
            (inv_filtered['Transaction Type'] == 'Stock In')
        ]
        
        # Production: Date filtered
        prod_rb = prod_filtered.copy()
        
        # Merge
        merged_rb = inv_rb.merge(
            prod_rb, 
            left_on=['Date', 'Label'], 
            right_on=['Date', 'Coffee Type'], 
            how='outer'
        )[['Date', 'Label', 'Quantity', 'Coffee Type', 'Roasted Output After SortOut (kg)']]
        
        # Rename columns and round
        merged_rb = merged_rb.rename(columns={'Quantity': 'Inventory', 'Roasted Output After SortOut (kg)': 'Production'})
        merged_rb = merged_rb.round(2)
        
        # Display Merged Data
        st.subheader("Detailed Comparison")
        
        # Apply red text to mismatched or null values
        def highlight_detailed_mismatch(row):
            colors = [''] * len(row)
            inv_val = row['Inventory']
            prod_val = row['Production']
            
            # Find column indices
            inv_idx = row.index.get_loc('Inventory')
            prod_idx = row.index.get_loc('Production')
            
            # Check if values don't match or if either is null
            if pd.isna(inv_val) or pd.isna(prod_val):
                colors[inv_idx] = 'color: #ff0000'
                colors[prod_idx] = 'color: #ff0000'
            elif isinstance(inv_val, (int, float)) and isinstance(prod_val, (int, float)):
                if abs(inv_val - prod_val) > 0.01:
                    colors[inv_idx] = 'color: #ff0000'
                    colors[prod_idx] = 'color: #ff0000'
            
            return colors
        
        st.dataframe(
            merged_rb.style
            .apply(highlight_detailed_mismatch, axis=1)
            .format({"Inventory": "{:.2f}", "Production": "{:.2f}"}), 
            use_container_width=True
        )
        
        # Totals
        col1, col2 = st.columns(2)
        
        with col1:
            st.subheader("Inventory Total")
            total_inv_rb = inv_rb.groupby('Label')['Quantity'].sum().reset_index()
            total_inv_rb = total_inv_rb.rename(columns={'Quantity': 'Inventory'})
            st.dataframe(total_inv_rb.style.format({"Inventory": "{:.2f}"}), use_container_width=True)
            
        with col2:
            st.subheader("Production Total")
            total_prod_rb = prod_rb.groupby('Coffee Type')['Roasted Output After SortOut (kg)'].sum().reset_index()
            total_prod_rb = total_prod_rb.rename(columns={'Roasted Output After SortOut (kg)': 'Production'})
            st.dataframe(total_prod_rb.style.format({"Production": "{:.2f}"}), use_container_width=True)
            
        # Variance Check
        st.subheader("Variance")
        validation_rb = total_inv_rb.merge(
            total_prod_rb, 
            left_on='Label', 
            right_on='Coffee Type', 
            how='outer'
        ).fillna(0)
        
        validation_rb['Difference'] = (validation_rb['Inventory'] - validation_rb['Production']).abs()
        validation_rb = validation_rb.round(2)
        
        st.dataframe(
            validation_rb.style
            .format({"Inventory": "{:.2f}", "Production": "{:.2f}", "Difference": "{:.2f}"}), 
            use_container_width=True
        )


    # --- Tab 3: Lilmore Output ---
    with tab3:
        st.header("Lilmore Stock In vs Lilmore Output")
        
        # Logic from notebook:
        # Inventory: Category='Roasted Beans', is L'Lmore=True, Transaction Type='Stock In'
        inv_lm = inv_filtered[
            (inv_filtered['Inventory Category'] == 'Roasted Beans') & 
            (inv_filtered["is L'Lmore"] == True) & 
            (inv_filtered['Transaction Type'] == 'Stock In')
        ]
        
        # Production: Date filtered
        prod_lm = prod_filtered.copy()
        
        # Merge
        merged_lm = inv_lm.merge(
            prod_lm, 
            left_on=['Date', 'Label'], 
            right_on=['Date', 'Coffee Type'], 
            how='outer'
        )[['Date', 'Label', 'Quantity', 'Coffee Type', 'Lilmore Output (kg)']]
        
        # Rename columns and round
        merged_lm = merged_lm.rename(columns={'Quantity': 'Inventory', 'Lilmore Output (kg)': 'Production'})
        merged_lm = merged_lm.round(2)
        
        # Display Merged Data
        st.subheader("Detailed Comparison")
        
        # Apply red text to mismatched or null values
        def highlight_detailed_mismatch(row):
            colors = [''] * len(row)
            inv_val = row['Inventory']
            prod_val = row['Production']
            
            # Find column indices
            inv_idx = row.index.get_loc('Inventory')
            prod_idx = row.index.get_loc('Production')
            
            # Check if values don't match or if either is null
            if pd.isna(inv_val) or pd.isna(prod_val):
                colors[inv_idx] = 'color: #ff0000'
                colors[prod_idx] = 'color: #ff0000'
            elif isinstance(inv_val, (int, float)) and isinstance(prod_val, (int, float)):
                if abs(inv_val - prod_val) > 0.01:
                    colors[inv_idx] = 'color: #ff0000'
                    colors[prod_idx] = 'color: #ff0000'
            
            return colors
        
        st.dataframe(
            merged_lm.style
            .apply(highlight_detailed_mismatch, axis=1)
            .format({"Inventory": "{:.2f}", "Production": "{:.2f}"}), 
            use_container_width=True
        )
        
        # Totals
        col1, col2 = st.columns(2)
        
        with col1: 
            st.subheader("Inventory Total")
            total_inv_lm = inv_lm.groupby('Label')['Quantity'].sum().reset_index()
            total_inv_lm = total_inv_lm.rename(columns={'Quantity': 'Inventory'})
            st.dataframe(total_inv_lm.style.format({"Inventory": "{:.2f}"}), use_container_width=True)
            
        with col2:
            st.subheader("Production Total")
            total_prod_lm = prod_lm.groupby('Coffee Type')['Lilmore Output (kg)'].sum().reset_index()
            total_prod_lm = total_prod_lm.rename(columns={'Lilmore Output (kg)': 'Production'})
            st.dataframe(total_prod_lm.style.format({"Production": "{:.2f}"}), use_container_width=True)

        # Variance Check
        st.subheader("Variance")
        validation_lm = total_inv_lm.merge(
            total_prod_lm, 
            left_on='Label', 
            right_on='Coffee Type', 
            how='outer'
        ).fillna(0)
        
        validation_lm['Difference'] = (validation_lm['Inventory'] - validation_lm['Production']).abs()
        validation_lm = validation_lm.round(2)
        
        st.dataframe(
            validation_lm.style
            .format({"Inventory": "{:.2f}", "Production": "{:.2f}", "Difference": "{:.2f}"}), 
            use_container_width=True
        )

elif app_mode == "Dispatch vs Full Tracker":
    st.title("Dispatch vs Full Tracker Reconciliation")
    
    # --- Data Loading ---
    @st.cache_data
    def load_dispatch_data():
        client = get_gspread_client()
        
        # Dispatch Orders
        try:
            sh_dispatch = client.open_by_key("1RfXzquQLqPWh8neSDhbNkQfR2vLJlM0gutRTlZK8Jbg")
            dispatch_orders = pd.DataFrame(sh_dispatch.worksheet("Orders").get_all_records())
            dispatch_items = pd.DataFrame(sh_dispatch.worksheet("Items").get_all_records())
        except Exception as e:
           st.error(f"Error loading 'Nandanvan Dispatch': {e}")
           dispatch_orders = pd.DataFrame()
           dispatch_items = pd.DataFrame()

        # Full Tracker Orders
        try:
            sh_full = client.open_by_key("1wrAHd2f7GcEtrtEpiV4TYYsI81f5tbQtUrzGIjAX55o")
            fulltracker_orders = pd.DataFrame(sh_full.worksheet("Orders").get_all_records())
            fulltracker_items = pd.DataFrame(sh_full.worksheet("Items").get_all_records())
        except Exception as e:
            st.error(f"Error loading 'Full Tracking': {e}")
            fulltracker_orders = pd.DataFrame()
            fulltracker_items = pd.DataFrame()
        
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
    # Convert dates
    dispatch_orders['Date'] = pd.to_datetime(dispatch_orders['Date'], format='%m/%d/%Y', errors='coerce').dt.date
    fulltracker_orders['Dispatch Date'] = pd.to_datetime(fulltracker_orders['Dispatch Date'], format='%m/%d/%Y', errors='coerce').dt.date

    # Clean and convert Invoice Amount columns - remove rupee symbol and convert to float
    if 'Invoice Amount' in dispatch_orders.columns:
        dispatch_orders['Invoice Amount'] = dispatch_orders['Invoice Amount'].astype(str).str.replace('₹', '').str.replace(',', '').str.strip()
        dispatch_orders['Invoice Amount'] = pd.to_numeric(dispatch_orders['Invoice Amount'], errors='coerce').round(2)
    
    if 'Invoice Amount' in fulltracker_orders.columns:
        fulltracker_orders['Invoice Amount'] = fulltracker_orders['Invoice Amount'].astype(str).str.replace('₹', '').str.replace(',', '').str.strip()
        fulltracker_orders['Invoice Amount'] = pd.to_numeric(fulltracker_orders['Invoice Amount'], errors='coerce').round(2)

    # Drop rows with invalid dates in critical columns if necessary, or handle as NaT
    # For now, we keep them but filtering might exclude them automatically

    # --- Sidebar Filters ---
    st.sidebar.header("Filters")
    
    # Use different keys for date inputs to avoid conflict with other view
    st.sidebar.markdown("<br>" * 10, unsafe_allow_html=True)
    
    # Calculate min/max dates
    # Drop NaN values before min/max to avoid comparison errors
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
    
    # Filter Dispatch Orders by Date
    dispatch_orders_filtered = dispatch_orders[
        (dispatch_orders['Date'] >= start_date) & 
        (dispatch_orders['Date'] <= end_date)
    ]
    
    # Filter Full Tracker Orders by Date and PO UID 'YT'
    fulltracker_orders_filtered = fulltracker_orders[
        (fulltracker_orders['Dispatch Date'] >= start_date) & 
        (fulltracker_orders['Dispatch Date'] <= end_date) &
        (fulltracker_orders['PO UID'].str.startswith('YT', na=False))
    ]
    
    # Merge on PO UID (one row per order)
    merged_df = fulltracker_orders_filtered.merge(
        dispatch_orders_filtered, 
        on='PO UID', 
        how='outer', 
        suffixes=('_fulltracker', '_dispatch')
    )
    
    # Build the final dataframe with proper column selection
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
    
    # Convert numeric columns to float to ensure proper formatting
    # Note: Packets Qty columns can contain comma-separated values for multi-SKU orders, so keep them as strings
    numeric_columns = ['Fulltracker Invoice Amount', 'Dispatch Invoice Amount', 
                       'Fulltracker Total Qty (Kg)', 'Dispatch Total Qty (Kg)']
    
    for col in numeric_columns:
        if col in final_df.columns:
            final_df[col] = pd.to_numeric(final_df[col], errors='coerce')
    
    # --- Display ---
    st.subheader("Reconciliation Table")
    
    st.write("Select columns to display:")
    
    # Get all columns from final_df
    available_columns = final_df.columns.tolist()
    
    selected_columns = st.multiselect(
        "Columns", 
        available_columns, 
        default=available_columns
    )
    
    if selected_columns:
        # Apply red text to mismatched values between FullTracker and Dispatch columns
        def highlight_reconciliation_mismatch(row):
            colors = [''] * len(row)
            
            # Get column names that exist in the row
            cols = row.index.tolist()
            
            # Check Invoice Number mismatch
            if 'Fulltracker Invoice Number' in cols and 'Dispatch Invoice Number' in cols:
                ft_inv = row['Fulltracker Invoice Number']
                d_inv = row['Dispatch Invoice Number']
                if pd.isna(ft_inv) or pd.isna(d_inv) or ft_inv != d_inv:
                    colors[cols.index('Fulltracker Invoice Number')] = 'color: #ff0000'
                    colors[cols.index('Dispatch Invoice Number')] = 'color: #ff0000'
            
            # Check Invoice Amount mismatch
            if 'Fulltracker Invoice Amount' in cols and 'Dispatch Invoice Amount' in cols:
                ft_amt = row['Fulltracker Invoice Amount']
                d_amt = row['Dispatch Invoice Amount']
                # Check for NaN first, then compare
                if pd.isna(ft_amt) or pd.isna(d_amt):
                    colors[cols.index('Fulltracker Invoice Amount')] = 'color: #ff0000'
                    colors[cols.index('Dispatch Invoice Amount')] = 'color: #ff0000'
                elif isinstance(ft_amt, (int, float)) and isinstance(d_amt, (int, float)):
                    if abs(ft_amt - d_amt) > 0.01:
                        colors[cols.index('Fulltracker Invoice Amount')] = 'color: #ff0000'
                        colors[cols.index('Dispatch Invoice Amount')] = 'color: #ff0000'
            
            # Check Total Qty mismatch
            if 'Fulltracker Total Qty (Kg)' in cols and 'Dispatch Total Qty (Kg)' in cols:
                ft_qty = row['Fulltracker Total Qty (Kg)']
                d_qty = row['Dispatch Total Qty (Kg)']
                # Check for NaN first, then compare
                if pd.isna(ft_qty) or pd.isna(d_qty):
                    colors[cols.index('Fulltracker Total Qty (Kg)')] = 'color: #ff0000'
                    colors[cols.index('Dispatch Total Qty (Kg)')] = 'color: #ff0000'
                elif isinstance(ft_qty, (int, float)) and isinstance(d_qty, (int, float)):
                    if abs(ft_qty - d_qty) > 0.01:
                        colors[cols.index('Fulltracker Total Qty (Kg)')] = 'color: #ff0000'
                        colors[cols.index('Dispatch Total Qty (Kg)')] = 'color: #ff0000'
            
            # Check SKU mismatch
            if 'Fulltracker SKU' in cols and 'Dispatch SKU' in cols:
                ft_sku = row['Fulltracker SKU']
                d_sku = row['Dispatch SKU']
                if pd.isna(ft_sku) or pd.isna(d_sku):
                    colors[cols.index('Fulltracker SKU')] = 'color: #ff0000'
                    colors[cols.index('Dispatch SKU')] = 'color: #ff0000'
                else:
                    # For SKU comparison, split by comma/newline and compare as sets (order doesn't matter)
                    # This handles both single SKUs and comma-separated multiple SKUs
                    ft_sku_set = set(s.strip() for s in str(ft_sku).replace('\n', ',').split(',') if s.strip())
                    d_sku_set = set(s.strip() for s in str(d_sku).replace('\n', ',').split(',') if s.strip())
                    if ft_sku_set != d_sku_set:
                        colors[cols.index('Fulltracker SKU')] = 'color: #ff0000'
                        colors[cols.index('Dispatch SKU')] = 'color: #ff0000'
            
            # Check Packets Qty mismatch
            if 'Fulltracker Packets Qty' in cols and 'Dispatch Packets Qty' in cols:
                ft_pkt = row['Fulltracker Packets Qty']
                d_pkt = row['Dispatch Packets Qty']
                # Check for NaN first
                if pd.isna(ft_pkt) or pd.isna(d_pkt):
                    colors[cols.index('Fulltracker Packets Qty')] = 'color: #ff0000'
                    colors[cols.index('Dispatch Packets Qty')] = 'color: #ff0000'
                # For numeric values, compare numerically
                elif isinstance(ft_pkt, (int, float)) and isinstance(d_pkt, (int, float)):
                    if abs(ft_pkt - d_pkt) > 0.01:
                        colors[cols.index('Fulltracker Packets Qty')] = 'color: #ff0000'
                        colors[cols.index('Dispatch Packets Qty')] = 'color: #ff0000'
                # For string values (multi-SKU), compare as multisets (order doesn't matter, but frequency does)
                elif isinstance(ft_pkt, str) or isinstance(d_pkt, str):
                    # Split by comma/newline and compare as multisets
                    ft_qty_list = [s.strip() for s in str(ft_pkt).replace('\n', ',').split(',') if s.strip()]
                    d_qty_list = [s.strip() for s in str(d_pkt).replace('\n', ',').split(',') if s.strip()]
                    # Use Counter to compare frequency of each quantity (allows duplicates)
                    if Counter(ft_qty_list) != Counter(d_qty_list):
                        colors[cols.index('Fulltracker Packets Qty')] = 'color: #ff0000'
                        colors[cols.index('Dispatch Packets Qty')] = 'color: #ff0000'
            
            return colors
        
        # Create format dictionary for numeric columns that exist and are numeric
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
    
    # 1. Get List of PO UIDs from the filtered orders
    # We should filter items based on the filtered orders to ensure consistency
    ft_pouids = fulltracker_orders_filtered['PO UID'].unique()
    disp_pouids = dispatch_orders_filtered['PO UID'].unique()
    
    # 2. Filter Items tables
    if not fulltracker_items.empty and not dispatch_items.empty:
        ft_items_filtered = fulltracker_items[fulltracker_items['PO UID'].isin(ft_pouids)]
        disp_items_filtered = dispatch_items[dispatch_items['PO UID'].isin(disp_pouids)]
    else:
        st.warning("Items data not available. Skipping SKU Totals Variance.")
        ft_items_filtered = pd.DataFrame(columns=['SKU', 'Quantity', 'Total Kg'])
        disp_items_filtered = pd.DataFrame(columns=['SKU', 'Quantity', 'Total Kg'])
    
    # 3. Group by SKU and Agg
    fulltracker_items_total_sku = ft_items_filtered.groupby(['SKU']).agg({'Quantity': 'sum', 'Total Kg': 'sum'}).reset_index()
    dispatch_items_total_sku = disp_items_filtered.groupby(['SKU']).agg({'Quantity': 'sum', 'Total Kg': 'sum'}).reset_index()
    
    # 4. Merge
    sku_merge = fulltracker_items_total_sku.merge(
        dispatch_items_total_sku,
        on='SKU',
        how='outer',
        suffixes=('_FullTracker', '_Dispatch')
    ).fillna(0)
    
    # Rename columns for better readability
    sku_merge = sku_merge.rename(columns={
        'Quantity_FullTracker': 'FullTracker Quantity',
        'Total Kg_FullTracker': 'FullTracker Total Kg',
        'Quantity_Dispatch': 'Dispatch Quantity',
        'Total Kg_Dispatch': 'Dispatch Total Kg'
    })
    
    # Convert to numeric to avoid string arithmetic errors
    sku_merge['FullTracker Quantity'] = pd.to_numeric(sku_merge['FullTracker Quantity'], errors='coerce').fillna(0)
    sku_merge['FullTracker Total Kg'] = pd.to_numeric(sku_merge['FullTracker Total Kg'], errors='coerce').fillna(0)
    sku_merge['Dispatch Quantity'] = pd.to_numeric(sku_merge['Dispatch Quantity'], errors='coerce').fillna(0)
    sku_merge['Dispatch Total Kg'] = pd.to_numeric(sku_merge['Dispatch Total Kg'], errors='coerce').fillna(0)
    
    # 5. Calculate Variance (Absolute)
    sku_merge['Quantity Variance'] = (sku_merge['FullTracker Quantity'] - sku_merge['Dispatch Quantity']).abs()
    sku_merge['Total Kg Variance'] = (sku_merge['FullTracker Total Kg'] - sku_merge['Dispatch Total Kg']).abs()
    
    # 6. Format and Display with red text for mismatches
    def highlight_sku_mismatch(row):
        colors = [''] * len(row)
        
        # Get column indices
        cols = row.index.tolist()
        
        # Highlight Quantity columns if variance > 0.01
        if row['Quantity Variance'] > 0.01:
            colors[cols.index('FullTracker Quantity')] = 'color: #ff0000'
            colors[cols.index('Dispatch Quantity')] = 'color: #ff0000'
            colors[cols.index('Quantity Variance')] = 'color: #ff0000'
        
        # Highlight Total Kg columns if variance > 0.01
        if row['Total Kg Variance'] > 0.01:
            colors[cols.index('FullTracker Total Kg')] = 'color: #ff0000'
            colors[cols.index('Dispatch Total Kg')] = 'color: #ff0000'
            colors[cols.index('Total Kg Variance')] = 'color: #ff0000'
        
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
    
    # Calculate total invoice amounts from filtered orders
    # Convert to numeric first to avoid string concatenation errors
    ft_total_invoice = pd.to_numeric(fulltracker_orders_filtered['Invoice Amount'], errors='coerce').fillna(0).sum()
    disp_total_invoice = pd.to_numeric(dispatch_orders_filtered['Invoice Amount'], errors='coerce').fillna(0).sum()
    invoice_variance = abs(ft_total_invoice - disp_total_invoice)
    
    # Create comparison dataframe
    invoice_comparison = pd.DataFrame({
        'Source': ['FullTracker', 'Dispatch', 'Variance'],
        'Total Invoice Amount': [ft_total_invoice, disp_total_invoice, invoice_variance]
    })
    
    # Apply red text if there's a variance
    def highlight_invoice_variance(row):
        colors = [''] * len(row)
        if row['Source'] == 'Variance' and row['Total Invoice Amount'] > 0.01:
            colors[1] = 'color: #ff0000'  # Highlight the amount column
        elif row['Source'] in ['FullTracker', 'Dispatch'] and invoice_variance > 0.01:
            colors[1] = 'color: #ff0000'  # Highlight both source amounts if variance exists
        return colors
    
    st.dataframe(
        invoice_comparison.style
        .apply(highlight_invoice_variance, axis=1)
        .format({"Total Invoice Amount": "{:.2f}"}),
        use_container_width=True
    )
