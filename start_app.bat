@echo off
rem Start the CRM, reachable by cofounder over Tailscale
cd /d C:\Users\harJD\Downloads\AgenticAILeads\app
streamlit run app.py --server.address 0.0.0.0 --server.port 8501 --server.headless true
