import PyInstaller.__main__
import shutil
import os
import streamlit
import re
import tarfile
import time
import sys

streamlit_path = os.path.dirname(streamlit.__file__)
streamlit_static_src = os.path.join(streamlit_path, 'static')
streamlit_data_arg = f'{streamlit_static_src}:streamlit/static'

version = "0.0.0"
with open("strata.py", "r", encoding="utf-8") as f:
    content = f.read()
    match = re.search(r'__VERSION__\s*=\s*"([^"]+)"', content)
    if match: version = match.group(1)

print(f"[INFO] Building Version: {version}")

DIST_DIR = "dist/strata_gui"
FINAL_DIR = "dist/strata"

if os.path.exists(DIST_DIR): shutil.rmtree(DIST_DIR)
print("[INFO] Starting build process...")

gui_args = [
    'run_app.py', '--name=strata_gui', '--onedir', '--contents-directory=internal',
    '--add-data=gui.py:.', '--add-data=strata.py:.', f'--add-data={streamlit_data_arg}',
    '--hidden-import=streamlit', '--hidden-import=plotly', '--hidden-import=pandas', '--hidden-import=sqlite3',
    '--hidden-import=streamlit.runtime.scriptrunner.magic_funcs',
    '--hidden-import=streamlit.runtime.scriptrunner.magic',
    '--hidden-import=streamlit.web.cli',
    '--copy-metadata=streamlit', '--clean', '--noconfirm', f'--distpath=dist',
]

if os.path.exists("app.ico"):
    print("[INFO] Adding app.ico to build...")
    gui_args.append('--icon=app.ico')

print("[INFO] Building GUI...")
PyInstaller.__main__.run(gui_args)

print("[INFO] Organizing Output...")
if os.path.exists(FINAL_DIR): shutil.rmtree(FINAL_DIR, ignore_errors=True)
time.sleep(1) 
shutil.move(DIST_DIR, FINAL_DIR)

print("[INFO] Building CLI...")
cli_args = ['strata.py', '--name=strata_cli', '--onefile', f'--distpath={FINAL_DIR}', '--clean', '--noconfirm']
if os.path.exists("app.ico"): cli_args.append('--icon=app.ico')
PyInstaller.__main__.run(cli_args)

if os.path.exists("strata.ini"): shutil.copy2("strata.ini", FINAL_DIR)
if os.path.exists("app.png"): shutil.copy2("app.png", FINAL_DIR)

print(f"[SUCCESS] Build Complete! Output directory: {FINAL_DIR}")

# Create Archives with FIXED NAMES
print("[INFO] Archiving...")

if sys.platform == "win32":
    # Windows -> ZIP -> strata-agent-windows.zip
    shutil.make_archive("dist/strata-agent-windows", 'zip', root_dir="dist", base_dir="strata")
    print(f"[SUCCESS] Windows Zip ready: dist/strata-agent-windows.zip")
else:
    # Linux/Mac logic handles by GitHub Actions mostly, but if run locally:
    plat_name = "macos" if sys.platform == "darwin" else "linux"
    with tarfile.open(f"dist/strata-agent-{plat_name}.tar.gz", "w:gz") as tar:
        tar.add(FINAL_DIR, arcname=os.path.basename(FINAL_DIR))
    print(f"[SUCCESS] Archive ready: dist/strata-agent-{plat_name}.tar.gz")