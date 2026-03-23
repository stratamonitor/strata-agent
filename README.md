# Strata Agent 🗄️🤖

[![Build Status](https://github.com/georgiykirillov/strata-agent/actions/workflows/build.yml/badge.svg)](https://github.com/georgiykirillov/strata-agent/actions)
[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](https://opensource.org/licenses/MIT)
[![Platform](https://img.shields.io/badge/platform-Linux%20%7C%20Windows-lightgrey)](#)

**The blazing-fast, AI-powered storage analyzer for modern sysadmins.**

<div style="background-color: #A8DCAB; padding: 10px; border-radius: 5px;">
🔒 <b>100% Read-Only by Design:</b>  Strata strictly analyzes metadata. It contains absolutely zero code to modify, move, or delete your files. Your data is perfectly safe from accidental deletion or AI hallucinations.
</div>

Strata is a portable, zero-dependency tool that helps you instantly find what's eating your disk space. It scans millions of files in seconds, stores history in a local SQLite database, and visualizes usage through beautiful, interactive Sunburst and Treemap charts.

But the real magic? **You can chat with your storage.** Connect Strata to the cloud and use Natural Language to query your file system using AI (Text-to-SQL).

![Strata Dashboard Screenshot](https://github.com/georgiykirillov/strata-agent/blob/main/img/strata_gui.jpg?text=strata_gui_screenshot)

## ✨ Features

*   ⚡ **Ultra-Fast Scanning:** Optimized `os.walk` implementation handles massive directories and NAS mounts with ease.
*   📊 **Interactive Dashboards:** Built-in Streamlit GUI with deep-zoomable Sunburst and Treemap visualizations.
*   🤖 **Autonomous AI Chat:** Ask questions like *"Show me the largest video files added last week"* and get instant answers (requires a free Strata Cloud API key).
*   🛡️ **Privacy First (Paranoid Mode):** Your file names and directory structures never leave your server. The local SQLite database stays on your machine.
*   📦 **Truly Portable:** Shipped as a single directory/executable for Linux and Windows. No Python installation required.
*   ⏱️ **Time Machine:** Compare current storage usage with past snapshots to detect anomalies and unexpected growth.

## 🚀 Quick Start

### 1. Download the Pre-compiled Binary
Head over to the [Releases](https://github.com/georgiykirillov/strata-agent/releases) page and download the latest archive for your OS (Linux `tar.gz` or Windows `zip`).

### 2. Run a Scan
Extract the archive and run the CLI tool to scan a directory (e.g., your root drive or a specific mount):

**Linux:**
```bash
sudo ./strata_cli --scan /mnt/data
```

**Windows:**
```powershell
strata_cli.exe --scan C:\Users
```

### 3. Open the Dashboard
Once the scan is complete, launch the GUI to visualize the data:
```bash
sudo ./strata_gui
```
*The dashboard will be available in your browser at http://localhost:8501.*

# ☁️ Strata Cloud & AI Integration
While the Strata Agent works perfectly completely offline, you can unlock its full potential (including the AI Assistant and Fleet Management) by connecting it to Strata Cloud.
1. Register for a free account at stratamonitor.com.
2. Copy your API Key from the cloud dashboard.
3. Open strata.ini (located next to your agent executable) and add your key:
```ini
[Server]
url = https://api.stratamonitor.com/api/v1/agent/sync
key = sk_your_api_key_here
```
4. Run ./strata_cli --check-tasks or click "Sync to Server" in the local GUI.

# ⏱️ Automation (Cron / Task Scheduler)
Strata is designed to run unattended. Add it to your server's crontab to keep your storage history up to date:
```bash
# Run a full scan every night at 2:00 AM
0 2 * * * /opt/strata/strata_cli --scan /mnt/data

# Sync metrics and check for AI tasks every 30 minutes
*/30 * * * * /opt/strata/strata_cli --check-tasks
```
# 🛠️ Building from Source
Don't trust pre-compiled binaries? We get it. Building Strata from source is incredibly easy.
1. Clone the repository:
```bash
git clone https://github.com/georgiykirillov/strata-agent.git
cd strata-agent
```
2. Install dependencies:
```bash
pip install -r requirements.txt
```
3. Build the executables using our build script:
```bash
python build.py
```
The compiled, ready-to-deploy product will be generated in the dist/strata folder.

# 📄 License
This project is licensed under the MIT License - see the [LICENSE](https://github.com/georgiykirillov/strata-agent/blob/main/LICENSE) file for details.