# Montage Studio X Publisher

Install this folder in the Chrome or Edge profile where you sign into X. No X developer account or API credits are used.

1. Open `chrome://extensions` (Chrome) or `edge://extensions` (Edge).
2. Enable **Developer mode**, click **Load unpacked**, and choose this `x-extension` folder containing `manifest.json`.
3. Open Montage Studio at http://127.0.0.1:8765/. Settings → Posting method → **Extension — your logged-in browser**.
4. Click **Generate extension pairing code**, then **Copy pairing code**. Paste into the extension popup and click **Pair with local app**.
5. Click **Verify X account** in the popup. It opens a dedicated X tab. If signed out, sign in normally yourself and verify again. The expected app account must match.
6. Enable **Process queued posts and schedules**. Keep the local app running and the browser open while uploading.
7. In Montage Studio, select a montage and use **Upload through extension for review**, **Post to X**, or **Schedule**. Queues are processed one operation at a time, checked every 30 seconds. The popup’s Check queue now checks immediately when processing is enabled.

Upload for review fills caption, attaches the MP4, waits for X’s Ready status, and applies Made with AI if selected; it does not click Post. Processing pauses after a review upload. You can then enable processing again and queue Post or Schedule from the app. An existing foreign draft is never overwritten. If you edit a prepared caption inside X, save/discard that draft yourself before continuing.

Post clicks X’s final Post button. Schedule clicks X’s native scheduling controls and verifies the saved date/caption in the Scheduled list. Once verified, X owns the schedule and the app can close. The extension checks for an existing identical date/caption before scheduling and refuses duplicates or an incomplete preflight check. It verifies presence in the Scheduled list after submitting.

The extension reads and changes X’s visible composer and profile link, and accesses only the paired local app’s queued video exports. Permissions are X, loopback localhost, storage and alarms. It has no cookie, password, debugger, broad-site, or private X API permissions. Pairing code and job state live in browser extension storage; generating a new pairing code in the app revokes the old one. Keep the code private.

Login challenges, rate/security warnings, wrong account, upload rejection, draft conflicts, or changed controls stop the queue. Resolve the warning yourself. No stealth flags or challenge bypasses are used. After the final click, ambiguous outcomes become **uncertain** and never republish automatically. Check X before taking another action. The extension stops after browser restart; the local app pauses pending jobs after app restart. Stop queue processing prevents new operations; an upload already started may finish.

Current scope: English X labels; MP4 up to 512 MB; selectors and readiness indicators may need changes when X updates. Popup and fixture tests passed, but a live X upload/post/schedule is unverified until you install this extension and test it. Existing X schedules and local exports are preserved. No automated batch cadence is inferred: queue each desired post/date explicitly in the app.

To update, replace the files, click the extension’s **Reload** control on the extensions page, and refresh X tabs. To uninstall, remove it from the extensions page, then generate a new app pairing code to revoke access.

Developer checks: `npm ci --ignore-scripts`, `npm test`. Node modules and tests are not needed to load the extension.
