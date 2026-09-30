# Real Time stream writer

Livrinho ao vivo writes a book about the [oMeiaUm](https://www.twitch.tv/omeiaum) stream, in Brazilian Portuguese, only while someone has the page open.

The live site is [tossemideia.cloud/livrinho-ao-vivo](https://tossemideia.cloud/livrinho-ao-vivo/).

Each browser keeps its own chapter. A refresh continues that chapter. Two open pages are two stories and two model calls. The stream capture is shared: one ffmpeg, one Twitch chat, one speech poller.

## What a reader can do

- Pick a voice: Amigo besta, Romance, Documentário, Detetive, or Personalizado.
- Personalizado takes a short style note (8–800 characters). The note changes tone only. Grounding rules, Brazilian Portuguese, and the instruction to narrate the stream stay outside that note.
- Read the chapter as it is written. Paragraphs are stored in SQLite and replayed on return.
- Clear one chapter from the trash icon on its shelf card. The chapter stays; the next paragraph starts from the current scene.
- See viewer chat in dark green Comic Sans. Emote names, including ones inside quotation marks, render as 7TV images. MeiaUm's speech and the screen stay in the book font.

Writing pauses when the scene has not changed, when the monthly estimate hits US$ 10, or when more than 8 chapters are live. Saved text stays readable. The cap uses published DeepSeek Flash rates and resets on the first day of the next UTC month.

## Run

The API listens on `127.0.0.1:8005`. The page in `frontend/` is static.

```bash
cp backend/.env.example backend/.env
# set DEEPSEEK_API_KEY
docker compose up -d --build
```

Without a key the page still loads and does not call the model.

Point a reverse proxy at the API for `/livrinho-ao-vivo/api/` (buffering off, long read timeout, so the event stream is not cached) and serve `frontend/` at `/livrinho-ao-vivo/`.

## Tests

Offline. No Twitch and no API key.

```bash
cd backend
python -m venv .venv
.venv/bin/pip install -r requirements.txt
PYTHONPATH=. .venv/bin/pytest tests -q
```

`streamkit` and `stream-in-novel` install from [stream-in-unexpected-places](https://github.com/Gugaapo/stream-in-unexpected-places) at commit `7eebf052b78059197fcc5463e39ee3622c3ea3b3`.

## Credits

Speech comes from the [fan transcription API](https://meiaum.vinnytasso.com.br/developers/transcription) by [@vinnytasso](https://x.com/vinnytasso). Prompts and the novel voices come from [stream-in-unexpected-places](https://github.com/Gugaapo/stream-in-unexpected-places).
