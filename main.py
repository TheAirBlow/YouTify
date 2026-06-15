from __future__ import annotations

import argparse
import asyncio
import logging

from bot import load_bot

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="YouTify Discord bot")
    parser.add_argument("-c", "--config", default="config.json", help="Path to config.json")
    parser.add_argument("-v", "--verbose", action="store_true", help="Enable debug-level logging")
    return parser

async def run(config_path: str, *, verbose: bool = False) -> int:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(level=level, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    logging.getLogger("discord").setLevel(logging.INFO)
    logging.getLogger("discord.http").setLevel(logging.INFO)
    bot = load_bot(config_path)
    async with bot:
        await bot.start(bot.config.bot_token)
    return 0

def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return asyncio.run(run(args.config, verbose=args.verbose))
    except (KeyboardInterrupt, asyncio.CancelledError):
        return 0

if __name__ == "__main__":
    raise SystemExit(main())