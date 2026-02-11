#!/bin/bash

echo "🚀 Production Deployment Script"
echo "==============================="

# Install dependencies
echo "1. Installing Python dependencies..."
pip install -r requirements.txt

# Install Playwright
echo "2. Installing Playwright browsers..."
playwright install chromium

# Install Tesseract
echo "3. Checking Tesseract installation..."
if ! command -v tesseract &> /dev/null; then
    echo "Tesseract not found. Installing..."
    # Ubuntu/Debian
    sudo apt-get update
    sudo apt-get install -y tesseract-ocr

    # macOS
    # brew install tesseract
fi

# Create directories
echo "4. Creating directories..."
mkdir -p screenshots
mkdir -p logs
mkdir -p models

# Create prefill data if missing
echo "5. Checking prefill data..."
if [ ! -f "prefill_data.json" ]; then
    cat > prefill_data.json << EOF
{
  "name": "John Doe",
  "email": "john@example.com",
  "phone": "+1234567890",
  "company": "Example Corp",
  "subject": "Business Inquiry",
  "message": "Hello, I would like to know more about your services."
}
EOF
    echo "Created prefill_data.json with example data"
fi

# Create Domains.csv if missing
echo "6. Checking Domains.csv..."
if [ ! -f "Domains.csv" ]; then
    cat > Domains.csv << EOF
Website URL
https://example.com/contact
https://httpbin.org/forms/post
EOF
    echo "Created Domains.csv with example URLs"
fi

# Set permissions
chmod +x run.py

echo ""
echo "✅ Deployment complete!"
echo ""
echo "To run:"
echo "  python main.py                 # Batch mode"
echo "  python main.py https://example.com  # Single URL"
echo ""
echo "Check config.yaml for settings adjustment."
