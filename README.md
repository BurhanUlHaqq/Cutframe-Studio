# Cutframe Studio

Turn YouTube links and local video files into short clips or one highlight reel. The Next.js studio queues a Django worker that transcribes with faster-whisper and renders with FFmpeg and OpenCV.

## Run with Docker Compose

1. Start Docker Desktop. Create the `D:\volumes\Cutframe` folder if it does not exist.
2. From the project root, build and start the stack.

   ```powershell
   docker compose up -d --build
   ```

3. Open [Cutframe Studio](http://localhost:5031). Configure a ranking provider under **Settings → AI models** if you plan to use Short Clips or AI transcript selection. Highlights can also run with local visual selection.

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

Choose **Upload video** on the new render page. Short Clips requires an audio track. Highlights accepts silent footage and can select moments by visual activity. Cutframe accepts MP4, MOV, M4V, MKV, WebM, AVI, MPEG, and MPEG transport stream files. Change the default 2 GiB upload limit under **Settings → Storage**. Uploaded sources live in the job’s folder and are reused on retry.

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

## Assemble a highlight reel

Choose **Highlights** on the new render page and set a maximum of 1–30 minutes. Cutframe assembles one 16:9 MP4, keeps the full frame visible, and preserves the order of the source. The actual runtime depends on the selected moments. **AI transcript** uses your configured ranking model to choose self-contained story beats. **Local visual activity** selects footage by sampled frame changes and works without a ranking provider. Turn captions off to skip Whisper when using local visual activity.

Use the chapter list to jump to each moment and see its original timestamp. The ZIP includes the MP4, a JPG thumbnail, and title and description text, plus an SRT when speech captions were generated. Eligible YouTube chapters are written to the TXT and appended when publishing to YouTube from Cutframe. YouTube requires a verified account for uploads over 15 minutes. [Story-driven summarization](https://openaccess.thecvf.com/content_cvpr_2013/papers/Lu_Story-Driven_Summarization_for_2013_CVPR_paper.pdf) · [YouTube chapter requirements](https://support.google.com/youtube/answer/9884579) · [YouTube upload length limits](https://support.google.com/youtube/answer/71673)

## Export clips

Open a finished render to play each clip and edit or copy its title and description. Download the MP4 by itself or in the ZIP. Both use the saved title as the filename and embed it as MP4 metadata. **Copy title** copies the exact title to paste into YouTube Studio’s Title field.

New renders produce specific titles and short descriptions in the clip's spoken language. Cutframe chooses a sharp, well-exposed frame from the reframed footage before adding captions. Use **JPG** on the clip card to download the cover on its own. [AutoClip title prompt reference](https://github.com/artbyjazi/autoclip/blob/main/backend/autoclip/prompts/highlight_v1.txt)

```text
01/
  An Example Clip.mp4
  An Example Clip.jpg
  An Example Clip.txt
  An Example Clip.srt
```

The ZIP uses the latest saved titles and descriptions. Captioned clips include the SRT file. Text edits reuse the rendered media and require zero ranking requests.

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
