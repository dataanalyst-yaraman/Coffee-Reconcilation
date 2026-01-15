import streamlit as st
import pandas as pd
import datetime

# Page Config
st.set_page_config(page_title="Inventory vs Production", layout="wide")

# --- Sidebar Navigation ---
st.sidebar.header("Overview")
if st.sidebar.button("Refresh Data", key="refresh_data_top"):
    st.cache_data.clear()

app_mode = st.sidebar.radio("Go to", ["Inventory vs Production", "Dispatch vs Full Tracker"])

if app_mode == "Inventory vs Production":
    st.title("Inventory vs Production Reconciliation")
    
    # --- Data Loading ---
    @st.cache_data
    def load_inv_prod_data():
        # Inventory Sheet
        sheet_id_inv = "1JTeE3dwYJj6cXGFF-MUWsmyX3aD3sHwh4dBR0KEgMVQ"
        gid_inv = "2013262881"
        url_inv = f"https://docs.google.com/spreadsheets/d/{sheet_id_inv}/export?format=csv&gid={gid_inv}"
        inventory = pd.read_csv(url_inv)
        
        # Production Sheet
        sheet_id_prod = "1C8nwOXF1u944Km_5DcG34ntvmuWx9JBPhdyvsZT2bC8"
        gid_prod = "0"
        url_prod = f"https://docs.google.com/spreadsheets/d/{sheet_id_prod}/export?format=csv&gid={gid_prod}"
        production = pd.read_csv(url_prod)
        
        return inventory, production

    try:
        inventory_df, production_df = load_inv_prod_data()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    # --- Preprocessing ---
    # Convert dates
    inventory_df['Date'] = pd.to_datetime(inventory_df['Date'], format='%m/%d/%Y', errors='coerce')
    production_df['Date'] = pd.to_datetime(production_df['Date'], format='%m/%d/%Y', errors='coerce')

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

    # Convert inputs to datetime for comparison
    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)

    # Filter Data by Date
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
        # Display Merged Data
        st.subheader("Detailed Comparison")
        st.dataframe(merged_gb.style.format({"Inventory": "{:.2f}", "Production": "{:.2f}"}), use_container_width=True)
        
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
        validation_gb['Match'] = validation_gb['Difference'].abs() < 0.01 # Tolerance
        validation_gb = validation_gb.round(2)
        
        # Apply color and format
        st.dataframe(
            validation_gb.style
            .apply(lambda x: ['background-color: #d4edda' if v else 'background-color: #f8d7da' for v in x], subset=['Match'])
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
        # Display Merged Data
        st.subheader("Detailed Comparison")
        st.dataframe(merged_rb.style.format({"Inventory": "{:.2f}", "Production": "{:.2f}"}), use_container_width=True)
        
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
        validation_rb['Match'] = validation_rb['Difference'].abs() < 0.01
        validation_rb = validation_rb.round(2)
        
        st.dataframe(
            validation_rb.style
            .apply(lambda x: ['background-color: #d4edda' if v else 'background-color: #f8d7da' for v in x], subset=['Match'])
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
        # Display Merged Data
        st.subheader("Detailed Comparison")
        st.dataframe(merged_lm.style.format({"Inventory": "{:.2f}", "Production": "{:.2f}"}), use_container_width=True)
        
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
        validation_lm['Match'] = validation_lm['Difference'].abs() < 0.01
        validation_lm = validation_lm.round(2)
        
        st.dataframe(
            validation_lm.style
            .apply(lambda x: ['background-color: #d4edda' if v else 'background-color: #f8d7da' for v in x], subset=['Match'])
            .format({"Inventory": "{:.2f}", "Production": "{:.2f}", "Difference": "{:.2f}"}), 
            use_container_width=True
        )

elif app_mode == "Dispatch vs Full Tracker":
    st.title("Dispatch vs Full Tracker Reconciliation")
    
    # --- Data Loading ---
    @st.cache_data
    def load_dispatch_data():
        # Dispatch Orders
        sheet_id_dispatch = "1RfXzquQLqPWh8neSDhbNkQfR2vLJlM0gutRTlZK8Jbg"
        gid_dispatch = "958246669"
        url_dispatch = f"https://docs.google.com/spreadsheets/d/{sheet_id_dispatch}/export?format=csv&gid={gid_dispatch}"
        dispatch_orders = pd.read_csv(url_dispatch)
        
        # Dispatch Items (for SKU totals)
        gid_dispatch_items = "1450158975"
        url_dispatch_items = f"https://docs.google.com/spreadsheets/d/{sheet_id_dispatch}/export?format=csv&gid={gid_dispatch_items}"
        dispatch_items = pd.read_csv(url_dispatch_items)
        
        # Full Tracker Orders
        sheet_id_full = "1wrAHd2f7GcEtrtEpiV4TYYsI81f5tbQtUrzGIjAX55o"
        gid_full = "1113783161"
        url_full = f"https://docs.google.com/spreadsheets/d/{sheet_id_full}/export?format=csv&gid={gid_full}"
        fulltracker_orders = pd.read_csv(url_full)
        
        # Full Tracker Items (for SKU totals)
        gid_full_items = "712686230"
        url_full_items = f"https://docs.google.com/spreadsheets/d/{sheet_id_full}/export?format=csv&gid={gid_full_items}"
        fulltracker_items = pd.read_csv(url_full_items)
        
        return dispatch_orders, dispatch_items, fulltracker_orders, fulltracker_items

    try:
        dispatch_orders, dispatch_items, fulltracker_orders, fulltracker_items = load_dispatch_data()
    except Exception as e:
        st.error(f"Error loading data: {e}")
        st.stop()

    # --- Preprocessing ---
    # Convert dates
    dispatch_orders['Date'] = pd.to_datetime(dispatch_orders['Date'], format='%m/%d/%Y', errors='coerce')
    fulltracker_orders['Dispatch Date'] = pd.to_datetime(fulltracker_orders['Dispatch Date'], format='%m/%d/%Y', errors='coerce')

    # Drop rows with invalid dates in critical columns if necessary, or handle as NaT
    # For now, we keep them but filtering might exclude them automatically

    # --- Sidebar Filters ---
    st.sidebar.header("Filters")
    
    # Use different keys for date inputs to avoid conflict with other view
    st.sidebar.markdown("<br>" * 10, unsafe_allow_html=True)
    
    # Calculate min/max dates
    min_date_disp = dispatch_orders['Date'].min()
    max_date_disp = dispatch_orders['Date'].max()
    min_date_ft = fulltracker_orders['Dispatch Date'].dropna().min()
    max_date_ft = fulltracker_orders['Dispatch Date'].dropna().max()
    
    # Handle NaTs if any
    if pd.isna(min_date_disp): min_date_disp = pd.Timestamp.now()
    if pd.isna(max_date_disp): max_date_disp = pd.Timestamp.now()
    if pd.isna(min_date_ft): min_date_ft = pd.Timestamp.now()
    if pd.isna(max_date_ft): max_date_ft = pd.Timestamp.now()

    min_date = min(min_date_disp, min_date_ft)
    max_date = max(max_date_disp, max_date_ft)

    if isinstance(min_date, pd.Timestamp): min_date = min_date.date()
    if isinstance(max_date, pd.Timestamp): max_date = max_date.date()

    start_date = st.sidebar.date_input("Start Date", min_date, key="disp_start_date")
    end_date = st.sidebar.date_input("End Date", max_date, key="disp_end_date")

    start_date = pd.to_datetime(start_date)
    end_date = pd.to_datetime(end_date)
    
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
    
    # Merge on PO UID
    # suffixes=('_fulltracker', '_dispatch')
    merged_df = fulltracker_orders_filtered.merge(
        dispatch_orders_filtered, 
        on='PO UID', 
        how='outer', 
        suffixes=('_fulltracker', '_dispatch')
    )
    
    # Rename columns that might have collided but didn't get suffixes if they weren't in both or handled differently
    # The user specifically requested these output columns:
    # ['PO UID', 'Dispatch Date', 'Invoice Number_fulltracker', 'Invoice Amount_fulltracker', 'Total Qty (Kg)', 'SKU', 'Qty (No.)', 'Date', 'Invoice Number_dispatch', 'Invoice Amount_dispatch', 'Quantity (Kg)', 'Product (SKU)', 'No of Packets']
    
    # We need to ensure these columns exist. 
    # 'Invoice Number' exists in both? 
    # Let's check the source columns from the notebook logic or previous viewing.
    # dispatch_orders: 'Invoice Number', 'Invoice Amount'
    # fulltracker_orders: 'Invoice Number', 'Invoice Amount'
    # So suffixes will apply to these.
    
    # Select requested columns
    all_target_columns = [
        'PO UID', 
        'Dispatch Date', 
        'Invoice Number_fulltracker', 
        'Invoice Amount_fulltracker', 
        'Total Qty (Kg)', 
        'SKU', 
        'Qty (No.)', 
        'Date', 
        'Invoice Number_dispatch', 
        'Invoice Amount_dispatch', 
        'Quantity (Kg)', 
        'Product (SKU)', 
        'No of Packets'
    ]
    
    # Ensure columns exist (fill with NaN if missing due to outer join or naming mismatch)
    for col in all_target_columns:
        if col not in merged_df.columns:
            merged_df[col] = pd.NA
            
    final_df = merged_df[all_target_columns]
    
    # Rename Columns
    rename_map = {
        'Dispatch Date': 'Fulltracker Dispatch Date',
        'Invoice Number_fulltracker': 'Fulltracker Invoice Number',
        'Invoice Amount_fulltracker': 'Fulltracker Invoice Amount',
        'Total Qty (Kg)': 'Fulltracker Total Qty (Kg)',
        'SKU': 'Fulltracker SKU',
        'Qty (No.)': 'Fulltracker Packets Qty',
        'Date': 'Dispatch Date',
        'Invoice Number_dispatch': 'Dispatch Invoice Number',
        'Invoice Amount_dispatch': 'Dispatch Invoice Amount',
        'Quantity (Kg)': 'Dispatch Total Qty (Kg)',
        'Product (SKU)': 'Dispatch SKU',
        'No of Packets': 'Dispatch Packets Qty'
    }
    
    final_df = final_df.rename(columns=rename_map)
    
    # --- Display ---
    st.subheader("Reconciliation Table")
    
    st.write("Select columns to display:")
    
    # Update default options to use renamed columns
    renamed_options = [rename_map.get(col, col) for col in all_target_columns]
    
    selected_columns = st.multiselect(
        "Columns", 
        renamed_options, 
        default=renamed_options
    )
    
    if selected_columns:
        # Check if user wants wrapping (User asked "Add warp for cells")
        # Apply wrapping via Pandas Styler
        st.dataframe(
            final_df[selected_columns].style.set_properties(**{'white-space': 'pre-wrap'}), 
            use_container_width=True
        )
    else:
        st.write("Please select at least one column.")

    # --- SKU Totals Comparison ---
    st.divider()
    st.subheader("SKU Totals Side-by-Side Validation")
    
    # 1. Get List of PO UIDs from the filtered orders
    # We should filter items based on the filtered orders to ensure consistency
    ft_pouids = fulltracker_orders_filtered['PO UID'].unique()
    disp_pouids = dispatch_orders_filtered['PO UID'].unique()
    
    # 2. Filter Items tables
    ft_items_filtered = fulltracker_items[fulltracker_items['PO UID'].isin(ft_pouids)]
    disp_items_filtered = dispatch_items[dispatch_items['PO UID'].isin(disp_pouids)]
    
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
    
    # 5. Calculate Variance (Absolute)
    sku_merge['Quantity Variance'] = (sku_merge['Quantity_FullTracker'] - sku_merge['Quantity_Dispatch']).abs()
    sku_merge['Total Kg Variance'] = (sku_merge['Total Kg_FullTracker'] - sku_merge['Total Kg_Dispatch']).abs()
    
    # 6. Format and Display
    st.dataframe(
        sku_merge.style.format({
            "Quantity_FullTracker": "{:.2f}",
            "Total Kg_FullTracker": "{:.2f}",
            "Quantity_Dispatch": "{:.2f}",
            "Total Kg_Dispatch": "{:.2f}",
            "Quantity Variance": "{:.2f}",
            "Total Kg Variance": "{:.2f}"
        }),
        use_container_width=True
    )
