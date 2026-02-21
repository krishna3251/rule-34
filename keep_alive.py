from flask import Flask
from threading import Thread
import os

app = Flask(__name__)

@app.route('/')
def home():
    return "✅ Bot is alive!"

@app.route('/health')
def health():
    return {
        "status": "healthy",
        "message": "Discord bot is running",
        "timestamp": __import__('datetime').datetime.now().isoformat()
    }

def run():
    port = int(os.environ.get("PORT", 8080))
    print(f"Running Flask app on port {port}")
    app.run(host='0.0.0.0', port=port)

def keep_alive():
    t = Thread(target=run)
    t.start()

# Optional: Automatically run the server when file is executed directly
if __name__ == "__main__":
    run()