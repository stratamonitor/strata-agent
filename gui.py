import streamlit as st
import sqlite3
import pandas as pd
import json
import os
import configparser
import plotly.express as px
import time
from datetime import datetime, timezone
import strata
from PIL import Image

FAVICON_SVG = """
<link rel="icon" href="data:image/svg+xml,<svg xmlns=%22http://www.w3.org/2000/svg%22 viewBox=%220 0 16 16%22 fill=%22%230d6efd%22><path d=%22M14 10a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1v-1a1 1 0 0 1 1-1h12zM2 9a2 2 0 0 0-2 2v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-1a2 2 0 0 0-2-2H2z%22/><path d=%22M5 11.5a.5.5 0 1 1-1 0 .5.5 0 0 1 1 0zm-2 0a.5.5 0 1 1-1 0 .5.5 0 0 1 1 0zM14 3a1 1 0 0 1 1 1v1a1 1 0 0 1-1 1H2a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h12zM2 2a2 2 0 0 0-2 2v1a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V4a2 2 0 0 0-2-2H2z%22/><path d=%22M5 4.5a.5.5 0 1 1-1 0 .5.5 0 0 1 1 0zm-2 0a.5.5 0 1 1-1 0 .5.5 0 0 1 1 0z%22/></svg>">
"""

icon = "💾"
if os.path.exists("app.png"):
    try: icon = Image.open("app.png")
    except: pass

st.set_page_config(page_title=f"Strata v{strata.__VERSION__}", page_icon=icon, layout="wide")
st.markdown(FAVICON_SVG, unsafe_allow_html=True) 

st.markdown("""
    <style>
    div[data-testid="stAlert"] {
        padding-top: 0.5rem;
        padding-bottom: 0.5rem;
    }
    </style>
""", unsafe_allow_html=True)

CONFIG_FILE = "strata.ini"

def get_config():
    config = configparser.ConfigParser(); config.read(CONFIG_FILE); return config
def save_config(config):
    with open(CONFIG_FILE, 'w') as f: config.write(f)
def get_db_path(): return get_config().get("General", "db_path", fallback="strata.db")

def format_bytes(size):
    if size is None or size == 0: return "0 B"
    power = 2**10
    n = 0
    power_labels = {0 : 'B', 1: 'KB', 2: 'MB', 3: 'GB', 4: 'TB'}
    while size >= power:
        size /= power
        n += 1
    return f"{size:.2f} {power_labels.get(n, 'PB')}"

def format_timestamp_local(utc_str):
    try:
        try: dt_utc = datetime.fromisoformat(utc_str)
        except ValueError:
            dt_utc = datetime.strptime(utc_str, "%Y-%m-%d %H:%M:%S")
            dt_utc = dt_utc.replace(tzinfo=timezone.utc)
        dt_local = dt_utc.astimezone()
        return dt_local.strftime("%Y-%m-%d %H:%M:%S")
    except: return utc_str 

def format_duration(seconds):
    if seconds is None: return "0s"
    seconds = int(seconds)
    m, s = divmod(seconds, 60)
    h, m = divmod(m, 60)
    if h > 0: return f"{h}h {m}m {s}s"
    if m > 0: return f"{m}m {s}s"
    return f"{s}s"

def check_password():
    config = get_config()
    pwd = config.get("General", "gui_password", fallback="")
    if not pwd: return True
    if "authenticated" not in st.session_state: st.session_state["authenticated"] = False
    if not st.session_state["authenticated"]:
        st.title("🔐 Strata Access")
        input_pwd = st.text_input("Enter Password", type="password")
        if st.button("Login"):
            if input_pwd == pwd:
                st.session_state["authenticated"] = True
                st.rerun()
            else: st.error("Incorrect password")
        return False
    return True

@st.cache_data(ttl=3600, show_spinner=False)
def load_chart_data(db_path, scan_id, total_size):
    threshold = total_size * 0.0005 
    conn = sqlite3.connect(db_path)
    try:
        query = f"SELECT path, parent_path, subtree_size_bytes as size FROM directories WHERE scan_id = {scan_id} AND subtree_size_bytes > {threshold}"
        df = pd.read_sql(query, conn)
    finally: conn.close()
    return df

@st.cache_data(ttl=600, show_spinner=False)
def load_diff_chart_data(db_path, old_scan_id, new_scan_id):
    conn = sqlite3.connect(db_path)
    try:
        q_old = f"SELECT path, parent_path, depth, size_bytes as old_size FROM directories WHERE scan_id = {old_scan_id}"
        old_df = pd.read_sql(q_old, conn)
        
        q_new = f"SELECT path, parent_path, depth, size_bytes as new_size FROM directories WHERE scan_id = {new_scan_id}"
        new_df = pd.read_sql(q_new, conn)
    finally: conn.close()

    df = pd.merge(old_df, new_df, on='path', how='outer', suffixes=('_old', '_new'))
    
    df['parent_path'] = df['parent_path_new'].combine_first(df['parent_path_old'])
    df['depth'] = df['depth_new'].combine_first(df['depth_old'])
    
    df['old_size'] = df['old_size'].fillna(0)
    df['new_size'] = df['new_size'].fillna(0)
    df['diff'] = df['new_size'] - df['old_size']

    df_inc = df[['path', 'parent_path', 'depth', 'diff']].copy()
    df_inc['diff'] = df_inc['diff'].clip(lower=0) 

    df_dec = df[['path', 'parent_path', 'depth', 'diff']].copy()
    df_dec['diff'] = (-df_dec['diff']).clip(lower=0)

    def build_diff_tree(d):
        d = d.sort_values('depth', ascending=False)
        subtree_dict = d.set_index('path')['diff'].to_dict()
        
        for path, parent in zip(d['path'], d['parent_path']):
            if parent and parent in subtree_dict:
                subtree_dict[parent] += subtree_dict[path]
        
        d['subtree_size'] = d['path'].map(subtree_dict)
        return d[d['subtree_size'] > 0].copy()

    df_inc = build_diff_tree(df_inc)
    df_dec = build_diff_tree(df_dec)

    if not df_inc.empty:
        root_inc = df_inc[df_inc['parent_path'] == '']['subtree_size'].max()
        if pd.notna(root_inc) and root_inc > 0:
            df_inc = df_inc[df_inc['subtree_size'] > root_inc * 0.0005]

    if not df_dec.empty:
        root_dec = df_dec[df_dec['parent_path'] == '']['subtree_size'].max()
        if pd.notna(root_dec) and root_dec > 0:
            df_dec = df_dec[df_dec['subtree_size'] > root_dec * 0.0005]

    return df_inc, df_dec

def get_targets(conn):
    try: return pd.read_sql("SELECT DISTINCT root_path FROM scans ORDER BY root_path", conn)['root_path'].tolist()
    except: return[]

def get_snapshots(conn, root_path):
    return pd.read_sql("SELECT id, timestamp, total_size_bytes, disk_total_bytes, disk_free_bytes FROM scans WHERE root_path = ? ORDER BY id DESC", conn, params=(root_path,))

# UPDATED: Reduced cache TTL to 10 minutes (600s) for faster update notifications
@st.cache_data(ttl=600, show_spinner=False)
def get_update_info(current_version):
    return strata.check_for_updates(current_version)

def render_sidebar(conn):
    st.sidebar.title(f"Strata v{strata.__VERSION__}")
    
    if "navigation" not in st.session_state: st.session_state.navigation = "Dashboard"
    page = st.sidebar.radio("Go to",["Dashboard", "🔍 Diff View", "💬 Chat", "Settings"], key="navigation")
    st.sidebar.divider()

    targets = get_targets(conn); options = targets + ["➕ New Scan..."]
    if "target_idx" not in st.session_state: st.session_state.target_idx = 0
    selected_option = st.sidebar.selectbox("Select Target", options, index=0 if targets else 0)
    
    if selected_option == "➕ New Scan...": target_path = st.sidebar.text_input("Enter path", value="/"); is_new = True
    else: target_path = selected_option; is_new = False
    
    st.sidebar.subheader("Actions")
    
    if st.sidebar.button("Scan Now" if not is_new else "Start Initial Scan", type="primary" if is_new else "secondary", use_container_width=True):
        if not target_path: st.sidebar.error("Empty path!")
        else:
            status_text = st.sidebar.empty()
            def update_progress(files, size):
                status_text.markdown(f"**Scanning...**\n\nFiles: {files:,}\nSize: {format_bytes(size)}")

            with st.spinner(f"Scanning {target_path}..."):
                config = get_config(); db = config.get("General", "db_path", fallback="strata.db")
                exc_str = config.get("General", "exclude", fallback=""); excludes =[e.strip() for e in exc_str.split(",") if e.strip()]
                strata.scan_directory(target_path, db, excludes, progress_callback=update_progress)
                load_chart_data.clear()
                status_text.empty()
                st.sidebar.success("Done!"); time.sleep(1); st.rerun()

    if not is_new:
        if st.sidebar.button("🔌 Test Connection", use_container_width=True):
            config = get_config()
            url = config.get("Server", "url", fallback=strata.DEFAULT_SERVER_URL)
            key = config.get("Server", "key", fallback="")
            if not url: st.sidebar.error("Server URL missing!")
            else:
                with st.spinner("Pinging server..."):
                    res = strata.test_connection(url, key)
                    if res["success"]: st.sidebar.success(res["message"])
                    else: st.sidebar.error(res["message"])
        
        if st.sidebar.button("🔄 Check Server Tasks", use_container_width=True):
            config = get_config()
            url = config.get("Server", "url", fallback=strata.DEFAULT_SERVER_URL)
            key = config.get("Server", "key", fallback="")
            db = config.get("General", "db_path", fallback="strata.db")
            if not url or not key: st.sidebar.error("Configure Server & Key in Settings first.")
            else:
                with st.spinner("Checking tasks..."):
                    res = strata.check_tasks(url, key, db)
                    st.sidebar.info(res)
                        
    st.sidebar.divider()
    update_info = get_update_info(strata.__VERSION__)
    if update_info and update_info.get("has_update"):
        st.sidebar.info(f"🚀 **Update Available:** v{update_info['latest_version']}\n\n[Download Here]({update_info['url']})")
    
    return target_path, is_new, page

def view_dashboard(conn, target_path):
    st.header(f"Dashboard: {target_path}")
    snapshots = get_snapshots(conn, target_path)
    if snapshots.empty: st.info("No snapshots."); return
    
    snapshots['label'] = snapshots.apply(lambda x: f"{format_timestamp_local(x['timestamp'])} ({format_bytes(x['total_size_bytes'])})", axis=1)
    
    selected_snapshot_label = st.selectbox("Select Snapshot", snapshots['label'])
    scan_data = snapshots[snapshots['label'] == selected_snapshot_label].iloc[0]
    scan_id = scan_data['id']
    full_scan_data = pd.read_sql(f"SELECT * FROM scans WHERE id = {scan_id}", conn).iloc[0]

    error_count = pd.read_sql(f"SELECT count(*) as cnt FROM scan_errors WHERE scan_id = {scan_id}", conn).iloc[0]['cnt']
    if error_count > 0:
        ec1, ec2 = st.columns([0.85, 0.15], vertical_alignment="center") 
        with ec1:
            st.error(f"{error_count} errors during scan.", icon="⚠️")
        with ec2:
            err_df = pd.read_sql(f"SELECT path, error_message FROM scan_errors WHERE scan_id = {scan_id}", conn)
            log_lines =[]
            for i, r in err_df.iterrows():
                msg = r['error_message']
                if ": '" in msg: msg = msg.split(": '")[0]
                log_lines.append(f"{r['path']} : {msg}")
            
            st.download_button(
                label="Log",
                data="\n".join(log_lines),
                file_name="scan_errors.log",
                mime="text/plain",
                use_container_width=True
            )

    c1, c2, c3, c4 = st.columns(4)
    c1.metric("Time", format_timestamp_local(full_scan_data['timestamp'])) 
    c2.metric("Logical Size", format_bytes(full_scan_data['total_size_bytes']), help="Sum of all file sizes. May exceed Physical Usage if your storage uses Data Deduplication or Compression.")
    c3.metric("Files", f"{full_scan_data['total_files']:,}")
    c4.metric("Duration", format_duration(full_scan_data['scan_duration_sec']))
    
    if 'disk_total_bytes' in full_scan_data and full_scan_data['disk_total_bytes'] and full_scan_data['disk_total_bytes'] > 0:
        total = full_scan_data['disk_total_bytes']; free = full_scan_data['disk_free_bytes']; used = total - free
        percent_used = min(used / total, 1.0)
        st.caption(f"Physical Disk Usage: {format_bytes(used)} used / {format_bytes(total)} total")
        st.progress(percent_used)
    st.divider()

    v_col1, v_col2, v_col3 = st.columns([6, 4, 2], gap="small")
    
    with v_col1: 
        c1, c2 = st.columns([0.6, 0.4]) 
        with c1:
            chart_type = st.radio("Chart Type", ["Sunburst", "Treemap"], horizontal=True, label_visibility="collapsed")
        with c2:
            st.write(""); st.write("") 
            show_labels = st.checkbox("Labels", value=True) 
            
    with v_col3:
        with st.popover("Export", use_container_width=True):
            db_path = get_db_path()
            df = load_chart_data(db_path, scan_id, full_scan_data['total_size_bytes'])
            if not df.empty:
                csv_data = df.to_csv(index=False).encode('utf-8')
                st.download_button("Download CSV", data=csv_data, file_name=f"strata_scan_{scan_id}.csv", mime="text/csv", use_container_width=True)

    with st.spinner("Rendering chart..."):
        if not df.empty:
            df['formatted_size'] = df['size'].apply(format_bytes)
            df['short_name'] = df['path'].apply(lambda p: os.path.basename(p) if p != "/" and p != "" else "ROOT")
            custom_colors =[(0.0, "#7effdb"), (0.142, "#5baa65"), (0.285, "#809e31"), (0.428, "#9d8f23"), (0.571, "#b57f00"), (0.714, "#9e5c1e"), (0.857, "#924424"), (1.0, "#841b2a")]
            if "Sunburst" in chart_type:
                fig = px.sunburst(df, names='path', parents='parent_path', values='size', branchvalues='total', maxdepth=3, color='size', color_continuous_scale=custom_colors, custom_data=['formatted_size', 'short_name'])
            else:
                fig = px.treemap(df, names='path', parents='parent_path', values='size', branchvalues='total', maxdepth=3, color='size', color_continuous_scale=custom_colors, custom_data=['formatted_size', 'short_name'])
            template = '%{customdata[1]}<br>%{customdata[0]}' if show_labels else '%{customdata[1]}'
            fig.update_traces(texttemplate=template, hovertemplate='<b>%{label}</b><br>Size: %{customdata[0]}<br>Path: %{id}<extra></extra>')
            fig.update_layout(margin=dict(t=40, l=10, r=10, b=10), height=700, coloraxis_colorbar=dict(title="Size", tickformat="s"))
            st.plotly_chart(fig, width="stretch")
        else: st.warning("No data.")

def view_diff(conn, target_path):
    st.header(f"Diff View: {target_path}")
    snapshots = get_snapshots(conn, target_path)
    
    if len(snapshots) < 2:
        st.info("You need at least 2 snapshots of this target to perform a Diff comparison.")
        return
        
    snapshots['label'] = snapshots.apply(lambda x: f"{format_timestamp_local(x['timestamp'])} ({format_bytes(x['total_size_bytes'])})", axis=1)
    
    c1, c2 = st.columns(2)
    with c1:
        old_label = st.selectbox("Old Snapshot (Baseline)", snapshots['label'], index=1)
    with c2:
        new_label = st.selectbox("New Snapshot", snapshots['label'], index=0)
        
    old_id = snapshots[snapshots['label'] == old_label].iloc[0]['id']
    new_id = snapshots[snapshots['label'] == new_label].iloc[0]['id']

    if old_id == new_id:
        st.warning("Please select two different snapshots to compare.")
        return

    with st.spinner("Calculating absolute differences..."):
        db_path = get_db_path()
        df_inc, df_dec = load_diff_chart_data(db_path, old_id, new_id)

    st.divider()
    
    v_col1, v_col2 = st.columns([6, 6])
    with v_col1:
        chart_type = st.radio("Chart Type",["Sunburst", "Treemap"], horizontal=True, label_visibility="collapsed", key="diff_chart")
    with v_col2:
        show_labels = st.checkbox("Labels", value=True, key="diff_labels")

    col_inc, col_dec = st.columns(2)

    def plot_diff_chart(df, title, color_scale):
        if df.empty:
            st.info(f"No {title.lower()} detected.")
            return
            
        df['formatted_size'] = df['subtree_size'].apply(lambda x: "+" + format_bytes(x) if "Increase" in title else "-" + format_bytes(x))
        df['short_name'] = df['path'].apply(lambda p: os.path.basename(p) if p != "/" and p != "" else "ROOT")
        
        root_val = df[df['parent_path'] == '']['subtree_size'].sum()
        st.subheader(f"{title}: {format_bytes(root_val)}")
        
        if "Sunburst" in chart_type:
            fig = px.sunburst(df, names='path', parents='parent_path', values='subtree_size', branchvalues='total', maxdepth=3, color='subtree_size', color_continuous_scale=color_scale, custom_data=['formatted_size', 'short_name'])
        else:
            fig = px.treemap(df, names='path', parents='parent_path', values='subtree_size', branchvalues='total', maxdepth=3, color='subtree_size', color_continuous_scale=color_scale, custom_data=['formatted_size', 'short_name'])
        
        template = '%{customdata[1]}<br>%{customdata[0]}' if show_labels else '%{customdata[1]}'
        fig.update_traces(texttemplate=template, hovertemplate='<b>%{label}</b><br>Diff: %{customdata[0]}<br>Path: %{id}<extra></extra>')
        fig.update_layout(margin=dict(t=20, l=10, r=10, b=10), height=500, coloraxis_showscale=False)
        st.plotly_chart(fig, width="stretch")

    with col_inc:
        # UPDATED: Increase = Red
        plot_diff_chart(df_inc, "Total Increase", px.colors.sequential.Reds)
    with col_dec:
        # UPDATED: Decrease = Green
        plot_diff_chart(df_dec, "Total Decrease", px.colors.sequential.Greens)

def view_chat():
    st.header("💬 AI Storage Assistant")
    config = get_config()
    server_url = config.get("Server", "url", fallback=strata.DEFAULT_SERVER_URL)
    server_key = config.get("Server", "key", fallback="")
    debug_mode = config.getboolean("General", "chat_debug", fallback=False)
    
    if not server_key:
        st.info("💡 **Unlock the power of AI!**\n\nConnect to Strata Cloud Server to enable:\n- 💬 Natural Language Chat with your storage\n- 📊 Automated Insight Reports\n- 🧠 Anomaly Detection\n\n[Register and get your API Key at stratamonitor.com](https://stratamonitor.com)")
        return

    if not server_url:
        st.warning("Please configure Server URL in Settings to use Chat.")
        return

    if "messages" not in st.session_state: st.session_state.messages =[]
    if st.button("🗑️ Clear History"):
        st.session_state.messages =[]
        st.rerun()

    for msg in st.session_state.messages:
        if msg["role"] == "user" and msg.get("type") != "tool_result":
            with st.chat_message("user"): st.markdown(msg["content"])
        elif msg["role"] == "assistant" and msg.get("type") != "tool_use":
             with st.chat_message("assistant"): st.markdown(msg["content"])

    if prompt := st.chat_input("Ask about your storage..."):
        with st.chat_message("user"): st.markdown(prompt)
        with st.chat_message("assistant"):
            with st.spinner("Analyzing storage..."):
                db_path = get_db_path(); server_config = {"url": server_url, "key": server_key}
                res = strata.run_chat_loop(prompt, st.session_state.messages, server_config, db_path, debug_mode)
                if res["success"]:
                    st.session_state.messages = res["history"]
                    st.markdown(res["answer"])
                else: st.error(f"Error: {res['message']}")
        st.rerun()

def view_settings():
    st.header("⚙️ Settings"); config = get_config()
    st.subheader("General")
    db_path = st.text_input("Database Path", config.get("General", "db_path", fallback="strata.db"))
    exclude = st.text_area("Global Excludes", config.get("General", "exclude", fallback="/proc,/sys"))
    retention = st.number_input("History Retention (Days)", min_value=0, value=config.getint("General", "retention_days", fallback=0), help="Older scans will be deleted on startup. 0 = Keep forever.")
    if retention > 0: st.warning(f"⚠️ Warning: Scans older than {retention} days will be permanently deleted.")
    gui_pass = st.text_input("GUI Password (Optional)", config.get("General", "gui_password", fallback=""), type="password")
    chat_debug = st.checkbox("Enable Chat Debug Log (chat_debug.log)", value=config.getboolean("General", "chat_debug", fallback=False))
    st.divider()
    st.subheader("☁️ Strata Cloud Server")
    server_url = st.text_input("Server URL (Endpoint /sync)", value=config.get("Server", "url", fallback=strata.DEFAULT_SERVER_URL))
    server_key = st.text_input("Server API Key", config.get("Server", "key", fallback=""), type="password")
    if st.button("Save"):
        if "General" not in config: config["General"] = {}
        if "Server" not in config: config["Server"] = {}
        config["General"]["db_path"] = db_path; config["General"]["exclude"] = exclude; config["General"]["gui_password"] = gui_pass
        config["General"]["retention_days"] = str(retention)
        config["General"]["chat_debug"] = str(chat_debug)
        config["Server"]["url"] = server_url; config["Server"]["key"] = server_key
        save_config(config); st.success("Saved!"); time.sleep(0.5); st.rerun()

def main():
    if not check_password(): return
    db_path = get_db_path()
    if os.path.exists(db_path): 
        strata.init_db(db_path)
        config = get_config()
        retention = config.getint("General", "retention_days", fallback=0)
        if retention > 0: strata.cleanup_retention(db_path, retention)
    conn = sqlite3.connect(db_path) if os.path.exists(db_path) else None
    
    target_path, is_new, page = render_sidebar(conn)
    
    if page == "Dashboard":
        if is_new: st.info("Start Initial Scan")
        elif conn: view_dashboard(conn, target_path)
    elif page == "🔍 Diff View":
        if conn: view_diff(conn, target_path)
        else: st.info("Database not initialized.")
    elif page == "💬 Chat":
        if conn: view_chat()
        else: st.info("Database not initialized.")
    elif page == "Settings": view_settings()
    
    if conn: conn.close()

if __name__ == "__main__": main()