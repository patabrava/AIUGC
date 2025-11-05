#!/bin/bash
# FLOW-FORGE Setup Script

echo "Setting up FLOW-FORGE UGC System..."

# Create .env file
cat > .env << 'EOF'
# FLOW-FORGE Environment Variables
# Generated: 2025-11-05

# Supabase
SUPABASE_URL=https://dfdtjamyajlhbbpumukw.supabase.co
SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6ImRmZHRqYW15YWpsaGJicHVtdWt3Iiwicm9sZSI6ImFub24iLCJpYXQiOjE3NjIzMDczNTIsImV4cCI6MjA3Nzg4MzM1Mn0.yx3s5N3AZzkyV-o5PZd4blY6nAMWvWXUFK8tefbUdPw
SUPABASE_SERVICE_KEY=YOUR_SERVICE_KEY_HERE

# LLM Providers (use dummy keys for Phase 0-1 testing)
OPENAI_API_KEY=sk-dummy-openai-key-for-development
ANTHROPIC_API_KEY=sk-ant-dummy-anthropic-key-for-development

# Video Providers
VEO_API_KEY=your-veo-key
SORA_API_KEY=your-sora-key

# Social Media
TIKTOK_CLIENT_KEY=your-tiktok-key
TIKTOK_CLIENT_SECRET=your-tiktok-secret
INSTAGRAM_ACCESS_TOKEN=your-instagram-token

# Cron Security
CRON_SECRET=flow-forge-cron-secret-$(openssl rand -hex 16)

# Application
DEBUG=1
LOG_LEVEL=INFO
ENVIRONMENT=development
EOF

echo "✅ Created .env file"
echo ""
echo "⚠️  IMPORTANT: Edit .env and add your API keys:"
echo "   - SUPABASE_SERVICE_KEY (get from Supabase dashboard)"
echo "   - OPENAI_API_KEY"
echo "   - ANTHROPIC_API_KEY"
echo "   - Video provider keys (optional for Phase 0-3)"
echo "   - Social media keys (optional for Phase 0-6)"
echo ""
echo "Setup complete!"
