# Cutframe Studio

Turn YouTube links and local video files into short clips or one highlight reel. The Next.js studio queues a Django worker that transcribes with faster-whisper and renders with FFmpeg and OpenCV.

## Run with Docker Compose

1. Start Docker Desktop. Create the `D:\volumes\Cutframe` folder if it does not exist.
2. From the project root, build and start the stack.

   ```powershell
   docker compose up -d --build
   ```

3. Open [Cutframe Studio](http://localhost:5031). Configure a ranking provider under **Settings → AI models** to rank speech with AI. The Gameplay editing style and Highlights with local visual selection run locally.

| Service | Host address |
| --- | --- |
| Next.js studio | `http://localhost:5031` |
| Django API | `http://localhost:5032/api/` |
| Render worker | Runs on the Docker network |
| Publisher | Runs on the Docker network |
| PostgreSQL 17 | `db:5432` on the Docker network |

The app runs without an `.env` file. Application settings are saved from the browser. Docker Compose binds the web and API ports to loopback and mounts the data directory before Django can connect to PostgreSQL. To use a different drive, change all three host paths in `compose.yaml` together.

PostgreSQL stores app settings, encrypted provider credentials, publishing accounts, render history, clip metadata, workspace preferences, and browser sessions in `D:\volumes\Cutframe\postgres`. Compose creates a database password in `D:\volumes\Cutframe\postgres-config` on first startup. Back up that folder and `django.secret` with the database so Cutframe can connect and decrypt the saved credentials.

On the first PostgreSQL startup, Cutframe copies the previous `studio.sqlite3` into the new database. The original SQLite file stays available as a backup. Uploaded media, rendered clips, ZIP exports, and the Whisper cache continue to live in the shared storage folder. Use the same browser and hostname to access your render history.

When running Django directly without Docker Compose, it uses SQLite by default. Set `STUDIO_DB_HOST`, `STUDIO_DB_NAME`, `STUDIO_DB_USER`, and `STUDIO_DB_PASSWORD` to connect to PostgreSQL.

If you use a Python package mirror for builds, pass `--build-arg UV_DEFAULT_INDEX=https://your-mirror.example/simple` to `docker compose build backend`.

### Import settings from an older installation

If you have an old settings file, start the updated stack with its existing data mount. Run this one-time import from PowerShell. It copies known app settings, keeps values already saved in the database, and encrypts imported credentials.

```powershell
$legacySettings = (Resolve-Path .\.env).Path
docker compose run --rm --no-deps -T --volume "${legacySettings}:/run/cutframe-legacy.env:ro" backend python manage.py import_legacy_settings /run/cutframe-legacy.env
```

Verify the settings in the browser. You can archive the old file outside the project. If the old deployment used custom ports or storage paths, match them in Compose before starting the new stack. If you explicitly overrode Django's encryption key through `DJANGO_SECRET_KEY`, preserve that same bootstrap key in the shared data directory's `django.secret` before upgrading so existing encrypted credentials remain readable.

## Workspace settings

Open **Settings** to save the default clip selection, clip length, aspect ratio, source quality, transcription language, framing, and caption style. Choose **By clip count** to render up to that many clips, or **By viral score** to keep every ranked highlight that meets your minimum. Leave the length fields blank for automatic timing, or set a minimum and maximum in seconds. You can override those values for one render.

In **Render defaults**, choose which mode opens first and save the Highlights runtime cap and moment-selection method.

The render details page shows the worker's current action above the estimated overall progress bar. The activity log records stage changes, completed steps, and any errors.

## Upload a local video

Choose **Upload video** on the new render page. Speech-based Short Clips styles require an audio track. Gameplay and Highlights can select moments from silent footage. Cutframe accepts MP4, MOV, M4V, MKV, WebM, AVI, MPEG, and MPEG transport stream files. Change the default 2 GiB upload limit under **Settings → Storage**. Uploaded sources live in the job’s folder and are reused on retry.

## Configure AI models

**Settings → AI models** has **Installation defaults** for shared provider, model, API key, Whisper device, and voice detection settings. Use **This browser** to override the defaults for a workspace. The worker reads the database when starting a render. API keys are write-only in the UI. **Check connection** checks model visibility through the provider’s metadata API.

Select **Load models** to see the options available through the current connection, or enter a model ID manually. You can choose a different ranking model while keeping the server environment's provider and credentials. Select **Use default** to restore its configured model.

| Provider | Configure in Settings |
| --- | --- |
| OpenAI | API key and ranking model |
| Google Gemini | API key and ranking model |
| OpenAI-compatible server | API base URL, model ID, and optional API key |

Start your local model server and load the model before running Cutframe’s connection check. For Ollama on Docker Desktop use `http://host.docker.internal:11434/v1`. When running both apps directly on the host, use `http://localhost:11434/v1`. Saved settings apply without restarting. [OpenShorts local model setup](https://github.com/mutonby/openshorts#6-run-without-a-google-key-local-llm-optional) · [Ollama API compatibility](https://docs.ollama.com/api/openai-compatibility)

## Detect, reframe, and caption

- **Viral moment detection** prioritizes clips with an opening hook and a payoff. The worker aligns proposed cut points with the transcript and removes overlapping picks that repeat the same moment.
- **Smart 9:16 reframing** tracks a face through the shot, stacks two separated faces, or keeps the full scene visible. When it detects a stable webcam inset, it puts the webcam above the full screen and leaves space below for captions. Select **Screen + streamer** to prioritize the full screen on wide footage even if the webcam is hard to detect.
- **Captions** use local Whisper word timings. Choose classic captions, highlighted words, or off. Captioned MP4s have subtitles burned in, and each includes a separate SRT download.

The feature design draws on OpenShorts. [OpenShorts reference](https://github.com/mutonby/openshorts)

Face detection runs locally with the bundled YuNet model. [OpenCV YuNet and its license](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)

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

## Export clips

Open a finished render to play each clip and edit or copy its title and description. Download the MP4 by itself or in the ZIP. Both use the saved title as the filename and embed it as MP4 metadata. **Copy title** copies the exact title to paste into YouTube Studio’s Title field.

New AI-ranked renders produce specific titles, descriptions grounded in the clip, and up to five topic hashtags in the clip's spoken language. [AutoClip title prompt reference](https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/prompts/highlight_v1.txt) · [YouTube hashtag guidance](https://support.google.com/youtube/answer/6390658)

Open **Settings → Descriptions** to choose a writing model from your saved OpenAI, Gemini, or OpenAI-compatible text connection. Leave the model ID blank to use the ranking model. Automatic AI descriptions are opt-in and use one text model request per batch of up to 12 finished videos. Drafts use any available speech excerpts and chapter topics to put concrete, searchable details up front. [YouTube description guidance](https://support.google.com/youtube/answer/12948449)

Open **Edit** on a finished clip and choose **Regenerate description** for general social, YouTube, Instagram Reels, or TikTok. Review and edit the draft before saving. Once saved, it is used in the TXT, ZIP, and future publishing drafts.

### Generate thumbnail artwork

Open **Settings → Thumbnails** and choose an OpenAI image model or enter the model ID and base URL for a compatible server. A local server running on the Docker host uses an address such as `http://host.docker.internal:7860/v1`. The server must accept `POST /images/generations` and return a base64 image in `data[0].b64_json`. OpenAI can use the key saved under **AI models**, and receives a still as a visual reference when available. Both providers receive the clip title, description, and any art direction you add. [OpenAI Images API](https://developers.openai.com/api/reference/resources/images/methods/generate)

Enable **Automatically generate covers for new renders** to generate one image per finished video. For a finished clip, open **Edit thumbnail** and click **Generate AI thumbnail** or **Regenerate thumbnail**. Add art direction to guide the next attempt. Successful generation updates the poster, JPG, and ZIP. It also supplies the thumbnail for future YouTube publications. A failed generation keeps the current cover. Without an image model, new renders use a frame cover. **Design from a video frame** provides a manual option with headline controls. [YouTube thumbnail design tips](https://support.google.com/youtube/answer/12340300)

```text
01/
  An Example Clip.mp4
  An Example Clip.jpg
  An Example Clip.txt
  An Example Clip.srt
```

The ZIP uses the latest saved titles and descriptions. Captioned clips include the SRT file. Text edits reuse the rendered media and require zero ranking requests.

## Organize and clean up the library

Open **Library** to find old renders by clip title, source, mode, status, or age. Sort by size to find large renders, and use the ZIP button to keep a copy before deleting. **Select renders** lets you remove several items from the current page. The confirmation shows the files' combined size.

Deletion removes the render, including its stored source copy, intermediate files, and exports. Queued or running renders and renders with active or scheduled publications are protected. Previously published videos and their publishing records stay available. If the operating system holds a file open, close the download and retry the **Cleanup incomplete** item in Library.

## Publish clips

Save developer app credentials in **Settings → Publishing**, then open **Publish Studio** to connect the account and queue or schedule a finished video. The [local publishing guide](docs/publish-studio.md) covers YouTube on localhost, stable HTTPS callbacks for TikTok and Instagram, and troubleshooting.

## Project layout

```text
backend/
  manage.py          Django management commands
  studio/            API, job queue, exports, migrations, and tests
  shorts_generator/  Download, transcription, ranking, and rendering
  requirements.txt  Backend Python dependencies
  Dockerfile
frontend/
  app/               Next.js interface
  public/            Static artwork
  Dockerfile
compose.yaml
```

The worker downloads the selected Whisper model on first use. Choose the model and device in **Settings → AI models**.

## Tests and operations

Run the API and media regression tests with test responses for highlight ranking.

```powershell
docker compose run --rm --no-deps backend python manage.py test studio
docker compose logs -f worker
docker compose down
```

With the database running, create a PostgreSQL backup and copy it to the host.

```powershell
docker compose exec -T db pg_dump -U cutframe -d cutframe -Fc -f /tmp/cutframe.dump
docker compose cp db:/tmp/cutframe.dump .\cutframe.dump
```

## License

This project is licensed under the [MIT License](LICENSE) and builds on the [AI YouTube Shorts Generator](https://github.com/SamurAIGPT/AI-Youtube-Shorts-Generator).
