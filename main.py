from app.server.app import create_app

app = create_app()

if __name__ == '__main__':
    # In production, use Gunicorn or similar. 
    # For dev, Flask server is fine.
    app.run(host='0.0.0.0', port=5000, debug=True)