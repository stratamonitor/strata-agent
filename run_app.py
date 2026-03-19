import sys
import os
import streamlit.web.cli as stcli

def resolve_path(path):
    if hasattr(sys, '_MEIPASS'): return os.path.join(sys._MEIPASS, path)
    return os.path.join(os.getcwd(), path)

if __name__ == "__main__":
    app_path = resolve_path("gui.py")
    # Bind to 0.0.0.0 to allow LAN access
    sys.argv = [
        "streamlit", "run", app_path, 
        "--global.developmentMode=false", 
        "--server.headless=true",
        "--server.address=0.0.0.0" 
    ]
    sys.exit(stcli.main())