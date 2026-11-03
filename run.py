from app import create_app

app = create_app()

if __name__ == "__main__":
    # localhost only: this is a development server for testing, not a production deployment
    app.run(host="127.0.0.1", port=5055, debug=False, threaded=True)
