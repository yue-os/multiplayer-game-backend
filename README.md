# Flask Supabase Game Backend

This is a production-ready, modular Flask backend designed for a multiplayer game. It integrates with **Supabase (PostgreSQL)** for persistence, uses **JWT** for secure authentication, and manages a **Game Server Registry** for Godot instances.

## 📂 Project Structure

```text
app/
├── auth/               # JWT authentication logic (Bearer token implementation)
├── server/
│   ├── models/         # SQLAlchemy Database Models (User, Mission, Logs)
│   ├── routes/         # API Endpoints (Auth, Server Registry, Gameplay)
│   ├── app.py          # App Factory Pattern
│   ├── database.py     # Database Connection & Initialization
│   └── seed.py         # Sample data generator
├── .env                # Environment configuration (Git ignored)
├── main.py             # Entry point for development
└── requirements.txt    # Python dependencies
```

## 🚀 Setup & Installation

### 1. Prerequisites
*   Python 3.8+
*   A Supabase Project (or any PostgreSQL database).

### 2. Environment Variables
Create a `.env` file in the root directory.

```ini
# Supabase Connection
# Format: postgresql://[user]:[password]@[host]:[port]/[database]
# Note: If using Supabase, use the "Transaction Pooler" port (usually 6543) or Session (5432).
SUPABASE_DB_URL=postgresql://postgres:yourpassword@db.yourref.supabase.co:5432/postgres

# Security
SECRET_KEY=dev_secret_key_change_in_prod
JWT_SECRET=jwt_secret_key_change_in_prod
JWT_ALGORITHM=HS256
```

### 3. Install Dependencies
```bash
pip install -r requirements.txt
```

### 4. Running the Server

**Development:**
```bash
python main.py
```
*The server will run at `http://0.0.0.0:5000`.*
*On the first run, it will automatically create tables and populate them with sample data.*

**Default Credentials:**
*   **Admin**: `admin` / `admin123`
*   **Teacher**: `Mr.Smith` / `teach123`
*   **Parent**: `ParentJane` / `parent123`
*   **Student**: `Timmy` / `timmy123`

**Production (Gunicorn):**
It is recommended to use Gunicorn for production deployments.
```bash
# Install Gunicorn
pip install gunicorn

# Run with 4 workers
gunicorn -w 4 -b 0.0.0.0:5000 main:app
```

---

## 📡 API Documentation

### 1. Authentication (`/auth`)

| Method | Endpoint | Description | Request Body |
| :--- | :--- | :--- | :--- |
| `POST` | `/auth/register` | Register a new user | `{"username": "user1", "email": "u@test.com", "password": "123", "role": "Student"}` |
| `POST` | `/auth/login` | Login & receive JWT | `{"username": "user1", "password": "123"}` |

*   **Roles**: `Student`, `Parent`, `Teacher`, `Admin`.
*   **Response**: Returns `{"access_token": "..."}`. Use this token in the `Authorization` header for protected routes.

### 2. Game Server Registry (`/server`)
*Used by Godot Server instances and Game Clients.*

| Method | Endpoint | Description | Payload / Response |
| :--- | :--- | :--- | :--- |
| `POST` | `/server/register` | Register/Heartbeat from Godot | **Request**: `{"port": 7777, "name": "Lobby 1", "count": 2}`<br>**Note**: IP is auto-detected. |
| `GET` | `/server/list` | Get active server list | **Response**: `[{"ip": "1.2.3.4", "port": 7777, "name": "Lobby 1", "count": 2}]` |

*Note: Servers are automatically removed from the list if no heartbeat is received for 15 seconds.*

### 3. Gameplay (`/mission`)
*Requires Header: `Authorization: Bearer <token>`*

| Method | Endpoint | Description | Request Body |
| :--- | :--- | :--- | :--- |
| `POST` | `/mission/update` | Save/Update mission progress | `{"mission_id": 1, "score": 150, "status": "completed"}` |

### 4. Parental Controls (`/parent`)
*Requires Header: `Authorization: Bearer <token>` (User must have `Parent` role)*

| Method | Endpoint | Description | Request Body / Response |
| :--- | :--- | :--- | :--- |
| `POST` | `/parent/link_child` | Link a student account | `{"child_username": "student1"}` |
| `GET` | `/parent/stats` | View linked children's stats | Returns a JSON list of children, including their recent playtime logs and mission scores. |

---

## 🗄️ Database Schema (Supabase)

The application uses SQLAlchemy. Tables are automatically created (`db.create_all()`) when the app starts if they do not exist.

*   `users`: Stores user credentials, roles, and relationships (Parent-Child, Teacher-Class).
*   `game_servers`: Ephemeral table for active game server instances (cleaned up via logic, not DB).
*   `missions`: Static game data (Title, Level Req).
*   `mission_progress`: Tracks Student scores and status per mission.
*   `playtime_logs`: Tracks daily playtime duration for students.

## 🤝 Team Notes

1.  **Supabase Connection**: The `database.py` script automatically fixes Supabase connection strings starting with `postgres://` to `postgresql://` for compatibility.
2.  **Timezones**: All timestamps (`created_at`, `updated_at`, `last_heartbeat`) are stored in UTC.
3.  **Deployment**: When deploying to a cloud provider (e.g., Render, Railway, AWS), ensure the `SUPABASE_DB_URL` environment variable is set.
