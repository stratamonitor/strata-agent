import sqlite3
import argparse
import os
import json
import time
import urllib.request
import urllib.error
import urllib.parse
import configparser
import sys
import shutil
import socket
import re
from datetime import datetime, timezone, timedelta
from collections import defaultdict

__VERSION__ = "1.2.2"
DEFAULT_SERVER_URL = "https://api.stratamonitor.com/api/v1/agent/sync"

def log(msg):
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    print(f"[{timestamp}] {msg}")

def log_debug(msg, enabled=False):
    if not enabled: return
    timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    try:
        with open("chat_debug.log", "a", encoding="utf-8") as f:
            f.write(f"[{timestamp}] {msg}\n")
    except: pass

def load_config(config_path):
    config = configparser.ConfigParser()
    if os.path.exists(config_path): config.read(config_path)
    return config

try: import pwd
except ImportError: pwd = None

def get_owner_name(uid):
    if pwd:
        try: return pwd.getpwuid(uid).pw_name
        except KeyError: return str(uid)
    return str(uid)

def get_db_connection(db_path): return sqlite3.connect(db_path)

def init_db(db_path):
    conn = get_db_connection(db_path)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS scans (id INTEGER PRIMARY KEY AUTOINCREMENT, timestamp DATETIME DEFAULT CURRENT_TIMESTAMP, root_path TEXT, total_size_bytes INTEGER, disk_usage_bytes INTEGER, total_files INTEGER, scan_duration_sec REAL, disk_total_bytes INTEGER, disk_free_bytes INTEGER)''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS directories (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, path TEXT, parent_path TEXT, depth INTEGER, size_bytes INTEGER, subtree_size_bytes INTEGER, file_count INTEGER, mtime REAL, top_extensions_json TEXT, top_owners_json TEXT, FOREIGN KEY(scan_id) REFERENCES scans(id))''')
    cursor.execute('''CREATE TABLE IF NOT EXISTS scan_errors (id INTEGER PRIMARY KEY AUTOINCREMENT, scan_id INTEGER, path TEXT, error_message TEXT, FOREIGN KEY(scan_id) REFERENCES scans(id))''')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_dirs_scan_id ON directories(scan_id)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_dirs_parent ON directories(parent_path)')
    cursor.execute('CREATE INDEX IF NOT EXISTS idx_scans_root ON scans(root_path)')
    try: cursor.execute('ALTER TABLE scans ADD COLUMN disk_total_bytes INTEGER')
    except: pass
    try: cursor.execute('ALTER TABLE scans ADD COLUMN disk_free_bytes INTEGER')
    except: pass
    conn.commit()
    conn.close()

def cleanup_retention(db_path, days_to_keep):
    if days_to_keep <= 0: return
    if not os.path.exists(db_path): return
    try:
        conn = get_db_connection(db_path)
        cursor = conn.cursor()
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days_to_keep)
        cutoff_str = cutoff_date.strftime("%Y-%m-%d %H:%M:%S")
        cursor.execute("SELECT id FROM scans WHERE timestamp < ?", (cutoff_str,))
        scan_ids = [row[0] for row in cursor.fetchall()]
        if not scan_ids:
            conn.close(); return
        log(f"Cleaning up {len(scan_ids)} old scans (older than {days_to_keep} days)...")
        ids_tuple = tuple(scan_ids)
        if len(ids_tuple) == 1: ids_tuple = f"({ids_tuple[0]})"
        else: ids_tuple = str(ids_tuple)
        cursor.execute(f"DELETE FROM directories WHERE scan_id IN {ids_tuple}")
        cursor.execute(f"DELETE FROM scan_errors WHERE scan_id IN {ids_tuple}")
        cursor.execute(f"DELETE FROM scans WHERE id IN {ids_tuple}")
        conn.commit(); cursor.execute("VACUUM"); conn.close()
        log("Cleanup complete.")
    except Exception as e: log(f"Error during cleanup: {e}")

def fast_walk_bottom_up(top, on_error=None):
    dirs = []; files =[]
    try:
        with os.scandir(top) as it:
            for entry in it:
                try:
                    if entry.is_dir(follow_symlinks=False): dirs.append(entry)
                    elif entry.is_file(follow_symlinks=False): files.append(entry)
                except OSError: pass
    except OSError as err:
        if on_error: on_error(err)
        return
    for d in dirs: yield from fast_walk_bottom_up(d.path, on_error)
    yield top, dirs, files

def scan_directory(root_path, db_path, exclude_list=None, progress_callback=None):
    if exclude_list is None: exclude_list =[]
    log(f"--- Scanning: {root_path} (High Performance) ---")
    if not os.path.exists(db_path): init_db(db_path)
    start_time = time.time()
    scan_time_utc = datetime.now(timezone.utc)
    
    disk_total = 0; disk_free = 0
    try:
        usage = shutil.disk_usage(root_path)
        disk_total = usage.total; disk_free = usage.free
    except: pass
    
    conn = get_db_connection(db_path)
    conn.execute("PRAGMA synchronous = OFF")
    conn.execute("PRAGMA journal_mode = MEMORY")
    
    cursor = conn.cursor()
    cursor.execute('INSERT INTO scans (root_path, disk_total_bytes, disk_free_bytes, timestamp) VALUES (?, ?, ?, ?)', 
                   (root_path, disk_total, disk_free, scan_time_utc))
    scan_id = cursor.lastrowid
    conn.commit()
    
    total_scan_size = 0; total_scan_files = 0; subtree_sizes = defaultdict(int)
    root_path = os.path.abspath(root_path)
    exclude_list =[os.path.abspath(ex) for ex in exclude_list]

    def on_walk_error(e):
        try:
            err_conn = get_db_connection(db_path); err_c = err_conn.cursor()
            path = getattr(e, 'filename', str(e))
            err_c.execute('INSERT INTO scan_errors (scan_id, path, error_message) VALUES (?, ?, ?)', (scan_id, path, str(e)))
            err_conn.commit(); err_conn.close()
        except: pass

    batch_data =[]
    BATCH_SIZE = 10000
    files_since_update = 0

    for current_path, dirs, files in fast_walk_bottom_up(root_path, on_error=on_walk_error):
        if any(current_path.startswith(ex) for ex in exclude_list): continue
        
        current_dir_size = 0; current_dir_files_count = 0
        extensions = defaultdict(int); owners = defaultdict(int); max_mtime = 0
        
        for entry in files:
            try:
                stat = entry.stat()
                current_dir_size += stat.st_size; current_dir_files_count += 1
                ext = os.path.splitext(entry.name)[1].lower().lstrip('.') or "no_ext"
                extensions[ext] += stat.st_size
                owner = str(stat.st_uid)
                if pwd:
                    try: owner = pwd.getpwuid(stat.st_uid).pw_name
                    except: pass
                owners[owner] += stat.st_size
                if stat.st_mtime > max_mtime: max_mtime = stat.st_mtime
            except OSError: pass
        
        current_subtree_size = current_dir_size
        for d in dirs: current_subtree_size += subtree_sizes.get(d.path, 0)
        subtree_sizes[current_path] = current_subtree_size
        
        top_ext = json.dumps(dict(sorted(extensions.items(), key=lambda x: x[1], reverse=True)[:5]))
        top_own = json.dumps(dict(sorted(owners.items(), key=lambda x: x[1], reverse=True)[:5]))
        parent_path = "" if current_path == root_path else os.path.dirname(current_path)
        rel_path = current_path[len(root_path):]; depth = rel_path.count(os.sep)
        
        batch_data.append((scan_id, current_path, parent_path, depth, current_dir_size, current_subtree_size, current_dir_files_count, max_mtime, top_ext, top_own))
        total_scan_size += current_dir_size
        total_scan_files += current_dir_files_count
        
        if len(batch_data) >= BATCH_SIZE:
            cursor.executemany('''INSERT INTO directories (scan_id, path, parent_path, depth, size_bytes, subtree_size_bytes, file_count, mtime, top_extensions_json, top_owners_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', batch_data)
            conn.commit()
            batch_data =[]

        if progress_callback:
            files_since_update += current_dir_files_count
            if files_since_update >= 2000:
                progress_callback(total_scan_files, total_scan_size)
                files_since_update = 0

    if batch_data:
        cursor.executemany('''INSERT INTO directories (scan_id, path, parent_path, depth, size_bytes, subtree_size_bytes, file_count, mtime, top_extensions_json, top_owners_json) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)''', batch_data)
        conn.commit()

    duration = time.time() - start_time
    disk_usage_scan = disk_total - disk_free if disk_total > 0 else 0
    cursor.execute('UPDATE scans SET total_size_bytes=?, total_files=?, scan_duration_sec=?, disk_usage_bytes=? WHERE id=?', (total_scan_size, total_scan_files, duration, disk_usage_scan, scan_id))
    conn.commit()
    conn.close()
    
    if progress_callback: progress_callback(total_scan_files, total_scan_size)
    log(f"--- Scan Complete. Snapshot ID: {scan_id}. Size: {total_scan_size/1048576:.2f} MB ---")
    return scan_id

def send_to_server(report_data, api_url, api_key):
    log(f"Syncing with: {api_url}")
    if not api_url: return {"success": False, "message": "Server URL is not configured."}
    try: hostname = socket.gethostname()
    except: hostname = "unknown_host"
    timestamp = datetime.now().astimezone().isoformat()
    payload_dict = {"hostname": hostname, "timestamp": timestamp, "report": report_data}
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}', 'User-Agent': f'StrataClient/{__VERSION__}'}
    payload = json.dumps(payload_dict).encode('utf-8')
    req = urllib.request.Request(api_url, data=payload, method='POST', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=30) as response:
            resp_body = response.read().decode('utf-8')
            try:
                raw_result = json.loads(resp_body)
                return {"success": True, "message": "Sync OK", "server_response": raw_result}
            except json.JSONDecodeError: return {"success": True, "message": "Sent, but response is not JSON.", "raw": resp_body}
    except urllib.error.HTTPError as e:
        try: err_body = e.read().decode('utf-8')
        except: err_body = "N/A"
        return {"success": False, "status": e.code, "message": f"Server Error {e.code}: {err_body}"}
    except Exception as e: return {"success": False, "message": f"Error: {str(e)}"}

def test_connection(api_url, api_key):
    if not api_url: return {"success": False, "message": "Server URL is empty"}
    if "/sync" in api_url: base_ping = api_url.replace("/sync", "/ping")
    else: base_ping = api_url.rstrip("/") + "/ping"
    try: hostname = socket.gethostname()
    except: hostname = "unknown"
    safe_hostname = urllib.parse.quote(hostname)
    separator = "&" if "?" in base_ping else "?"
    ping_url = f"{base_ping}{separator}hostname={safe_hostname}"
    log(f"Testing connection/registering agent: {ping_url}")
    headers = {'Authorization': f'Bearer {api_key}', 'User-Agent': f'StrataClient/{__VERSION__}'}
    req = urllib.request.Request(ping_url, method='GET', headers=headers)
    try:
        with urllib.request.urlopen(req, timeout=10) as response:
            return {"success": True, "message": "Connection Established!", "code": 200} if response.status == 200 else {"success": False, "message": f"Status {response.status}"}
    except Exception as e: return {"success": False, "message": str(e)}

# UPDATED: Allow WITH clauses for Common Table Expressions (CTEs)
def execute_sql_task(db_path, query):
    if not query: return {"error": "Received empty SQL query from server."}
    
    # Clean the query and check if it starts with SELECT or WITH
    cleaned_query = query.strip().upper()
    if not (cleaned_query.startswith("SELECT") or cleaned_query.startswith("WITH")): 
        return {"error": "Only SELECT or WITH queries are allowed."}
        
    try:
        conn = get_db_connection(db_path)
        conn.row_factory = sqlite3.Row
        cursor = conn.cursor()
        cursor.execute(query)
        rows = cursor.fetchall()
        result = [dict(row) for row in rows]
        conn.close()
        return {"data": result}
    except Exception as e: return {"error": str(e)}

def check_tasks(api_url, api_key, db_path):
    if not api_url: return "Server URL not configured."
    base_url = api_url.split("/agent")[0] + "/agent"
    tasks_url = f"{base_url}/tasks"; result_url = f"{base_url}/tasks/result"
    try: hostname = socket.gethostname()
    except: hostname = "unknown"
    
    log(f"Checking tasks at: {tasks_url}")
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}', 'User-Agent': f'StrataClient/{__VERSION__}'}
    
    summary =[]
    
    try:
        req = urllib.request.Request(tasks_url, data=json.dumps({"hostname": hostname}).encode('utf-8'), method='POST', headers=headers)
        with urllib.request.urlopen(req, timeout=10) as response:
            tasks = json.loads(response.read().decode('utf-8')).get("tasks",[])
        
        if not tasks: 
            return "No pending tasks."
        
        summary.append(f"Found {len(tasks)} tasks.")
        
        for task in tasks:
            task_id = task.get("id"); task_type = task.get("type")
            log(f"Processing Task {task_id}: {task_type}")
            
            result_data = {}
            status = "completed"
            
            if task_type == "sql_query":
                query = task.get("payload", {}).get("query", "")
                result_data = execute_sql_task(db_path, query)
                if "error" in result_data: status = "error"
                summary.append(f"Task {task_id} (SQL): {status}")
            
            elif task_type == "execute_sql_batch":
                sqls = task.get("payload", {}).get("sqls",[])
                if not isinstance(sqls, list): result_data = {"error": "Payload 'sqls' must be a list"}; status = "error"
                else:
                    batch_output =[]
                    log(f"Processing batch of {len(sqls)} SQL queries...")
                    for sql in sqls:
                        res = execute_sql_task(db_path, sql)
                        batch_output.append({"query": sql, "success": "data" in res, "data": res.get("data"), "error": res.get("error")})
                    result_data = {"results": batch_output}
                summary.append(f"Task {task_id} (Batch SQL): {status}")

            elif task_type == "autonomous_chat":
                prompt = task.get("payload", {}).get("initial_prompt")
                if not prompt:
                    result_data = {"error": "Missing initial_prompt in payload"}; status = "error"
                else:
                    log(f"Starting Autonomous Report Generation: {prompt}")
                    server_config = {"url": api_url, "key": api_key}
                    chat_res = run_chat_loop(prompt,[], server_config, db_path, debug_mode=True)
                    if chat_res["success"]: result_data = {"text": chat_res["answer"]}
                    else: result_data = {"error": chat_res.get("message", "Unknown error")}; status = "error"
                summary.append(f"Task {task_id} (Auto-Chat): {status}")
            
            else: 
                result_data = {"error": "Unknown task type"}; status = "error"
                summary.append(f"Task {task_id} (Unknown): Error")
            
            res_payload = {"task_id": task_id, "status": status, "result": result_data, "hostname": hostname}
            res_req = urllib.request.Request(result_url, data=json.dumps(res_payload).encode('utf-8'), method='POST', headers=headers)
            with urllib.request.urlopen(res_req, timeout=10) as res_response:
                log(f"Task {task_id} result sent. Server: {res_response.status}")
                
        return "\n".join(summary)
        
    except Exception as e: 
        log(f"Task processing error: {e}")
        return f"Error: {str(e)}"

def run_chat_loop(user_query, history, server_config, db_path, debug_mode=False):
    if not server_config.get("url"): 
        return {"success": False, "message": "Server URL not configured"}
    base_url = server_config['url'].split("/agent")[0]
    chat_url = f"{base_url}/chat/turn"
    api_key = server_config['key']
    try: hostname = socket.gethostname()
    except: hostname = "unknown"
    history.append({"role": "user", "content": user_query})
    headers = {'Content-Type': 'application/json', 'Authorization': f'Bearer {api_key}', 'User-Agent': f'StrataClient/{__VERSION__}'}
    loop_count = 0; max_loops = 10 
    while loop_count < max_loops:
        loop_count += 1
        log_debug(f"--- Loop {loop_count} ---", debug_mode)
        payload = {"hostname": hostname, "history": history}
        log_debug(f"OUTGOING PAYLOAD: {json.dumps(payload, ensure_ascii=False)}", debug_mode)
        retry_count = 0; max_retries = 3; response_data = None
        while retry_count < max_retries:
            try:
                req = urllib.request.Request(chat_url, data=json.dumps(payload).encode('utf-8'), method='POST', headers=headers)
                with urllib.request.urlopen(req, timeout=60) as response:
                    raw_resp = response.read().decode('utf-8')
                    log(f"Server Response (Loop {loop_count}): {raw_resp}")
                    log_debug(f"INCOMING RESPONSE: {raw_resp}", debug_mode)
                    try: response_data = json.loads(raw_resp); break 
                    except json.JSONDecodeError: return {"success": False, "message": "Invalid JSON from server"}
            except urllib.error.HTTPError as e:
                if e.code == 429:
                    retry_count += 1; wait_time = 10; log(f"⚠️ Rate Limit (429). Retrying in {wait_time}s... ({retry_count}/{max_retries})"); time.sleep(wait_time)
                    if retry_count == max_retries: return {"success": False, "message": "Server Rate Limit Exceeded (429)."}
                else:
                    try: err = e.read().decode('utf-8')
                    except: err = str(e)
                    return {"success": False, "message": f"HTTP {e.code}: {err}"}
            except Exception as e: return {"success": False, "message": f"Network Error: {str(e)}"}
        if not response_data: return {"success": False, "message": "Failed to get response after retries."}
        action = response_data.get("action"); log_debug(f"Server Action: {action}", debug_mode)
        if action == "execute_sql":
            sql_query = response_data.get("sql")
            if not sql_query: sql_query = response_data.get("content")
            if not sql_query or not str(sql_query).strip():
                error_msg = json.dumps({"error": "Empty SQL query received."})
                history.append({"role": "assistant", "type": "tool_use", "content": ""}) 
                history.append({"role": "user", "type": "tool_result", "content": error_msg})
                log(f"Warning: Empty SQL. Retrying..."); time.sleep(5); continue
            history.append({"role": "assistant", "type": "tool_use", "content": sql_query})
            log_debug(f"Executing SQL: {sql_query}", debug_mode)
            exec_result = execute_sql_task(db_path, sql_query)
            result_str = json.dumps(exec_result, ensure_ascii=False)
            history.append({"role": "user", "type": "tool_result", "content": result_str})
            rows_count = len(exec_result.get('data',[])) if 'data' in exec_result else 'Error'
            log_debug(f"Rows returned: {rows_count}", debug_mode)
            log_debug(f"SQL RESULT PAYLOAD: {result_str}", debug_mode)
            time.sleep(5); continue
        elif action == "final_answer":
            answer_text = response_data.get("text")
            if not answer_text: answer_text = response_data.get("content")
            if not answer_text: answer_text = "(No text provided)"
            history.append({"role": "assistant", "content": answer_text})
            return {"success": True, "history": history, "answer": answer_text}
        else: return {"success": False, "message": f"Unknown server action: {action}"}
    return {"success": False, "message": "Max autonomous iterations reached."}

def check_for_updates(current_version):
    """Checks the GitHub API for the latest release tag."""
    try:
        url = "https://api.github.com/repos/stratamonitor/strata-agent/releases/latest"
        req = urllib.request.Request(url, headers={'User-Agent': f'StrataClient/{current_version}'})
        with urllib.request.urlopen(req, timeout=2) as response:
            data = json.loads(response.read().decode('utf-8'))
            
            tag_name = data.get("tag_name", "")
            html_url = data.get("html_url", "")
            
            if not tag_name: 
                return None
            
            def extract_v(v_str):
                m = re.search(r'(\d+)\.(\d+)(?:\.(\d+))?', v_str)
                if m: return tuple(int(x) if x else 0 for x in m.groups())
                return (0,0,0)

            curr_parts = extract_v(current_version)
            latest_parts = extract_v(tag_name)
            
            if latest_parts > curr_parts:
                return {"has_update": True, "latest_version": tag_name.lstrip('v'), "url": html_url}
                    
            return {"has_update": False}
    except Exception:
        return None

if __name__ == "__main__":
    config = load_config("strata.ini")
    defaults = {"db": config.get("General", "db_path", fallback="strata.db"), "exclude": ""}
    parser = argparse.ArgumentParser(description=f"Strata CLI v{__VERSION__}", formatter_class=argparse.RawTextHelpFormatter)
    parser.add_argument("--scan", type=str); parser.add_argument("--report", type=str); parser.add_argument("--db", type=str, default=defaults["db"]); parser.add_argument("--exclude", type=str)
    parser.add_argument("--check-tasks", action="store_true", help="Check server for tasks")
    args = parser.parse_args()
    
    update_info = check_for_updates(__VERSION__)
    if update_info and update_info.get("has_update"):
        print(f"\033[93m⚠️  UPDATE AVAILABLE: A new version (v{update_info['latest_version']}) is available! Download at: {update_info['url']}\033[0m\n")
    
    excludes = []
    if args.scan: scan_directory(args.scan, args.db, excludes)
    elif args.check_tasks:
        url = config.get("Server", "url", fallback=DEFAULT_SERVER_URL); key = config.get("Server", "key", fallback="")
        if url and key: 
            res = check_tasks(url, key, args.db)
            print(res) 
        else: log("Server URL/Key missing in strata.ini")
    else: parser.print_help()