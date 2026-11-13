# Host the Booking RPA dashboard on Streamlit

`app.py` is the Streamlit front-end. It reuses `dashboard.py` (task list, checks, run/stop, Excel batch)
and runs your existing `step*.py` scripts unchanged.

## Run locally
    pip install -r requirements.txt
    python -m playwright install chromium
    copy .streamlit\secrets.toml.example .streamlit\secrets.toml   (then edit it)
    streamlit run app.py

## Host on Streamlit Community Cloud
1. Push this folder to GitHub (private repo). `.env`, `auth_state.json`, `screenshots/` and
   `.streamlit/secrets.toml` are in `.gitignore`, so they are not uploaded.
2. share.streamlit.io -> **Create app** -> pick the repo, branch `main`, main file `app.py`.
3. **Advanced settings -> Secrets**: paste the four lines from `.streamlit/secrets.toml.example` with real values.
4. Deploy. First start installs Chromium (about a minute).
5. App **Share** settings: restrict viewers to your own email addresses.

## Notes
- Hosted runs are always headless. Do not install `keyring` on the server (login comes from the secrets).
- The disk is wiped on restart, so each run logs in fresh (`auth_state.json` is not kept).
- One task at a time. Cancel runs are dry-run by default and need you to type CANCEL for a real run.
- If Infor Nexus blocks logins from the cloud server's IP, or asks for an Access Code, host it on your own PC/server instead.
