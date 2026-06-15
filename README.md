# YouTify
YouTify is a Discord bot for tracking YouTube channels and playlists to receive notifications about new videos.

## Features
- Tracks channels and playlists per Discord user.
- Supports catch-up mode to stay up to date about what you missed when you weren't using this bot.
- Sends updates to a guild channel or via direct messages.
- Supports Google auth for private playlists, e.g. Liked Music.

## Setup
1. Create or activate a virtual environment.
2. Install dependencies from `requirements.txt`.
3. Copy `config.example.json` to `config.json` and fill in the values.
4. Put your Google OAuth client file at the path in `client_secrets_file`.
5. Run the bot from `main.py`.

Example commands:
```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
cp config.example.json config.json
python main.py
```

## License
[GNU General Public License v2.0](https://github.com/TheAirBlow/YouTify/blob/main/LICENCE)