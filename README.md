# Cutframe Studio

Turn a YouTube link or local video into a pack of short clips or a highlight reel. Review and edit the finished videos, download a ZIP, or prepare posts for YouTube, TikTok, and Instagram Reels. Cutframe runs in Docker with a Next.js interface, Django API and workers, and PostgreSQL storage.

[Quick start](#run-with-docker-compose) · [Create a render](#create-a-render) · [Video downloader](#download-videos) · [AI models](#configure-ai-models) · [Exports](#review-and-export-clips) · [Publishing](#publish-clips) · [Operations](#tests-and-operations) · [License](#license)

## Run with Docker Compose

1. Start Docker Desktop with Linux containers. In PowerShell, create the data directories.

   ```powershell
   New-Item -ItemType Directory -Force -Path 'D:\volumes\Cutframe', 'D:\volumes\Cutframe\postgres', 'D:\volumes\Cutframe\postgres-config' | Out-Null
   ```

2. From the project root, build and start Cutframe.

   ```powershell
   docker compose up -d --build --wait
   ```

3. Open [Cutframe Studio](http://localhost:5031). To try a render using local selection, choose **Short Clips → Gameplay** or **Highlights → Local visual activity**. For speech-based short clips, configure a ranking provider in **Settings → AI models**.

| Service | Host address |
| --- | --- |
| Next.js studio | `http://localhost:5031` |
| Django API | [Health check](http://localhost:5032/api/health/) |
| Render worker | Runs on the Docker network |
| Downloader | Runs on the Docker network |
| Publisher | Runs on the Docker network |
| PostgreSQL 17 | `db:5432` on the Docker network |

The Docker setup runs without an `.env` file. Save model, publishing, and workspace settings in the browser. Compose binds the web app and API to ports 5031 and 5032 on loopback. To change the storage location, update all three host paths in [compose.yaml](compose.yaml) together.

PostgreSQL stores app settings, encrypted provider credentials, publishing accounts, render and download history, clip metadata, workspace preferences, and browser sessions in `D:\volumes\Cutframe\postgres`. Compose creates a database password in `D:\volumes\Cutframe\postgres-config` on first startup. Back up that folder and `django.secret` with the database so Cutframe can connect and decrypt the saved credentials.

When the migration runs against a fresh Docker PostgreSQL database, it can import render history, workspace settings, and sessions from `studio.sqlite3` in the existing data directory. The original file stays available as a backup. Uploaded sources, finished exports, and the Whisper cache live in the same shared storage folder. Use the same browser profile and hostname to access your workspace. For example, keep opening `localhost:5031` if that is where you first used Cutframe.

When running Django directly without Docker Compose, it uses SQLite by default. Set `STUDIO_DB_HOST`, `STUDIO_DB_NAME`, `STUDIO_DB_USER`, and `STUDIO_DB_PASSWORD` to connect to PostgreSQL.

If you use a Python package mirror for builds, pass `--build-arg UV_DEFAULT_INDEX=https://your-mirror.example/simple` to `docker compose build backend`.

### Import settings from an older installation

If you have an old settings file, start the updated stack with its existing data mount. Run this one-time import from PowerShell. It copies known app settings, keeps values already saved in the database, and encrypts imported credentials.

```powershell
$legacySettings = (Resolve-Path .\.env).Path
docker compose run --rm --no-deps -T --volume "${legacySettings}:/run/cutframe-legacy.env:ro" backend python manage.py import_legacy_settings /run/cutframe-legacy.env
```

Verify the settings in the browser. You can archive the old file outside the project. If the old deployment used custom ports or storage paths, match them in Compose before starting the new stack. If you explicitly overrode Django's encryption key through `DJANGO_SECRET_KEY`, preserve that same bootstrap key in the shared data directory's `django.secret` before upgrading so existing encrypted credentials remain readable.

## Create a render

1. Open **New render**. Paste a YouTube URL or choose **Upload video** to browse for a file or drop it into the upload area.
2. Choose **Short Clips** for a pack of standalone videos or **Highlights** for one 16:9 reel up to 30 minutes.
3. Review the **ZIP export** summary and select **Generate clips** or **Generate highlights**. On phones, the Generate button stays visible while you scroll.

For Short Clips, choose **By clip count** to render up to a set number or **By viral score** to keep ranked highlights that meet your minimum. Gameplay uses an activity score based on motion and audio changes. Set a minimum or maximum clip length in seconds, or leave both blank for automatic timing. Choose the editing style, aspect ratio, framing, caption style, and transcription language per render. Save your usual choices in **Settings → Render defaults**, including the default mode and Highlights runtime cap.

The uploader accepts MP4, MOV, M4V, MKV, WebM, AVI, MPEG, and MPEG transport stream files. Change the default 2 GiB limit in **Settings → Storage**. Speech-based Short Clips styles require an audio track. Gameplay and Highlights can use silent footage.

The render page shows the worker's current action and estimated progress. Open **Render activity** for worker updates. You can leave the page and return through the Library. **Cancel render** stops queued work immediately and running work at the next checkpoint. An AI request already in progress can take a moment to finish. Retry reuses the uploaded source and can reuse completed videos and matching AI covers if export was interrupted.

Open a Short Clips render and choose **Create a highlight reel**, or open a Highlights render and choose **Create short clips**. Set the options for the new output and queue it from the same original video. Each render has its own ZIP and can be reviewed or published separately. Cutframe reuses a saved YouTube download when available at the same requested quality. Uploaded videos are copied locally by the worker, so you can remove the source render once the new render has prepared its own copy. A saved transcript is reused when it matches the copied video, language, Whisper model, and voice activity settings. A change to caption styling can use the same word timings.

## Download videos

Open **Downloader** in the sidebar to save original videos without starting a render. Paste one link per line, choose a resolution preference, and select **Download video**. Use **Save video** when the file is ready. Download history is private to the workspace and remains available when you close the page. You can stop an active download, retry a stopped or failed one, or delete a saved file from Cutframe.

The tool accepts individual public YouTube videos and Shorts, Vimeo videos, Dailymotion videos, TikTok short links and videos, public Instagram reels and video posts, Twitch clips and VODs, and Reddit-hosted videos. It queues up to eight links at a time with a 4 GiB limit for each video. Resolution preferences use the closest source quality. Output stays in the source container or is muxed with its audio without re-encoding, so the extension may vary.

Some platforms restrict downloads by region, sign-in, or network, and site changes can temporarily affect extraction. Use finished videos you have permission to save. The downloader runs as its own Compose service and stores files under `D:/volumes/Cutframe/downloads`. [yt-dlp supported sites](https://github.com/yt-dlp/yt-dlp/blob/master/supportedsites.md)

## Configure AI models

Open **Settings → AI models** to see the saved model for each task. Apply a recommendation to fill the connection fields, then check and save your selection.

| Tab | Runs on | Where to start |
| --- | --- | --- |
| Moment detection | OpenAI, Gemini, or an OpenAI-compatible text server | Use a cloud text model for long transcripts, or run Qwen in Ollama or LM Studio. |
| Transcription | Local faster-whisper on the worker | Start with `base` on CPU. Try `small` for difficult speech or a larger model on a CUDA-enabled worker. |
| Descriptions | The moment detection connection or an independent text provider | Reuse the existing connection, or save a separate smaller text model for copy and hashtags. |
| Thumbnails | OpenAI or an OpenAI-compatible image server | Use GPT Image or a locally hosted image model such as FLUX.1 schnell through LocalAI. Frame covers are available with AI generation off. |

**This workspace** overrides **Installation defaults** for moment detection and transcription. Descriptions and thumbnails are saved per workspace. Settings and encrypted credentials are stored in the database and read by new renders without restarting. The app sends keys to your configured provider and keeps them write-only in the UI.

**Load models** lists IDs from the selected provider. **Check connection** checks the selected ID using model metadata. These actions make no generation requests. Some compatible servers omit a model list, so you can also enter a model ID directly.

From a Docker Desktop container, use `http://host.docker.internal:11434/v1` for Ollama, `http://host.docker.internal:1234/v1` for LM Studio, or `http://host.docker.internal:8080/v1` for a LocalAI server on its usual port. Replace the host with `localhost` when Cutframe and the model server both run directly on the host. Install or load your model in the server first and ensure the server accepts connections from Cutframe. Local image servers must support `/images/generations` with an inline base64 response. [Docker Desktop networking](https://docs.docker.com/desktop/features/networking/networking-how-tos/) · [Ollama OpenAI compatibility](https://docs.ollama.com/api/openai-compatibility) · [LM Studio local server](https://lmstudio.ai/docs/developer/core/server) · [LocalAI image generation](https://localai.io/docs/models/capabilities/vision/image-generation/)

## Detect, reframe, and caption

- **Viral moment detection** prioritizes clips with an opening hook and a payoff. The worker aligns proposed cut points with the transcript and removes overlapping picks that repeat the same moment.
- **Smart 9:16 reframing** tracks a face through the shot, stacks two separated faces, or keeps the full scene visible. When it detects a stable webcam inset, it puts the webcam above the full screen and leaves space below for captions. Select **Screen + streamer** to prioritize the full screen on wide footage even if the webcam is hard to detect.
- **Captions** use local Whisper word timings. Choose classic captions, highlighted words, or off. Captioned MP4s have subtitles burned in, and each includes a separate SRT download.

The feature design draws on OpenShorts. [OpenShorts reference](https://github.com/mutonby/openshorts)

Face detection runs locally with the bundled YuNet model. [OpenCV YuNet](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)

### Editing styles for Short Clips

Play the sample cards to compare styles, then choose one for a render or save it under **Settings → Render defaults**. Explicit clip lengths, framing, and caption settings take precedence.

| Style | Selection and editing |
| --- | --- |
| Balanced | Finds self-contained moments with a clear hook and payoff. Uses standard smart framing and caption pacing. |
| Fast Cuts | Favors quick payoffs, tightens pauses confirmed by audio and transcript timing, and uses shorter caption groups. |
| Podcast | Preserves conversation context, uses steadier face framing, and levels spoken audio. Removes only longer confirmed pauses. |
| Gameplay | Ranks sustained motion and audio changes locally. Keeps the main screen visible and moves a detected webcam inset above it. In score mode the threshold uses activity. |
| Tutorial | Keeps the setup, action, and result together. Smart framing fits the full demonstration on screen. |

## Assemble a highlight reel

Choose **Highlights** on the new render page and set a maximum of 1–30 minutes. Cutframe assembles one 16:9 MP4, keeps the full frame visible, and preserves the order of the source. The actual runtime depends on the selected moments. **AI transcript** uses your configured ranking model to choose self-contained story beats. **Local visual activity** selects footage by sampled frame changes and works without a ranking provider. Turn captions off to skip Whisper when using local visual activity.

Use the chapter list to jump to each moment and see its original timestamp. The ZIP includes the MP4, a JPG thumbnail, and title and description text, plus an SRT when speech captions were generated. Eligible YouTube chapters are written to the TXT and appended when publishing to YouTube from Cutframe. YouTube requires a verified account for uploads over 15 minutes. [Story-driven summarization](https://openaccess.thecvf.com/content_cvpr_2013/papers/Lu_Story-Driven_Summarization_for_2013_CVPR_paper.pdf) · [YouTube chapter requirements](https://support.google.com/youtube/answer/9884579) · [YouTube upload length limits](https://support.google.com/youtube/answer/71673)

### Community guidelines for highlights

**AI transcript** screens the same timed transcript it uses to find story beats. It excludes passages that the model flags for safety or missing context, then rejects cuts that would include those passages after adjusting timestamps. If no usable moments pass, the render stops. **Local visual activity** picks moments by motion and leaves the content review to you. It is also used when there is no speech or configured ranking model. The render activity log and the review page show which method was used.

Before queueing a highlight reel in **Publish Studio**, watch the full cut with sound and review its captions, title, description, and cover for each destination. Confirm that you have the rights to use the footage, music, and other material in the reel. The same review applies before posting an exported file manually. Transcript screening can miss graphic visuals, on-screen text, and audio or dialogue that was missed during transcription.

- **YouTube** may require the context for sensitive material to appear in the video or audio. Keep context inside the cut and make its cover accurately represent the reel. [Content context on YouTube](https://support.google.com/youtube/answer/6345162) · [Violent or graphic content](https://support.google.com/youtube/answer/2802008) · [Thumbnails policy](https://support.google.com/youtube/answer/9229980)
- **TikTok** removes violations and can restrict mature content. Check the finished reel and caption before completing an inbox draft or a manual upload. [TikTok Community Guidelines](https://www.tiktok.com/community-guidelines/) · [TikTok’s official policy update](https://newsroom.tiktok.com/create-discover-and-connect-on-tiktok-with-simpler-and-stronger-community-guidelines?lang=en)
- **Instagram** applies additional recommendation rules to discovery surfaces, including Reels. Sensitive material can have reduced distribution even when allowed. [Meta’s Recommendation Guidelines](https://about.fb.com/news/2020/08/recommendation-guidelines/) · [Instagram’s Sensitive Content Control](https://about.fb.com/news/2021/07/introducing-sensitive-content-control/)
- **Copyright and monetization** have their own requirements. A short extract, credit, or disclaimer alone gives no permission to reuse a movie scene, stream, or song. YouTube also assesses reused content for monetization. [Fair use on YouTube](https://support.google.com/youtube/answer/9783148) · [YouTube monetization policies](https://support.google.com/youtube/answer/1311392)

## Review and export clips

Open a finished render to preview each video. The clip card offers **Publish**, **MP4**, and **Edit text**. Expand **More clip actions** to copy the title or post text, edit the thumbnail, or download the JPG, TXT, or available SRT. MP4 downloads use the saved title as the filename and embed it as video metadata. **Copy title** captures the exact title for a manual YouTube upload.

New AI-ranked renders produce specific titles, descriptions grounded in the clip, and up to five topic hashtags in the clip's spoken language. [AutoClip title prompt reference](https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/prompts/highlight_v1.txt) · [YouTube hashtag guidance](https://support.google.com/youtube/answer/6390658)

Open **Settings → AI models → Descriptions** to choose a writing model. Reuse the moment detection connection or configure an independent provider. Automatic AI descriptions are opt-in and use one text model request per batch of up to 12 finished videos. Drafts use any available speech excerpts and chapter topics to put concrete, searchable details up front. [YouTube description guidance](https://support.google.com/youtube/answer/12948449)

Open **Edit text** on a finished clip and choose **Regenerate description** for general social, YouTube, Instagram Reels, or TikTok. Review and edit the draft before saving. Once saved, it is used in the TXT, ZIP, and future publishing drafts.

### Generate thumbnail artwork

Open **Settings → AI models → Thumbnails** and choose an OpenAI image model or enter the model ID and base URL for a compatible server. A local server running on the Docker host uses an address such as `http://host.docker.internal:7860/v1`. The server must accept `POST /images/generations` and return a base64 image in `data[0].b64_json`. OpenAI can use the key saved under **AI models**, and receives a still as a visual reference when available. Both providers receive the clip title, description, and any art direction you add. [OpenAI Images API](https://developers.openai.com/api/reference/resources/images/methods/generate)

Enable **Automatically generate covers for new renders** to use your selected image model during export. For an existing clip, open **More clip actions → Edit thumbnail** to generate a new AI cover, add art direction, or design a cover from a video frame. A new cover updates the poster, JPG, ZIP, and future YouTube uploads. Frame covers are available when AI covers are skipped or unavailable. [YouTube thumbnail design tips](https://support.google.com/youtube/answer/12340300)

Set **Maximum automatic covers per render** to bound the number of image requests. During generation, **Skip remaining AI covers** finishes the ZIP with frame covers for the rest. Cutframe also stops automatic cover requests when the provider reports a rate limit, missing model, or credential error.

```text
01/
  An Example Clip.mp4
  An Example Clip.jpg
  An Example Clip.txt
  An Example Clip.srt
```

The ZIP uses the latest saved titles and descriptions. Captioned clips include the SRT file. Text edits reuse the rendered media and require zero ranking requests.

## Organize and clean up the library

Open **Library** to search by clip title or source. Use **Ready**, **In progress**, or **Failed** for quick access, or open **Filters** for mode, age, other statuses, and sort order. Choose **Largest first** to find large renders and use **ZIP** to keep a copy before deleting. **Select renders** lets you clean up several items on the current page with a combined size estimate.

Deletion removes the render, including its stored source copy, intermediate files, and exports. Queued or running renders and renders with active or scheduled publications are protected. Previously published videos and their publishing records stay available. If the operating system holds a file open, close the download and retry the **Cleanup incomplete** item in Library.

Use **Clear working files** to reclaim temporary media and downloaded source copies from a completed render while keeping its final clips, covers, ZIP, and metadata. The Library shows the amount you can reclaim.

## Publish clips

In **Publish Studio**, use the **Renders** and **Clips** panes to browse finished work. Search across both by source or clip title. **New post** previews the selected video and prefills the title and caption. **Bulk post** keeps your selection as you move between renders and lets you schedule multiple deliveries with a gap between videos.

- **Connected accounts** use developer credentials saved in **Settings → Publishing**. Connect an account in **Publish Studio → Accounts**. YouTube uses the post title and applies the saved cover where supported. Instagram uploads Reels, and the TikTok API sends inbox drafts to finish in TikTok.
- **TikTok Studio** uses your browser’s TikTok sign-in. Select a clip, download the MP4, copy the caption, and open the official uploader in a new tab. Upload and confirm the post in TikTok Studio. This path works without a developer app or an HTTPS callback. [Open the TikTok Studio tab](http://localhost:5031/publish?mode=tiktok)
- **Queue** shows individual deliveries and batches with pagination and platform and status filters. **Needs attention** surfaces failed posts and deliveries to check on the platform.

See the [local publishing guide](docs/publish-studio.md) for account setup, credential links, HTTPS callbacks, and troubleshooting.

## Project layout

```text
backend/
  manage.py          Django management commands
  studio/            API, job queue, exports, migrations, and tests
  publishing/        OAuth connections, publishing queue, and platform adapters
  shorts_generator/  Download, transcription, ranking, and rendering
  requirements.txt   Backend Python dependencies
  Dockerfile
frontend/
  app/               Next.js interface
  public/            Static artwork
  Dockerfile
compose.yaml
```

The worker downloads the selected Whisper model on first use. Choose the model and device in **Settings → AI models → Transcription**.

## Tests and operations

Run the render and publishing regression suites in an isolated container using SQLite and no network access. This keeps the tests off your workspace database and away from live providers.

```powershell
docker compose build backend
docker run --rm --network none -e STUDIO_DATA_DIR=/tmp/cutframe cutframe-studio-backend python manage.py test studio publishing
```

Check the stack, follow the worker logs, or stop the application.

```powershell
docker compose ps
docker compose logs -f worker publisher downloader
docker compose down
```

With the database running, create a PostgreSQL backup and copy it to the host. Keep the dump with copies of `django.secret`, `postgres-config`, and the shared media folders so you can recover credentials and exports alongside their metadata.

```powershell
docker compose exec -T db pg_dump -U cutframe -d cutframe -Fc -f /tmp/cutframe.dump
docker compose cp db:/tmp/cutframe.dump .\cutframe.dump
```

## License

Cutframe Studio is licensed under the [MIT License](LICENSE). The project builds on [AI YouTube Shorts Generator](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator) by Anil Chandra Naidu Matcha. The root license includes the Cutframe and upstream copyright notices. [Upstream MIT license](https://github.com/Anil-matcha/AI-Youtube-Shorts-Generator/blob/main/LICENSE)

The bundled YuNet face detection model by Shiqi Yu carries its own MIT notice. [Bundled YuNet license](backend/shorts_generator/local/models/LICENSE)
