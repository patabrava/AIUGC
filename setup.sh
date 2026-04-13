#!/bin/bash
# FLOW-FORGE Setup Script

echo "Setting up FLOW-FORGE UGC System..."

# Create .env file
cat > .env << 'EOF'
# FLOW-FORGE Environment Variables
# Generated: 2025-11-05

# Supabase
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-supabase-key
SUPABASE_SERVICE_KEY=your-service-role-key

# LLM Providers
OPENAI_API_KEY=your-openai-api-key
ANTHROPIC_API_KEY=your-anthropic-api-key

# Video Providers
GEMINI_API_KEY=your-gemini-api-key
SORA_API_KEY=your-sora-api-key

# Social Media
TIKTOK_CLIENT_KEY=your-tiktok-client-key
TIKTOK_CLIENT_SECRET=your-tiktok-client-secret
INSTAGRAM_ACCESS_TOKEN=your-instagram-access-token

# Cron Security
CRON_SECRET=your-random-secret-string

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
echo "   - GEMINI_API_KEY"
echo "   - SORA_API_KEY"
echo "   - Social media keys (optional for Phase 0-6)"
echo ""
echo "Setup complete!"
