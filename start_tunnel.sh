
#!/usr/bin/env bash
# Script to start a public HTTPS Cloudflare tunnel pointing to port 8000

echo "🚀 Starting Cloudflare Tunnel for http://localhost:8000 ..."
cloudflared tunnel --url http://localhost:8000
