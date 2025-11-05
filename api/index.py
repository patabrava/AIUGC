"""
FLOW-FORGE Vercel Adapter
Exports FastAPI app for Vercel serverless deployment.
"""

from app.main import app

# Vercel expects an 'app' or 'handler' export
handler = app
