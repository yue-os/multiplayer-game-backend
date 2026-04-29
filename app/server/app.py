from flask import Flask
from flask_cors import CORS
from app.server.database import init_db
from app.server.routes.user import user_bp
from app.server.routes.appRoutes import app_bp
from app.server.routes.teacher import teacher_bp
from app.server.routes.docs import docs_bp
from app.server.routes.admin_users_flask import admin_users_bp
import os
from dotenv import load_dotenv

load_dotenv(override=True)

def create_app():
    app = Flask(__name__)
    
    # Configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    
    # CORS Configuration for LAN and local dev
    # Allow specified origins and common request headers (includes Content-Type)
    CORS(
        app,
        resources={
            r"/*": {
                "origins": [
                    "http://localhost:5173",
                    "http://127.0.0.1:5173",
                    "http://localhost:3000",
                    "http://127.0.0.1:3000",
                    "http://192.168.1.7:5173",
                    "http://192.168.1.7:3000",
                ],
                "origin_regex": r"http://192\.168\.\d+\.\d+:\d+",
            }
        },
        supports_credentials=True,
        allow_headers=["Content-Type", "Authorization", "X-Requested-With", "Accept", "Origin"],
        methods=["GET", "HEAD", "POST", "OPTIONS", "PUT", "PATCH", "DELETE"],
    )
    
    # Initialize Database
    init_db(app)
    
    # Register Blueprints
    app.register_blueprint(user_bp)
    app.register_blueprint(app_bp)
    app.register_blueprint(teacher_bp)
    app.register_blueprint(docs_bp)
    app.register_blueprint(admin_users_bp)
    
    return app