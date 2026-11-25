# Publish from local Cutframe

Open [Settings → Publishing](http://localhost:5031/settings#publishing-settings-title) to save developer app credentials and copy redirect URIs. Open [Publish Studio](http://localhost:5031/publish), select **Accounts**, and use **Connect account** to sign in with the platform. Settings take effect immediately. Cutframe encrypts app secrets and connected-account tokens in PostgreSQL.

| Platform | Recommended connection | Publishing behavior |
| --- | --- | --- |
| YouTube | Google OAuth **Web application** client with the localhost redirect | Uploads directly. Private is the default. |
| TikTok | Login Kit for Web and an approved Content Posting API app with a stable public HTTPS redirect | Sends a draft to the TikTok inbox. Finish posting in TikTok. |
| Instagram Reels | Facebook Login for Business with a stable public HTTPS redirect and a linked professional Instagram account | Publishes Reels directly to the selected account. |

## Get your developer credentials

| Platform dashboard | Where to find the credentials | Cutframe fields |
| --- | --- | --- |
| [Google Auth Platform → Clients](https://console.cloud.google.com/auth/clients) | Create a **Web application** client. Copy the **Client ID** and **Client secret** from the confirmation dialog or its downloaded JSON. | **YouTube OAuth client ID**, **YouTube OAuth client secret** |
| [TikTok for Developers → Manage apps](https://developers.tiktok.com/portal/apps) | Open your app, then go to **App details → Credentials**. Copy the **Client key** and **Client secret** for the environment you will use. | **TikTok client key**, **TikTok client secret** |
| [Meta for Developers → My Apps](https://developers.facebook.com/apps/) | Open your app and go to **App settings → Basic**. Copy the **App ID** and reveal the **App secret**. If you configured Facebook Login for Business, get its numeric **Configuration ID** under **Configurations**. | **Meta app ID**, **Meta app secret**, **Login configuration ID** if used |

Paste these values into [Settings → Publishing](http://localhost:5031/settings#publishing-settings-title). Cutframe obtains the account's access and refresh tokens during **Connect account**.

## YouTube on localhost

1. Choose or create a Google Cloud project and enable [YouTube Data API v3](https://console.cloud.google.com/apis/library/youtube.googleapis.com).
2. Configure [Branding](https://console.cloud.google.com/auth/branding) and [Audience](https://console.cloud.google.com/auth/audience) in the same project. If the app is **External** and in **Testing**, add the Google account you will connect under **Test users**. Under [Data Access](https://console.cloud.google.com/auth/scopes), configure the YouTube upload and read-only scopes `https://www.googleapis.com/auth/youtube.upload` and `https://www.googleapis.com/auth/youtube.readonly`.
3. Open [Clients](https://console.cloud.google.com/auth/clients) and choose **Create client → Web application**. Under **Authorized redirect URIs**, add `http://localhost:5031/api/publishing/callback/youtube/` exactly as shown in Cutframe. Use the same hostname in the Cutframe browser.
4. Copy the **Client ID** and **Client secret** when Google creates them. If you lose the secret, open that client and use **Add secret** to create a replacement. Save the values in **Settings → Publishing**, then open **Publish → Accounts** and select your Google account and YouTube channel. [Google credential creation](https://developers.google.com/workspace/guides/create-credentials) · [Google secret recovery instructions](https://developers.google.com/health/codelabs/make-your-first-api-call-using-oauth2-playground)
5. For your first transfer, set visibility to **Private** and confirm the upload in YouTube Studio.

Google allows HTTP redirects on localhost. External apps in Testing generally issue seven-day refresh tokens for these scopes. YouTube also restricts uploads from certain unaudited projects to Private. Reconnect after a test token expires and follow Google's verification and audit instructions if you need a long-running integration. [Google OAuth setup](https://developers.google.com/identity/protocols/oauth2/web-server) · [Token expiration](https://developers.google.com/identity/protocols/oauth2#expiration) · [YouTube upload API](https://developers.google.com/youtube/data/reference/rest/v3/videos/insert)

Cutframe sends the saved JPG as a separate request after YouTube finishes processing the upload. For a video already in the queue history, open **Publish → Queue** and select **Apply thumbnail** to set its cover on the existing YouTube video. If you edit the JPG later, choose **Update thumbnail**. The channel must have access to custom thumbnails. [YouTube thumbnail upload API](https://developers.google.com/youtube/data/reference/rest/v3/thumbnails/set)

## Stable HTTPS callbacks for TikTok and Instagram

For a local Windows installation, use a **named Cloudflare Tunnel** with a dedicated subdomain on a domain you control. Route just the two callbacks to Django on `127.0.0.1:5032`. Keep opening Cutframe on `http://localhost:5031`. The browser brings authorization back to that address and the worker uploads videos through outbound requests. The tunnel can be stopped after connecting and started again when you reconnect. [Cloudflare named tunnel guide](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/create-local-tunnel/) · [Path-based ingress configuration](https://developers.cloudflare.com/tunnel/features/locally-managed-tunnels/configuration-file/)

1. Add your domain in the [Cloudflare dashboard](https://dash.cloudflare.com/) and install `cloudflared` on the Windows host. Open PowerShell and create the named tunnel. Use your real subdomain in place of the example. [Download cloudflared for Windows](https://developers.cloudflare.com/tunnel/downloads/)

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

1. In [Manage apps](https://developers.tiktok.com/portal/apps), select **Connect an app** or open an existing app. Add **Login Kit** with Web enabled and **Content Posting API**. Request `user.info.basic` and `video.upload`. [TikTok app registration and credential location](https://developers.tiktok.com/docs/en/getting-started-create-an-app)
2. Register the HTTPS redirect from the TikTok card in Login Kit. It ends in `/api/publishing/callback/tiktok/`. Under **App details → Credentials**, copy the **Client key** and **Client secret** from the same app and environment. Keep sandbox credentials paired with the sandbox configuration and target user.
3. In **Publish → Accounts**, connect your creator account and grant the requested scopes. When a video reaches **Ready in TikTok**, open the notification to review the draft, choose its cover frame, set visibility, add the caption, and publish. Cutframe can copy the caption and check the draft status.

TikTok's sandbox supports designated target users and limits public posting. Its app review rules exclude private or personal-only apps. If your installation has no approved TikTok developer app, use **Post manually** in Publish Studio. [TikTok Login Kit for Web](https://developers.tiktok.com/docs/en/login-kit-web) · [Inbox upload](https://developers.tiktok.com/docs/en/content-posting-api-get-started-upload-content) · [Sandbox](https://developers.tiktok.com/docs/en/add-a-sandbox) · [App review rules](https://developers.tiktok.com/docs/en/app-review-guidelines)

### Instagram Reels

1. Link an Instagram **Business** or **Creator** account to a Facebook Page you manage. Confirm the Facebook user can publish to that Page.
2. In [Meta for Developers → My Apps](https://developers.facebook.com/apps/), choose **Create App** or open your existing app. Set up **Instagram API with Facebook Login** and **Facebook Login for Business**. Configure `pages_show_list`, `pages_read_engagement`, `instagram_basic`, and `instagram_content_publish`. For roles assigned through Business Manager, check the additional permissions in Meta's publishing guide.
3. Add the Instagram callback under **Valid OAuth Redirect URIs**. It ends in `/api/publishing/callback/instagram/`. If you create a Login for Business configuration, select **User access token** and include your publishing permissions and Page assets. Save the configuration ID in Cutframe.
4. In the Meta App Dashboard, open **App settings → Basic**. Copy **App ID** and select **Show** beside **App secret**. Save both in **Settings → Publishing**. Connect the Facebook account in **Publish → Accounts** and select the linked Instagram profile when preparing a reel. In development mode, use eligible app roles. Access for other users may require Meta approval. [Meta's app secret location](https://developers.facebook.com/docs/graph-api/guides/secure-requests/)

Cutframe transfers the local MP4 through Meta's resumable upload endpoint and requests the selected thumbnail frame when a timestamp is recorded in the render. To use the designed JPG with its headline, download it and choose it when uploading manually in Instagram. Start with an account where you are ready for a live Reel. [Instagram API with Facebook Login](https://developers.facebook.com/documentation/instagram-platform/instagram-api-with-facebook-login/) · [Reels covers and `thumb_offset`](https://developers.facebook.com/documentation/instagram-platform/instagram-graph-api/reference/ig-user/media)

## Troubleshoot connection and publishing

| Symptom | What to check |
| --- | --- |
| Redirect URI mismatch | Copy the full URI again from **Settings → Publishing**. Match the scheme, hostname, port, path, and trailing slash. Use the correct developer app and its matching secret. |
| The public callback returns 404 or 502 | Check the public hostname route, exact regex path, running tunnel, and Django on port 5032. Use the callback test above. |
| The callback opens Cutframe and reports an expired connection | Restart **Connect account** from the same browser and exact local address. Finish the sign-in within ten minutes. |
| The returned platform account is missing | Confirm you selected the intended YouTube channel or the Facebook user that manages the linked Page. Verify developer app roles and granted permissions. |
| The platform rejects the upload | Check privacy or audit requirements, the account's media limits, and the message in Cutframe's **Queue**. If the queue says **Check platform**, check the account before approving another upload. |

To schedule a transfer, set **Send time** and leave the Cutframe Docker stack running at that time. For a manual upload, use **Post manually** to download the MP4, copy the prepared text, and open the platform's official upload page.
