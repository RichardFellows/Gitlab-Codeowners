#!/bin/sh

# Set script to exit immediately if a command exits with a non-zero status
set -e

echo ""
echo "======================================================"
echo "🚀 GitLab CODEOWNERS Tool is starting up..."
echo "   You can access the web UI by clicking the link below."
echo ""
echo "   >> http://localhost:5001 <<"
echo ""
echo "======================================================"
echo ""

exec gunicorn --bind 0.0.0.0:5001 --reload --access-logfile - --error-logfile - app:app