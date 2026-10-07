from __future__ import annotations

import asyncio
import logging
import shutil
import sys
import time
from logging.handlers import RotatingFileHandler

import discord
from discord import app_commands
from discord.ext import commands

from . import __version__, config
from .db import Database

log = logging.getLogger("renderbot")
EXTENSIONS = ("bot.cogs.general", "bot.cogs.account", "bot.cogs.files", "bot.cogs.render")

DM_ONLY_MESSAGE = "🔒 الأوامر دي بتشتغل في **الخاص** بس. كلّم البوت في الرسايل الخاصة (DM)."


class DMOnlyTree(app_commands.CommandTree):
    """Every command is registered as DM-only, and is re-checked at runtime as a second layer."""

    def __init__(self, client: discord.Client) -> None:
        super().__init__(
            client,
            allowed_contexts=app_commands.AppCommandContext(guild=False, dm_channel=True, private_channel=True),
            allowed_installs=app_commands.AppInstallationType(guild=True, user=True),
        )

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.guild_id is not None:
            await interaction.response.send_message(DM_ONLY_MESSAGE, ephemeral=True)
            return False
        return True


class RenderBot(commands.Bot):
    def __init__(self) -> None:
        super().__init__(command_prefix=commands.when_mentioned, intents=discord.Intents.default(),
                         tree_cls=DMOnlyTree, activity=discord.Game("/help"))
        self.db = Database(config.DB_PATH)

    async def setup_hook(self) -> None:
        # Leftovers from a crash (half-downloaded files) are never reusable: start clean.
        shutil.rmtree(config.STAGING_DIR, ignore_errors=True)
        config.STAGING_DIR.mkdir(parents=True, exist_ok=True)
        await self.db.fail_preparing()
        for ext in EXTENSIONS:
            await self.load_extension(ext)
        self._lag_task = asyncio.create_task(self._lag_monitor())
        self.tree.on_error = self.on_tree_error
        if config.DEV_GUILD_ID:
            # One-time cleanup of server-only test commands left by the first version of the bot.
            guild = discord.Object(id=config.DEV_GUILD_ID)
            self.tree.clear_commands(guild=guild)
            await self.tree.sync(guild=guild)
            log.info("Removed old server-only commands from guild %s. You can delete DEV_GUILD_ID now.",
                     config.DEV_GUILD_ID)
        synced = await self.tree.sync()  # global sync: DM commands cannot be registered per-server
        log.info("Registered %d global commands on this Discord application: %s",
                 len(synced), ", ".join(sorted(c.name for c in synced)))

    async def on_tree_error(self, interaction: discord.Interaction, error: app_commands.AppCommandError) -> None:
        if isinstance(error, app_commands.CommandOnCooldown):
            msg = f"⏳ استنى {int(error.retry_after) + 1} ثانية وجرّب تاني."
        elif isinstance(error, app_commands.CheckFailure):
            return  # the check already told the user (e.g. "DM only"); a second message would be noise
        elif isinstance(getattr(error, "original", None), discord.NotFound) and error.original.code == 10062:
            # Discord's 3-second window had already closed when we tried to answer. Nothing can be sent back.
            log.warning("Reply came too late for /%s (Discord 'Unknown interaction'). See the 'age=' line above.",
                        getattr(getattr(error, "command", None), "name", "?"))
            return
        else:
            log.error("Command failed: %s", type(error).__name__, exc_info=error)
            msg = "❌ حصل خطأ غير متوقع. جرّب تاني بعد شوية."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(msg, ephemeral=True)
            else:
                await interaction.response.send_message(msg, ephemeral=True)
        except discord.HTTPException:
            pass

    async def on_interaction(self, interaction: discord.Interaction) -> None:
        # Diagnostic only: shows whether Discord delivers the user's command to THIS process.
        # Never log option values: login modals carry the user's Kaggle token.
        name = interaction.command.name if interaction.command else interaction.type.name
        age = (discord.utils.utcnow() - interaction.created_at).total_seconds()
        lat = self.latency
        log.info("interaction received: %s (%s) age=%.2fs gateway_latency=%s", name,
                 "DM" if interaction.guild_id is None else "server", age,
                 "n/a" if lat != lat else f"{lat * 1000:.0f}ms")  # NaN until the gateway is connected
        if age > 2.0:
            log.warning("This command reached the bot %.1fs after Discord sent it (limit to answer: 3s). "
                        "Causes: this PC's clock is off, a slow/unstable connection or VPN, or the PC was busy/asleep.", age)

    async def _lag_monitor(self) -> None:
        """Warns when the event loop is frozen (antivirus, heavy CPU, a blocking call, PC sleep)."""
        while True:
            start = time.perf_counter()
            await asyncio.sleep(1)
            lag = time.perf_counter() - start - 1
            if lag > 1.0:
                log.warning("The bot was frozen for %.1fs (CPU busy / antivirus / PC went to sleep). "
                            "Commands sent during that time fail with 'application did not respond'.", lag)

    async def on_ready(self) -> None:
        log.info("RenderBot v%s logged in as %s", __version__, self.user)


def main() -> None:
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    console = logging.StreamHandler()
    console.setFormatter(fmt)
    logfile = RotatingFileHandler(config.DATA_DIR / "bot.log", maxBytes=2_000_000, backupCount=3, encoding="utf-8")
    logfile.setFormatter(fmt)
    root.addHandler(console)
    root.addHandler(logfile)
    try:
        RenderBot().run(config.DISCORD_TOKEN, log_handler=None)
    except discord.LoginFailure:
        print("DISCORD_TOKEN is invalid. Reset it in the Developer Portal and fix .env.")
        sys.exit(78)  # run.sh / run.bat do not restart on this code


if __name__ == "__main__":
    main()
