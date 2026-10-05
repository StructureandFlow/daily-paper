# The Daily Paper

GitHub builds a vintage front-page PDF of your chosen news sources every morning and emails it to you
(handy for reading on a reMarkable or any tablet). You change sources, sections, source priority, keywords,
send time and story count in `settings.html`. Nothing has to stay running on your computer.

---
## A. For the person sharing it (one time)
1. Create a new GitHub repo named `daily-paper` (public, so friends can copy it; it holds no secrets) and upload this
   whole folder, including the hidden `.github` folder. With git:
   `git init -b main && git add . && git commit -m "Daily Paper" && git remote add origin https://github.com/YOU/daily-paper.git && git push -u origin main`
2. On the repo page: Settings > General > tick **Template repository**.
3. Send friends the repo link and tell them to follow section B.

## B. Setup for each person (about 15 minutes, nothing to upload)
1. Open the template link and click **Use this template > Create a new repository**. Name it, choose **Private**.
2. In your new repo: Settings > Secrets and variables > Actions > New repository secret. Add:
   - `SMTP_USER` — the Gmail address that sends your paper (can be your own)
   - `SMTP_PASS` — a Gmail App Password (Google Account > Security > 2-Step Verification > App passwords)
   Not on Gmail? You can change the mail server and port in `settings.json`.
3. Create an access token: GitHub > Settings > Developer settings > Fine-grained tokens. Choose **only your new repo**,
   with **Contents: read & write** and **Actions: read & write**.
4. Download `settings.html` from your repo (or from the zip) and double-click it. Enter `yourname/repo-name` and the token.
5. Add sources (paste any site URL), set your email address and send time, tick **Send automatically**, then click **Send now** to test.

## Notes
- Runs hourly; each day it sends on the first check after your chosen time. **Send now** sends immediately.
- Private repos get 2,000 free Actions minutes a month; this uses roughly 800.
- Each edition is kept for 7 days under the run's Artifacts on the repo's Actions tab.
- Every person's repo, token, secrets and settings are separate from everyone else's.
- Your token stays in your browser only. Each settings change is saved as a small commit to `settings.json`.
- Sources are fetched from their public pages. Paywalled sites show only a teaser. Personal use only; respect each publisher's terms.
- Local test: `pip install -r requirements.txt` then `python run.py build --demo`.
- Fonts in `fonts/` are open-license (SIL OFL).
