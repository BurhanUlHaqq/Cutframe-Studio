# Publish from local Cutframe

Open [Settings → Publishing](http://localhost:5031/settings#publishing-settings-title) to save developer app credentials and copy redirect URIs. Open [Publish Studio](http://localhost:5031/publish), select **Accounts**, and use **Connect account** to sign in with the platform. Settings take effect immediately. Cutframe encrypts app secrets and connected-account tokens in PostgreSQL.

| Platform | Recommended connection | Publishing behavior |
| --- | --- | --- |
| YouTube | Google OAuth **Web application** client with the localhost redirect | Uploads directly. Private is the default. |
| TikTok | Login Kit for Web and an approved Content Posting API app with a stable public HTTPS redirect | Sends a draft to the TikTok inbox. Finish posting in TikTok. |
| Instagram Reels | Facebook Login for Business with a stable public HTTPS redirect and a linked professional Instagram account | Publishes Reels directly to the selected account. |

## YouTube on localhost

1. Open [Google Cloud Console](https://console.cloud.google.com/), choose a project, and enable **YouTube Data API v3**.
2. In **Google Auth Platform**, configure **Branding** and **Audience**. If the app is **External** and in **Testing**, add the Google account you will connect as a test user.
3. Open **Clients → Create client → Web application**. Under **Authorized redirect URIs**, add `http://localhost:5031/api/publishing/callback/youtube/` exactly as shown in Cutframe. Use the same hostname in the Cutframe browser.
4. Save the **YouTube OAuth client ID** and **YouTube OAuth client secret** in **Settings → Publishing**. In **Publish → Accounts**, connect the Google account and select the YouTube channel.
5. For your first transfer, set visibility to **Private** and confirm the upload in YouTube Studio.

Google allows HTTP redirects on localhost. External apps in Testing generally issue seven-day refresh tokens for these scopes. YouTube also restricts uploads from certain unaudited projects to Private. Reconnect after a test token expires and follow Google's verification and audit instructions if you need a long-running integration. [Google OAuth setup](https://developers.google.com/identity/protocols/oauth2/web-server) · [Token expiration](https://developers.google.com/identity/protocols/oauth2#expiration) · [YouTube upload API](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert)

## Stable HTTPS callbacks for TikTok and Instagram

For a local Windows installation, use a **named Cloudflare Tunnel** with a dedicated subdomain on a domain you control. Route just the two callbacks to Django on `127.0.0.1:5032`. Keep opening Cutframe on `http://localhost:5031`. The browser brings authorization back to that address and the worker uploads videos through outbound requests. The tunnel can be stopped after connecting and started again when you reconnect. [Cloudflare named tunnel guide](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/) · [Path-based ingress configuration](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)

1. Add the domain to Cloudflare and install `cloudflared` on the Windows host. Open PowerShell and create the named tunnel. Use your real subdomain in place of the example.

   ```powershell
   cloudflared tunnel login
   cloudflared tunnel create cutframe-oauth
   cloudflared tunnel route dns cutframe-oauth cutframe-auth.example.com
   ```

2. Save the following as `cutframe-oauth.yml` alongside the tunnel credentials, outside the project repository. Replace the tunnel UUID, credentials path, and hostname.

   ```yaml
   tunnel: YOUR-TUNNEL-UUID
   credentials-file: 'C:\Users\YOUR-USER\.cloudflared\YOUR-TUNNEL-UUID.json'
   ingress:
     - hostname: cutframe-auth.example.com
       path: '^/api/publishing/callback/(tiktok|instagram)/$'
       service: http://127.0.0.1:5032
       originRequest:
         httpHostHeader: localhost
     - service: http_status:404
   ```

3. Validate and run the tunnel from the folder containing the YAML file.

   ```powershell
   cloudflared tunnel --config .\cutframe-oauth.yml ingress validate
   cloudflared tunnel --config .\cutframe-oauth.yml ingress rule https://cutframe-auth.example.com/api/publishing/callback/tiktok/
   cloudflared tunnel --config .\cutframe-oauth.yml run cutframe-oauth
   ```

4. In a second window, verify both the callback and the restricted route before adding any platform credentials.

   ```powershell
   curl.exe -sS -D - -o NUL https://cutframe-auth.example.com/api/publishing/callback/tiktok/
   curl.exe -sS -D - -o NUL https://cutframe-auth.example.com/api/publishing/accounts/
   ```

   The callback should return `302` with a `Location` beginning `http://localhost:5031/publish#connection=expired`. That is the expected response when no sign-in was started. The second URL should return `404`. Use the same route check for `/api/publishing/callback/instagram/`. Keep Cloudflare Access login screens and browser challenges off the callback paths so the OAuth redirect can reach Cutframe.

5. Save `https://cutframe-auth.example.com` as the **Public HTTPS callback origin** in **Settings → Publishing**. Copy the full redirect URI for each platform. Keep the trailing slash when registering it.

A stable hostname keeps the registered redirect valid across restarts. Quick tunnel addresses change. If you already have a public HTTPS reverse proxy, apply the same path-only routing and pass `Host: localhost` to Django. If you only need YouTube, the localhost workflow is sufficient. [Cloudflare origin Host header](https://developers.cloudflare.com/tunnel/reference/origin-parameters/#httphostheader) · [Cloudflare Quick Tunnel behavior](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)

### TikTok

1. In [TikTok for Developers](https://developers.tiktok.com/), add **Login Kit** with Web enabled and **Content Posting API**. Request `user.info.basic` and `video.upload`.
2. Register the HTTPS redirect from the TikTok card in Login Kit. It ends in `/api/publishing/callback/tiktok/`. Save the matching **TikTok client key** and **TikTok client secret** in Cutframe.
3. In **Publish → Accounts**, connect your creator account and grant the requested scopes. When a video reaches **Ready in TikTok**, open the notification to review the draft, set visibility, add the caption, and publish. Cutframe can copy the caption and check the draft status.

TikTok's sandbox supports designated target users and limits public posting. Its app review rules exclude private or personal-only apps. If your installation has no approved TikTok developer app, use **Post manually** in Publish Studio. [TikTok Login Kit for Web](https://developers.tiktok.com/docs/en/login-kit-web) · [Inbox upload](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content) · [Sandbox](https://developers.tiktok.com/docs/en/add-a-sandbox) · [App review rules](https://developers.tiktok.com/docs/en/app-review-guidelines)

### Instagram Reels

1. Link an Instagram **Business** or **Creator** account to a Facebook Page you manage. Confirm the Facebook user can publish to that Page.
2. In [Meta for Developers](https://developers.facebook.com/apps/), set up **Instagram API with Facebook Login** and **Facebook Login for Business**. Configure `pages_show_list`, `pages_read_engagement`, `instagram_basic`, and `instagram_content_publish`. For roles assigned through Business Manager, check the additional permissions in Meta's publishing guide.
3. Add the Instagram callback under **Valid OAuth Redirect URIs**. It ends in `/api/publishing/callback/instagram/`. If you create a Login for Business configuration, select **User access token** and include your publishing permissions and Page assets. Save the configuration ID in Cutframe.
4. Save your **Meta app ID** and **Meta app secret** in **Settings → Publishing**. Connect the Facebook account in **Publish → Accounts** and select the linked Instagram profile when preparing a reel. In development mode, use eligible app roles. Access for other users may require Meta approval.

Cutframe transfers the local MP4 directly using Meta's resumable upload endpoint. Start with an account where you are ready for a live Reel. [Instagram API with Facebook Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/) · [Content publishing](https://developers.facebook.com/documentation/instagram-platform/content-publishing)

## Troubleshoot connection and publishing

| Symptom | What to check |
| --- | --- |
| Redirect URI mismatch | Copy the full URI again from **Settings → Publishing**. Match the scheme, hostname, port, path, and trailing slash. Use the correct developer app and its matching secret. |
| The public callback returns 404 or 502 | Check the public hostname route, exact regex path, running tunnel, and Django on port 5032. Use the callback test above. |
| The callback opens Cutframe and reports an expired connection | Restart **Connect account** from the same browser and exact local address. Finish the sign-in within ten minutes. |
| The returned platform account is missing | Confirm you selected the intended YouTube channel or the Facebook user that manages the linked Page. Verify developer app roles and granted permissions. |
| The platform rejects the upload | Check privacy or audit requirements, the account's media limits, and the message in Cutframe's **Queue**. If the queue says **Check platform**, check the account before approving another upload. |

To schedule a transfer, set **Send time** and leave the Cutframe Docker stack running at that time. For a manual upload, use **Post manually** to download the MP4, copy the prepared text, and open the platform's official upload page.
