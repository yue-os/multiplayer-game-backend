# **System User Manual**

Name

**BatangAware Team**

Company

**BatangAware**

Department

**Product and Engineering**

Date

**September 11, 2026**

## **I. Introduction**

Welcome to the **BatangAware** System User Manual. This manual explains how to use the BatangAware multiplayer educational card game and its supporting web backend. The system combines a Godot 4 mobile game client with authenticated web services for accounts, classrooms, quizzes, parent monitoring, game-server discovery, persistent progress, and real-time multiplayer sessions.

The system is intended for students, parents, teachers, administrators, and technical operators. The game teaches practical health-awareness concepts through a social deduction and trading experience involving hidden roles, infection risk, location events, quizzes, and cooperative or competitive objectives.

### **1. About BatangAware**

BatangAware is an educational multiplayer game focused on health awareness, influenza prevention, decision-making, and collaboration. Players participate in timed rounds while managing health, inventory, location risk, missions, and interactions with other players.

The system consists of two connected products:

- **BatangAware Game Client**: A Godot 4 mobile-first application for joining or hosting rooms, playing rounds, trading cards, completing quizzes, using role abilities, and viewing results.
- **BatangAware Backend**: A Flask and FastAPI service that manages authentication, role-based access, PostgreSQL data, Redis sessions and game state, server discovery, parent and teacher dashboards, and real-time WebSocket gameplay.

### **2. Purpose of this Manual**

This manual enables clients and users to:

- Install and connect the game client to the backend.
- Create, verify, and use accounts.
- Join or host a multiplayer room.
- Understand the game interface, roles, locations, cards, health, and round flow.
- Monitor student progress as a parent or teacher.
- Manage users, classes, quizzes, password-reset requests, and announcements as an administrator.
- Operate, update, back up, and troubleshoot the backend services.

This manual describes the current implemented behavior. Deployment URLs, support contacts, production credentials, and hosting-specific procedures must be supplied by the client's system administrator.

## **II. Getting Started**

### **1. System Setup**

#### **A. Game client requirements**

- Android device or compatible desktop development environment.
- Network access to the deployed BatangAware backend.
- A registered BatangAware account.
- A parent link for Student accounts before the student can enter a game.
- Device audio enabled if music, sound effects, or announcements are required.

The game is configured for Godot 4.7 Mobile rendering and uses a responsive 1280 x 720 logical viewport that expands to the device display.

#### **B. Backend requirements for technical operators**

- Python 3.8 or later.
- Docker and Docker Compose for the recommended deployment.
- PostgreSQL 15 or a compatible PostgreSQL service.
- Redis 7 or a compatible Redis service.
- A domain or reachable host for production clients.
- SMTP credentials if email verification or password-reset email is enabled.

#### **C. Installation and startup**

For a local or server deployment:

1. Open the backend project directory.
2. Copy `.env.example` to `.env`.
3. Replace development secrets and database credentials with secure values.
4. Install Python dependencies with `pip install -r requirements.txt`, or build the Docker services.
5. Start the services with `docker-compose up -d`.
6. Verify the backend with `GET /health`.
7. Configure the game client's master server URL to point to the deployed backend.
8. Install and launch the Godot game client.

The Docker Compose deployment provides PostgreSQL, Redis, and the FastAPI/Flask backend. The default container mapping exposes the backend on port `8080` and the internal application on port `8000`.

For development without Docker, install dependencies and run:

```text
python main.py
```

The Flask development service listens on port `5000`. To run the real-time service directly:

```text
uvicorn fastapi_main:app --host 0.0.0.0 --port 8000 --reload
```

Do not use development secrets, debug mode, default Redis passwords, or the Flask development server for a production deployment.

#### **D. Account registration and login**

1. Select **Register** in the client or connected web interface.
2. Enter first name, last name, username, email address, password, and account role.
3. Submit the registration form.
4. Enter the one-time password (OTP) sent to the email address.
5. Return to the login screen and enter the username and password.
6. Store the returned session securely. The backend uses a JWT bearer token for protected requests.

Supported roles are **Student**, **Parent**, **Teacher**, and **Admin**. A Student account must be linked to a Parent account before the student can play.

To change a password, use the authenticated password-change function. To recover a password, request a reset by email; an administrator may need to approve the request before a reset link is issued.

### **2. Interface Overview**

#### **A. Game client navigation**

The game client provides the following main screens and controls:

- **Login and account screens**: Registration, OTP verification, login, and password actions.
- **Student choice or role entry**: The transition into the multiplayer lobby.
- **Lobby**: Host a room, join an available room, refresh the room list, or return to the previous screen.
- **Room list**: Displays room name, player count, availability, started status, and approximate connection quality.
- **Game screen**: Displays the current location, round number, timer, announcements, player list, role information, inventory, chat, trading, and event interactions.
- **Dashboard**: Shows the player's role, health or vulnerability status, inventory, missions, and player information.
- **Game completion screen**: Shows scores, winner information, highlights, and the option to return home.
- **Settings**: Controls master volume, music volume, sound-effects volume, and quitting the current session.

#### **B. Backend and role dashboards**

- **Student**: Plays the game, completes missions and quizzes, and records progress.
- **Parent**: Links children, reviews statistics, reads feedback, and communicates with connected teachers.
- **Teacher**: Manages classes, quizzes, student performance, game lobbies, announcements, and feedback.
- **Admin**: Manages users, classes, password-reset requests, and system-level records.

## **III. System Features**

### **1. Feature 1: Multiplayer Lobby and Real-Time Game**

The lobby system uses the backend server registry and WebSocket relay to help players find and enter multiplayer rooms.

#### **Host a room**

1. Log in with a valid account.
2. Open the lobby screen.
3. Select **Host**.
4. Wait for the real-time connection to be established.
5. Share the room information with the intended players.
6. Wait until the required participants have joined.
7. Select **Start Game**. Only the player who created the room can start it.

#### **Join a room**

1. Open the lobby screen.
2. Select **Join Server** or the public room-list action.
3. Review the available rooms and player counts.
4. Select **Refresh** if a room is not listed yet.
5. Select **Join Now** for an available room.
6. Wait for the game state to synchronize.
7. Follow the room creator's instructions and wait for the game to start.

A room may show as offline, started, open, or not yet started. A room that has already started may not accept additional players. If no room is available, create a room or ask the host to verify that the backend is reachable.

The real-time service sends game-state updates through a WebSocket connection. Redis stores and distributes shared lobby state so that connected clients receive consistent updates.

### **2. Feature 2: Game Rounds, Locations, Health, and Hidden Roles**

A game consists of timed rounds. The standard round duration is 60 seconds, and the game supports up to 10 rounds. At the beginning of each round, players review the location, announcements, personal health, missions, and available actions.

The main locations are:

- **School**: Quiz activities, school-supply trades, group tasks, and knowledge rewards. Medium infection risk.
- **Canteen**: Fast trading, snacks, and multi-trade chains. Very high infection risk.
- **Clinic**: Healing, medicine use, and Doctor bonuses. Low infection risk.
- **Market**: Buying, selling, and Vendor advantages. Medium infection risk.
- **Park**: Outdoor activities and lower-risk interactions. The exact event effect may vary by round.
- **Quarantine**: A restricted state entered through game events or voting outcomes.

The game uses the following player roles:

- **Student**: Completes school requirements, collects school supplies, and benefits from enhanced quiz rewards.
- **Doctor**: Maintains player health, can heal during trades, and receives a Clinic healing bonus.
- **Vendor**: Earns profit, collects consumables, and receives Market trade benefits.
- **Caretaker**: Reduces environmental risk, cleans locations, and supports safe trades.
- **Guard**: Controls risky behavior, can restrict trading in a location, and identifies vulnerable players.
- **Infected**: A hidden carrier who must complete a cover-role mission while managing infection spread and remaining undiscovered.

The infected status is hidden from other players. Patient Zero is assigned by the game service when the lobby has players and no carrier exists. A Doctor is not selected as Patient Zero by the real-time game service.

Health is presented in broad levels for gameplay decisions:

- **Healthy**: 80 to 100 health.
- **Weak**: 50 to 79 health.
- **Sick**: 20 to 49 health.
- **Critical**: 0 to 19 health.

Players should use health cards, visit the Clinic, make careful trades, and consider location risk before interacting.

### **3. Feature 3: Cards, Inventory, Trading, Quizzes, and Missions**

#### **Cards and inventory**

Players receive starting resources and use the Inventory panel to inspect available cards. Card groups include:

- **Health cards**: Mask, Sanitizer, Medicine, Vitamins, and Face Shield.
- **Consumables**: Snacks, Drinks, and Packed Meal.
- **Mission items**: Notebook, Pen, Book, Medical Kit, Cleaning Kit, and ID Badge.
- **Utilities**: Disinfectant Spray, Gloves, Trash Bag, Whistle, and Access Pass.
- **Economy items**: Coins, Trade Token, and Discount Card.
- **Special actions**: Safe Trade Pass, Quick Trade, and Isolation Pass.

Select an item to view its description. Confirm item use in the item-use panel. Some items reduce infection risk, restore health, support missions, or enable special actions.

#### **Trading**

1. Open the player list or select a nearby player.
2. Choose **Trade**.
3. Select items for your offer.
4. Review the partner's offer.
5. Confirm only when both sides agree.
6. Cancel if the offer or player target changes.
7. Wait for the trade result and updated inventory.

Trades can affect inventory, missions, health, infection exposure, and role objectives. Prefer safe trades in risky areas and avoid trading without checking the current round timer.

#### **Quizzes and location events**

School activities can present influenza or health-awareness quizzes. Special quiz rounds are configured for rounds 3, 6, and 9. A successful quiz can grant rewards, and Student role bonuses may increase quiz rewards.

Location events can change risk, health, rewards, or available actions. Read the announcement and event information before committing to a trade or item use.

#### **Missions and scoring**

Each role has different missions. Examples include collecting supplies, completing quizzes, healing players, earning coins, cleaning locations, restricting risky trades, or completing a cover-role objective. The game completion screen displays scores and highlights such as top trader, Patient Zero, and quiz performance when available.

Mission progress is also saved by the backend when a valid mission identifier and score are submitted.

### **4. Feature 4: Chat, Player Information, and Settings**

Use the chat panel to communicate during a session. Follow the client's conduct and privacy rules. Do not share passwords, OTPs, private health information, or access tokens in chat.

The player dashboard provides public player information and private information for the local player. Depending on the current state, the interface may show role status, health visibility, inventory, missions, protection status, and quarantine state.

The Settings panel allows the player to adjust master volume, music volume, and sound effects. Use **Quit** to leave the session through the supported game flow. Closing the application unexpectedly can cause the player to disconnect from the room.

### **5. Feature 5: Parent, Teacher, and Administrator Services**

#### **Parent functions**

1. Log in with a Parent account.
2. Link a child using the child's username.
3. Review linked-child statistics, mission progress, quiz results, and playtime.
4. Review feedback messages.
5. Send a message to an authorized teacher and identify the linked child and class.
6. Unlink a child only when the relationship is no longer required.

Parent data is restricted to the parent's linked children and authorized conversations.

#### **Teacher functions**

1. Log in with a Teacher account.
2. Create or select a class.
3. Review class-level mission, quiz, and student aggregates.
4. Open an individual student performance summary when more detail is needed.
5. Create quizzes with multiple-choice, true/false, or identification questions.
6. Save quizzes as drafts, publish them, set answer deadlines, configure retakes and grading, and close them when finished.
7. Review student feedback and communicate with linked parents.
8. Create or manage a teacher lobby when classroom play is required.

Published quizzes need valid questions. Multiple-choice questions require at least two options, and a publishing deadline must be in the future when one is specified.

#### **Administrator functions**

Administrators manage users, classes, role assignments, password-reset approvals, and system records. Admin actions should be limited to authorized staff and recorded according to the client's privacy and retention policy.

### **6. Feature 6: Admin and Teacher Dashboard Web Application**

The **Admin & Teacher Dashboard** is a React web application connected to the BatangAware backend. It provides browser-based management tools for administrators, teachers, and parents. Students use the game client rather than this dashboard.

#### **Dashboard setup**

For local development:

1. Open the `admin-teacher-dashboard` project directory.
2. Install the frontend packages with `npm install`.
3. Start the Vite development server with `npm run dev`.
4. Confirm that the dashboard API proxy points to the backend, normally `http://127.0.0.1:5000`.
5. If the backend is on another computer, set `VITE_API_PROXY_TARGET` to the backend's LAN URL before starting the dashboard.
6. For a built or remotely hosted dashboard, set `VITE_API_BASE_URL` to the deployed backend URL.
7. Ensure the backend CORS configuration includes the dashboard's origin.

The dashboard requires a reachable backend and a valid account. Only Admin and Teacher accounts can access the Admin and Teacher dashboard routes. Parent accounts can access the Parent dashboard route.

#### **Dashboard login and routes**

1. Open the dashboard login page.
2. Enter the account username and password.
3. After successful authentication, the application stores an expiring JWT session in the browser and redirects according to the account role.
4. Use `/admin` for the Admin Dashboard, `/teacher` for the Teacher Dashboard, and `/parent` for the Parent Dashboard.
5. Select the logout action when finished, especially on shared computers.

Protected routes redirect unauthorized users away from restricted pages. If the account is flagged to change its password, complete the password-change flow before continuing.

#### **Admin Dashboard functions**

Administrators can use the dashboard to:

- View platform analytics and summary information.
- Create, view, update, and delete user accounts.
- Review user roles, classes, and parent relationships.
- Review and process password-reset requests.
- Manage administrative records using protected API requests.

#### **Teacher Dashboard functions**

Teachers can use the dashboard to:

- View class overviews and student performance.
- Create and manage classes.
- Create quizzes with multiple-choice, true/false, or identification questions.
- Save quizzes as drafts, publish them, configure grading and retakes, and set answer deadlines.
- Review quiz submissions, mission progress, and individual student summaries.
- Manage classroom game-lobby workflows where enabled.

#### **Parent Dashboard functions**

Parents can use the dashboard to:

- View linked children and their learning or gameplay statistics.
- Review mission progress, quiz results, and playtime.
- Read feedback messages.
- Send authorized messages to connected teachers.

Dashboard users should refresh after changes to classes, users, quizzes, or parent-child relationships. Do not store passwords, JWTs, or private student information in screenshots or support tickets.

## **IV. Troubleshooting and Support**

### **1. Common Issues and Solutions**

| Issue | Recommended solution |
|---|---|
| Registration email or OTP does not arrive | Check the email address and spam folder. Confirm SMTP settings and wait for the email queue. Request a new registration only after the previous pending registration has expired or been cleared. |
| Login returns invalid credentials | Confirm the username and password. Use password recovery if necessary. Check whether the account was created successfully after OTP verification. |
| Student login is rejected because of parent linking | Ask the Parent account holder to link the Student account, then log in again. |
| No rooms are displayed | Select Refresh, verify the master server URL, check `/server/list`, and confirm the game server is sending heartbeats. |
| Room is offline or started | Select another available room, ask the host to create a new room, or wait for the current session to finish. |
| WebSocket connection fails | Verify the backend is reachable, the JWT is valid, the WebSocket URL uses `ws` or `wss` correctly, and the lobby ID is valid. Review backend logs. |
| Player is disconnected | Check network stability, return to the lobby, and rejoin. A server restart or expired session may also require logging in again. |
| Trading does not complete | Confirm both players are connected, selected items are available, the trade is not restricted by the location or Guard ability, and both offers are valid. |
| Game state or timer appears incorrect | Wait for the next synchronization update. Check Redis health and confirm that only one authoritative game service is handling the lobby. |
| Parent or teacher data is missing | Confirm the correct account role, child/class relationship, and JWT authorization. Refresh the dashboard after changes. |
| Dashboard does not start | Run `npm install`, confirm Node.js is installed, and run `npm run dev` from the `admin-teacher-dashboard` directory. |
| Dashboard cannot reach the backend | Check `VITE_API_PROXY_TARGET` or `VITE_API_BASE_URL`, confirm the backend is running, and verify the dashboard origin is allowed by CORS. |
| Dashboard route is unauthorized | Use an account with the required role: Admin for `/admin`, Teacher for `/teacher`, or Parent for `/parent`. Log out and sign in again if the browser session belongs to another role. |
| Dashboard shows an expired session | Log in again. The dashboard validates JWT expiry and clears an invalid browser session. |
| Backend returns HTTP 401 | The token is missing, expired, or invalid. Log in again and send `Authorization: Bearer <token>`. |
| Backend returns HTTP 403 | The authenticated account does not have permission for the requested role or resource. Contact an administrator if the access is expected. |
| Backend returns HTTP 500 | Review application logs, database connectivity, environment variables, and recent deployment changes. Do not expose stack traces to end users. |

Technical operators can use these checks:

```text
docker-compose ps
docker-compose logs backend
docker-compose logs db
docker-compose logs redis
docker exec game-redis redis-cli -a <password> ping
```

The expected Redis response is `PONG`. The expected backend health response is a JSON object containing a healthy or OK status.

### **2. Contacting Support**

The client administrator should replace the following placeholders before distributing this manual:

- **Technical support email**: `[SUPPORT EMAIL]`
- **Operations contact**: `[OPERATIONS CONTACT]`
- **Client representative**: `[CLIENT CONTACT]`
- **Service URL**: `[PRODUCTION BACKEND URL]`
- **Expected response time**: `[SUPPORT SLA]`

When submitting a support request, include the account role, approximate time of the issue, device or browser, room ID if applicable, visible error message, and steps that reproduce the problem. Never include passwords, OTPs, JWTs, SMTP credentials, database URLs, or Redis passwords.

### **3. Frequently Asked Questions (FAQs)**

**Can a Student play immediately after registration?**  
No. The account must complete email verification and be linked to a Parent account before login is allowed into the game.

**Who can start a hosted game?**  
The player who created the lobby can start it.

**How long is a round?**  
The standard real-time round duration is 60 seconds.

**How many rounds are in a game?**  
The configured game supports up to 10 rounds.

**Can players see who is Infected?**  
No. Infection is intended to be hidden. Players must use public information, health indicators, events, and behavior to make decisions.

**What happens when a room is empty?**  
The real-time service cleans up the in-memory lobby runtime when all connections leave. Persisted state may remain in Redis according to the cache policy.

**What database stores account and progress information?**  
PostgreSQL stores users, classes, missions, progress, quizzes, logs, and related records.

**What does Redis store?**  
Redis is used for sessions, pending registration data, notifications, lobby state, and real-time event distribution.

**Can a Parent message any Teacher?**  
No. Parent messages are restricted to teachers connected to the linked child's class or records.

**How are password-reset requests handled?**  
The user submits a request. An administrator reviews and approves or rejects it. Approved reset links expire and can be used once.

**Which application should administrators and teachers use?**  
Use the Admin & Teacher Dashboard web application. Administrators sign in at `/admin`, teachers at `/teacher`, and parents at `/parent`. Students use the BatangAware game client.

**Can the dashboard run on another computer?**  
Yes. Set `VITE_API_PROXY_TARGET` or `VITE_API_BASE_URL` to the backend machine's reachable LAN or production URL, allow the dashboard origin through CORS, and ensure the backend firewall permits the required port.

## **V. System Updates and Maintenance**

### **1. Updating the System**

Before an update:

1. Announce a maintenance window to affected users.
2. Confirm the current backend version and game client version.
3. Back up PostgreSQL data and verify the backup can be restored.
4. Record the current environment configuration without exposing secrets.
5. Stop or drain active game sessions when possible.
6. Pull the approved source release or container image.
7. Rebuild and restart services with `docker-compose up --build -d`.
8. Run health checks and a login test.
9. Test room listing, lobby connection, a trade, a quiz or mission update, and dashboard access.
10. Publish release notes and reopen the service.

For the game client, distribute the approved Android or desktop build through the client's chosen distribution channel. Verify that its configured master server URL matches the deployed backend and that the client version is compatible with the backend protocol.

### **2. Maintenance Procedures**

#### **Daily or per-session checks**

- Check backend, PostgreSQL, and Redis health.
- Review error logs and failed WebSocket connections.
- Confirm no unexpected room or user activity is present.
- Confirm disk space and container status.

#### **Weekly checks**

- Review PostgreSQL backups and restore evidence.
- Review Redis persistence and memory usage.
- Remove obsolete test accounts and stale development data according to policy.
- Review failed email delivery and pending registrations.
- Review admin activity and password-reset requests.

#### **Release and security checks**

- Change all default secrets before production.
- Use HTTPS and secure WebSockets (`wss`) in production.
- Restrict CORS to approved client origins.
- Keep JWT, Flask, SMTP, PostgreSQL, Redis, and webhook credentials out of source control.
- Use strong Redis authentication and enable persistence as required.
- Limit administrative access and use separate accounts for each administrator.
- Apply operating-system, Python, Node, Docker, PostgreSQL, Redis, and Godot updates through a tested release process.
- Avoid `FLUSHDB` or deleting Docker volumes unless a verified backup exists; `docker-compose down -v` deletes persisted database and Redis volumes.

### **3. Release Notes**

The following baseline capabilities are included in the current system documentation:

- Godot 4 mobile-first game client with responsive game UI.
- Multiplayer lobby hosting and room discovery.
- JWT authentication and role-based access.
- Email OTP registration and administrator-reviewed password recovery.
- PostgreSQL persistence for users, classes, missions, quizzes, progress, playtime, and messages.
- Redis session and lobby-state caching.
- FastAPI WebSocket real-time game state, round timers, location events, and trade processing.
- Parent child-linking and statistics views.
- Teacher class, quiz, student-performance, lobby, and feedback workflows.
- Administrator user and password-reset management.

Future releases should record the version, release date, new features, bug fixes, database or environment changes, client compatibility notes, and rollback instructions.

## **VI. Best Practices and Tips**

### **1. Data Management**

- Use real email addresses only when the client has approved the data-handling process.
- Keep student, parent, teacher, and administrator records accurate and current.
- Remove test accounts and test messages before production use.
- Back up PostgreSQL before migrations or bulk user changes.
- Treat playtime, health, quiz, mission, and parent-child relationship data as protected information.
- Do not place secrets or access tokens in screenshots, chat messages, issue reports, or source control.

### **2. Workflow Optimization**

- Teachers should create and test quizzes as drafts before publishing them to a class.
- Schedule quiz deadlines outside expected maintenance windows.
- Hosts should create rooms before the class session and confirm that players can see the room.
- Players should review their missions and inventory at the start of every round.
- Use the Clinic and protective items when health is low or a location is high risk.
- Use safe trades, observe the timer, and communicate clearly before confirming an offer.
- Keep the client and backend versions aligned after every release.

### **3. Collaboration**

- Parents should communicate with teachers through authorized account relationships and include the correct student and class.
- Teachers should use class-level data for routine monitoring and individual records only when detailed support is needed.
- Administrators should use least-privilege access and avoid sharing administrator credentials.
- Game participants should respect privacy, avoid revealing private account information, and follow the client's conduct policy.
- Technical operators should record deployment changes, incidents, backups, and restore tests.

## **VII. Additional Resources**

### **1. Online Help Center**

The project repository contains supporting technical references:

- Backend setup and API overview: `multiplayer-game-backend/README.md`
- Redis, PostgreSQL, and Docker operations: `multiplayer-game-backend/REDIS_SETUP.md`
- Admin and Teacher Dashboard setup: `admin-teacher-dashboard/README.md`
- Backend API reference: `GET /docs` and `GET /openapi.json` where enabled by the deployment
- Game project context and design conventions: `multiplayer-card-game/GEMINI.md`

The client should provide a production help-center URL before distributing this manual.

### **2. Training Workshops**

Recommended onboarding sessions are:

- **Player orientation**: Account setup, lobby joining, round timer, health, inventory, trading, and game completion.
- **Parent orientation**: Linking children, reviewing progress, reading feedback, and contacting teachers.
- **Teacher orientation**: Class management, quiz creation, student monitoring, and classroom lobby operation.
- **Administrator orientation**: User management, password-reset approval, privacy, backups, and incident response.
- **Technical operator orientation**: Docker services, environment configuration, database and Redis checks, logs, backups, and rollback.

### **3. Community Forums**

The client may provide a private support channel, issue tracker, or community forum at:

**[CLIENT COMMUNITY OR SUPPORT PORTAL]**

Use the support channel for gameplay questions and the technical issue tracker for reproducible defects, deployment incidents, and release feedback.

## **VIII. Conclusion**

Congratulations on completing the **BatangAware** System User Manual. The system brings together an educational multiplayer game and a role-based backend that supports safe account management, classroom use, health-awareness learning, real-time play, and progress monitoring.

Players should protect their accounts, read each round's information, manage health and risk carefully, and communicate responsibly. Parents and teachers should use the reporting features to support learning without exposing unnecessary personal information. Administrators and operators should maintain secure credentials, reliable backups, controlled access, and tested release procedures.

For assistance, contact the client's designated support team using the contact information supplied in Section IV.
