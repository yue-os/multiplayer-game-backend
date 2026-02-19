from flask import Flask
from app.server.database import init_db
from app.server.routes.user import user_bp
from app.server.routes.appRoutes import app_bp
import os
from dotenv import load_dotenv

load_dotenv(override=True)

def create_app():
    app = Flask(__name__)
    
    # Configuration
    app.config['SECRET_KEY'] = os.getenv('SECRET_KEY')
    
    # Initialize Database
    init_db(app)
    
    # Register Blueprints
    app.register_blueprint(user_bp)
    app.register_blueprint(app_bp)
    
    return app