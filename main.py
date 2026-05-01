import os
from dotenv import load_dotenv
import asyncio
import twitchio
from datetime import datetime

load_dotenv()

"""An example of connecting to a conduit and subscribing to EventSub when a User Authorizes the application.

This bot can be restarted as many times without needing to subscribe or worry about tokens:
- Tokens are stored in '.tio.tokens.json' by default
- Subscriptions last 72 hours after the bot is disconnected and refresh when the bot starts.

Consider reading through the documentation for AutoBot for more in depth explanations.
"""

import asyncio
import logging
import random
from typing import TYPE_CHECKING

import asqlite

import twitchio
from twitchio import eventsub
from twitchio.ext import commands


if TYPE_CHECKING:
    import sqlite3


LOGGER: logging.Logger = logging.getLogger("Bot")

# Consider using a .env or another form of Configuration file!
CLIENT_ID: str = os.getenv("CLIENT_ID")
CLIENT_SECRET: str = os.getenv("CLIENT_SECRET")
BOT_ID = os.getenv("BOT_ID")
OWNER_ID = os.getenv("MY_ID")


class Bot(commands.AutoBot):
    def __init__(self, *, token_database: asqlite.Pool, subs: list[eventsub.SubscriptionPayload]) -> None:
        self.token_database = token_database

        super().__init__(
            client_id=CLIENT_ID,
            client_secret=CLIENT_SECRET,
            bot_id=BOT_ID,
            owner_id=OWNER_ID,
            prefix="!",
            subscriptions=subs,
            force_subscribe=True,
        )

    async def setup_hook(self) -> None:
        from twitchio.web import AiohttpAdapter

        class CustomAdapter(AiohttpAdapter):
            def _find_redirect(self, request):
                return "https://streak-bot.com/oauth/callback"

        adapter = CustomAdapter(port=4343, domain="streak-bot.com/oauth/callback")
        await self.set_adapter(adapter)
        await self.add_component(MyComponent(self))

    async def event_oauth_authorized(self, payload: twitchio.authentication.UserTokenPayload) -> None:
        await self.add_token(payload.access_token, payload.refresh_token)

        if not payload.user_id:
            return

        if payload.user_id == self.bot_id:
            # We usually don't want subscribe to events on the bots channel...
            return

        # A list of subscriptions we would like to make to the newly authorized channel...
        subs: list[eventsub.SubscriptionPayload] = [
            eventsub.ChatMessageSubscription(broadcaster_user_id=payload.user_id, user_id=self.bot_id),
            eventsub.ChatNotificationSubscription(broadcaster_user_id=payload.user_id, user_id=self.bot_id),
        ]

        resp: twitchio.MultiSubscribePayload = await self.multi_subscribe(subs)
        LOGGER.info("Subscription results for user %s: successes=%r errors=%r", payload.user_id, resp.subscriptions, resp.errors)
        if resp.errors:
            LOGGER.warning("Failed to subscribe to: %r, for user: %s", resp.errors, payload.user_id)

    async def add_token(self, token: str, refresh: str) -> twitchio.authentication.ValidateTokenPayload:
        # Make sure to call super() as it will add the tokens interally and return us some data...
        resp: twitchio.authentication.ValidateTokenPayload = await super().add_token(token, refresh)

        # Store our tokens in a simple SQLite Database when they are authorized...
        query = """
        INSERT INTO tokens (user_id, token, refresh)
        VALUES (?, ?, ?)
        ON CONFLICT(user_id)
        DO UPDATE SET
            token = excluded.token,
            refresh = excluded.refresh;
        """

        try:
            async with self.token_database.acquire() as connection:
                await connection.execute(query, (resp.user_id, token, refresh))
            LOGGER.info("Added token to the database for user: %s", resp.user_id)
        except Exception as e:
            LOGGER.error(f"Failed to save token for {resp.user_id}: {e}")

        return resp

    async def event_ready(self) -> None:
        LOGGER.info("Successfully logged in as: %s", self.bot_id)


class MyComponent(commands.Component):
    # An example of a Component with some simple commands and listeners
    # You can use Components within modules for a more organized codebase and hot-reloading.

    def __init__(self, bot: Bot) -> None:
        # Passing args is not required...
        # We pass bot here as an example...
        self.bot = bot
        

    # An example of listening to an event
    # We use a listener in our Component to display the messages received.
    #@commands.Component.listener()
    #async def event_message(self, payload: twitchio.ChatMessage) -> None:
        #LOGGER.info("event_message fired: broadcaster=%s chatter=%s text=%s", payload.broadcaster.name, payload.chatter.name, payload.text)
        #print(f"[{payload.broadcaster.name}] - {payload.chatter.name}: {payload.text}")

    @commands.command()
    async def streak(self, ctx: commands.Context, username: str = None) -> None:
        if username is None:
            # no username provided, look up the person who ran the command
            target = ctx.chatter.name
            try:
                async with self.bot.token_database.acquire() as connection:
                    row = await connection.fetchone("SELECT * FROM streaks WHERE username = ?", (target,))
                    if row is None:
                        await ctx.reply("No streak data for this user yet :o")
                    else:
                        await ctx.reply(f"Your streak is {row['streak_count']}")
            except Exception as e:
                LOGGER.error(f"Failed to find streak for {target}: {e}")
                await ctx.reply("Something went wrong looking up that streak :[")
        else:
            # username was provided, look up that person
            target = username
            try:
                async with self.bot.token_database.acquire() as connection:
                    row = await connection.fetchone("SELECT * FROM streaks WHERE username = ?", (target,))
                    if row is None:
                        await ctx.reply("No streak data for this user yet :o")
                    else:
                        await ctx.reply(f"{target}'s streak is {row['streak_count']}")
            except Exception as e:
                LOGGER.error(f"Failed to find streak for {target}: {e}")
                await ctx.reply("Something went wrong looking up that streak :[")

    @commands.command()
    async def streakleaderboard(self, ctx: commands.Context) -> None:
        try:
            async with self.bot.token_database.acquire() as connection:
                rows = await connection.fetchall(
                    """
                    SELECT username, streak_count
                    FROM streaks
                    ORDER BY streak_count DESC, username ASC
                    LIMIT 5
                    """
                )

            if not rows:
                await ctx.reply("No streak data yet.")
                return

            lines = [f"{i+1}) {row['username']}: {row['streak_count']}" for i, row in enumerate(rows)]
            await ctx.reply("Top 5 streaks: " + " | ".join(lines))

        except Exception as e:
            LOGGER.error(f"Failed to fetch streak leaderboard: {e}")
            await ctx.reply("Something went wrong getting the leaderboard.")

        

    @commands.Component.listener()
    async def event_chat_notification(self, payload) -> None:
        if payload.watch_streak:
            query = """
                INSERT INTO streaks (user_id, username, streak_count, last_streak_date)
                VALUES (?, ?, ?, ?)
                ON CONFLICT(user_id)
                DO UPDATE SET
                    username = excluded.username,
                    streak_count = excluded.streak_count,
                    last_streak_date = excluded.last_streak_date;
            """
            try:
                async with self.bot.token_database.acquire() as connection:
                    await connection.execute(query, (payload.chatter.id, payload.chatter.name, payload.watch_streak.streak, payload.timestamp.strftime("%Y-%m-%d")))
                print(f"{payload.chatter.name} is on a watch streak of {payload.watch_streak.streak}")
            except Exception as e:
                LOGGER.error(f"Failed to save streak for {payload.chatter.name}: {e}")


async def setup_database(db: asqlite.Pool) -> tuple[list[tuple[str, str]], list[eventsub.SubscriptionPayload]]:
    # Create our token table, if it doesn't exist..
    # You should add the created files to .gitignore or potentially store them somewhere safer
    # This is just for example purposes...

    query = """CREATE TABLE IF NOT EXISTS tokens(user_id TEXT PRIMARY KEY, token TEXT NOT NULL, refresh TEXT NOT NULL)"""
    async with db.acquire() as connection:
        await connection.execute(query)

        # Fetch any existing tokens...
        rows: list[sqlite3.Row] = await connection.fetchall("""SELECT * from tokens""")

        tokens: list[tuple[str, str]] = []
        subs: list[eventsub.SubscriptionPayload] = []

        for row in rows:
            tokens.append((row["token"], row["refresh"]))
            LOGGER.info("Loaded token for user_id=%s", row["user_id"])

            if row["user_id"] == BOT_ID:
                LOGGER.info("Skipping subscriptions for bot account %s", BOT_ID)
                continue

            LOGGER.info("Building subscriptions for broadcaster user_id=%s", row["user_id"])
            subs.extend([
                eventsub.ChatMessageSubscription(broadcaster_user_id=row["user_id"], user_id=BOT_ID),
                eventsub.ChatNotificationSubscription(broadcaster_user_id=row["user_id"], user_id=BOT_ID),
            ])

    query = """CREATE TABLE IF NOT EXISTS streaks(user_id TEXT PRIMARY KEY, username TEXT NOT NULL, streak_count INTEGER NOT NULL, last_streak_date TEXT NOT NULL)"""
    async with db.acquire() as connection:
        await connection.execute(query)

    return tokens, subs


# Our main entry point for our Bot
# Best to setup_logging here, before anything starts
def main() -> None:
    twitchio.utils.setup_logging(level=logging.INFO)

    async def runner() -> None:
        async with asqlite.create_pool("tokens.db") as tdb:
            tokens, subs = await setup_database(tdb)

            async with Bot(token_database=tdb, subs=subs) as bot:
                for pair in tokens:
                    await bot.add_token(*pair)

                await bot.start(load_tokens=False)

    try:
        asyncio.run(runner())
    except KeyboardInterrupt:
        LOGGER.warning("Shutting down due to KeyboardInterrupt")


if __name__ == "__main__":
    main()