# Deployment Instructions

Follow these steps to run the modernized Digital Photo Frame application.

## 1. Prerequisites

Ensure you have the following installed:

- React Frontend: `Node.js` (v18+)
- Backend: `Python 3` (3.9+)
- Dependencies: `pip` and `npm`

## 2. Setting up the Backend

The backend uses a local SQLite database along with modular Flask routes and Pillow for image processing.

1. Navigate to the project root directory:

   ```bash
   cd /path/to/DigitalPhotoFrame
   ```

2. Install python dependencies:

   ```bash
   pip install -r backend/requirements.txt
   ```

   _Note: If backend/requirements.txt is not present/updated, ensure you have: `flask flask-cors werkzeug numpy opencv-python pillow pillow-heif requests psutil`_

3. (Optional) Run the database migrations if they didn't run automatically:
   The application now uses `backend/WebAPI/database.py` which will initialize `database.db` upon the first run and migrate your existing `users.json` and `metadata.json`.

## 3. Setting up the Frontend

The frontend has been entirely rewritten using React and Vite for a touch-friendly, dynamic, and beautiful UI.

1. Navigate to the frontend directory:

   ```bash
   cd frontend
   ```

2. Install npm dependencies:

   ```bash
   npm install
   ```

3. Build the production application. This outputs to `frontend/dist`. Flask is configured to serve these files automatically.
   ```bash
   npm run build
   ```

## 4. Running the Application

1. Start the main application from the project root:

   ```bash
   python backend/app.py --headless
   ```

   _(Use `--headless` if you don't need the local pygame display, which will only run the Flask server and FrameServer compositor)._

2. The application will be available at:
   - **Frontend UI**: `http://localhost:5001`
   - **Video Stream**: `http://localhost:5001/api/stream`

- **Contrast Text**: In the new Settings UI, you can toggle `Contrast Text` to dynamically inverse overlay colors.
- **Gallery Management**: Manage your photos via the Gallery. HEIC images are auto-converted.
- **Password Visibility**: Auth forms now include an "eye" icon to toggle password visibility.
- **Password Reset**: If you forget your password, use the "Forgot Password?" link on the login page to reset it instantly without a code.
- **Hot-Reloading**: Settings changes take effect immediately in the frame stream without a restart.

---

> [!IMPORTANT]
> Since the frontend is React-based, you MUST run `npm run build` inside the `frontend` folder for any UI changes or new pages (like Reset Password) to be visible in the browser.
