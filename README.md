# Cutframe Studio

Turn YouTube links and local video files into short clips. The Next.js studio queues a Django worker that transcribes with faster-whisper, ranks highlights with your chosen AI provider, and renders with FFmpeg and OpenCV.

## Run with Docker Compose

1. Start Docker Desktop. Create the `D:\volumes\Cutframe` folder if it does not exist.
2. Configure a ranking provider in **Settings → AI models** after startup, or copy `.env.example` to `.env` and set the server defaults before startup.
3. From the project root, build and start the stack.

   ```powershell
   docker compose up -d --build
   ```

4. Open [Cutframe Studio](http://localhost:5031).

| Service | Host address |
| --- | --- |
| Next.js studio | `http://localhost:5031` |
| Django API | `http://localhost:5032/api/` |
| Render worker | Runs on the Docker network |
| PostgreSQL 17 | `db:5432` on the Docker network |

The web and API ports bind to the local loopback interface. Set `STUDIO_PORT` and `STUDIO_API_PORT` in `.env` to override them. Set `STUDIO_DATA_PATH` to an existing absolute host folder when using another drive or operating system.

PostgreSQL stores render history, clip metadata, workspace preferences, AI settings, and browser sessions in `D:\volumes\Cutframe\postgres`. Compose creates a database password in `D:\volumes\Cutframe\postgres-config` on first startup. Keep that folder and `django.secret` with the storage so the app can connect to the database and decrypt saved provider credentials.

On the first PostgreSQL startup, Cutframe copies the previous `studio.sqlite3` into the new database. The original SQLite file stays available as a backup. Uploaded media, rendered clips, ZIP exports, and the Whisper cache continue to live in the shared storage folder. Use the same browser and hostname to access your render history.

When running Django directly without Docker Compose, it uses SQLite by default. Set `STUDIO_DB_HOST`, `STUDIO_DB_NAME`, `STUDIO_DB_USER`, and `STUDIO_DB_PASSWORD` to connect to PostgreSQL.

Docker builds use PyPI by default. Set `CUTFRAME_PYPI_INDEX` to a trusted Python package mirror if PyPI downloads are slow.

## Workspace settings

Open **Settings** to save the default clip selection, clip length, aspect ratio, source quality, transcription language, framing, and caption style. Choose **By clip count** to render up to that many clips, or **By viral score** to keep every ranked highlight that meets your minimum. Leave the length fields blank for automatic timing, or set a minimum and maximum in seconds. You can override those values for one render.

The render details page shows the worker's current action above the estimated overall progress bar. The activity log records stage changes, completed steps, and any errors.

## Upload a local video

Choose **Upload video** on the new render page and select a file with audio. Cutframe accepts MP4, MOV, M4V, MKV, WebM, AVI, MPEG, and MPEG transport stream files. Uploads default to a 2 GB limit per video. Set `STUDIO_MAX_UPLOAD_BYTES` in `.env` to change it. Uploaded sources live in the job’s folder under your configured storage path and are reused on retry.

## Configure AI models

**Settings → AI models** saves your ranking provider, model ID, and local Whisper model for this browser. The worker uses the saved settings when it starts a render. Choose **Server environment** to use the provider and model configured in `.env`. API keys entered in Settings are encrypted in the local database. **Check connection** checks model visibility through the provider’s model metadata API.

Select **Load models** to see the options available through the current connection, or enter a model ID manually. You can choose a different ranking model while keeping the server environment's provider and credentials. Select **Use default** to restore its configured model.

| Provider | Server environment | Example API base URL |
| --- | --- | --- |
| OpenAI | `LLM_PROVIDER=openai`, `OPENAI_API_KEY`, `OPENAI_MODEL` | Built in |
| Google Gemini | `LLM_PROVIDER=gemini`, `GEMINI_API_KEY`, `GEMINI_MODEL` | Built in |
| Ollama, LM Studio, vLLM, OpenRouter, or another compatible server | `LLM_PROVIDER=compatible`, `LLM_BASE_URL`, `LLM_MODEL`, optional `LLM_API_KEY` | `http://host.docker.internal:11434/v1` for Ollama on Docker Desktop |

Start your local model server and load the model before running Cutframe’s connection check. When running both apps directly on the host, use `http://localhost:11434/v1` for Ollama. Recreate the containers with `docker compose up -d` after changing server environment values. [OpenShorts local model setup](https://github.com/mutonby/openshorts#6-run-without-a-google-key-local-llm-optional) · [Ollama API compatibility](https://docs.ollama.com/api/openai-compatibility)

## Detect, reframe, and caption

- **Viral moment detection** prioritizes clips with an opening hook and a payoff. The worker aligns proposed cut points with the transcript and removes overlapping picks that repeat the same moment.
- **Smart 9:16 reframing** tracks a face through the shot, stacks two separated faces, or keeps the full scene visible. When it detects a stable webcam inset, it puts the webcam above the full screen and leaves space below for captions. Select **Screen + streamer** to prioritize the full screen on wide footage even if the webcam is hard to detect.
- **Captions** use local Whisper word timings. Choose classic captions, highlighted words, or off. Captioned MP4s have subtitles burned in, and each includes a separate SRT download.

The feature design draws on OpenShorts. [OpenShorts reference](https://github.com/mutonby/openshorts)

Face detection runs locally with the bundled YuNet model. [OpenCV YuNet and its license](https://github.com/opencv/opencv_zoo/tree/main/models/face_detection_yunet)

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
.env.example
```

The worker downloads the selected Whisper model on first use. Set `LOCAL_WHISPER_MODEL` and `LOCAL_WHISPER_DEVICE` in `.env` to control local transcription. Edit `backend/shorts_generator/highlights.py` to adjust highlight ranking criteria.

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
