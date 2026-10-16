# SpinMate mail relay

A Google Apps Script web app that sends the welcome email for every SpinMate install, so nobody has to set up
mail settings. The app sends only the name and the email address (over HTTPS); the relay sends the email from
the account that deployed it, with the logo, the user guide and the presentation as PDFs. The text, the logo
and the PDFs are read from `web/` on `main`, so the email always matches the repository.

Limits: one email per address per day and 80 a day in total (consumer Gmail allows about 100 recipients a day).

## Deploy (once, about 5 minutes)

1. Sign in with the account that should send the email (for SpinMate: spinmate.ai.agent@gmail.com) and open
   https://script.google.com, then **New project**. Name it *SpinMate mail relay*.
2. Replace the contents of `Code.gs` with [`Code.gs`](Code.gs) from this folder and save.
3. Choose the function **testSend** and press **Run**. Allow the permissions (on "Google hasn't verified this
   app": *Advanced* > *Go to SpinMate mail relay*). You get the welcome email in that account's inbox.
4. **Deploy > New deployment > Web app**: *Execute as* **Me**, *Who has access* **Anyone** > **Deploy**.
   Copy the web app URL (it ends in `/exec`).
5. Put the URL in `RELAY_DEFAULT` in `app/mailer.py` (or `MAIL_RELAY=<url>` in a local `.env` to try it first).

Check it:

```bash
curl -sL "<web app URL>"                                  # {"ok":true,"service":"spinmate-mail-relay",...}
curl -sL -X POST "<web app URL>" -H 'Content-Type: application/json' -d '{"name":"Test","email":"you@example.com"}'
```

After changing `Code.gs`, use **Deploy > Manage deployments > Edit > New version**, so the URL stays the same.
