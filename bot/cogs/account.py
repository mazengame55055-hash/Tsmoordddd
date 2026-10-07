from __future__ import annotations

import discord
from discord import app_commands
from discord.ext import commands

from .. import crypto, kaggle_client
from ..accounts import load_credentials
from ..kaggle_client import AuthError, Credentials, KaggleError
from ..utils import cooldown, report_ui_error


class LoginModal(discord.ui.Modal, title="تسجيل الدخول لـ Kaggle"):
    secret = discord.ui.TextInput(label="Kaggle API Token", placeholder="KGAT_...",
                                  min_length=10, max_length=300)
    username = discord.ui.TextInput(label="Username (للمفتاح القديم kaggle.json بس)", required=False,
                                    min_length=2, max_length=60)

    def __init__(self, bot: commands.Bot):
        super().__init__()
        self.bot = bot

    async def on_error(self, interaction: discord.Interaction, error: Exception) -> None:
        await report_ui_error(interaction, error, "login modal")

    async def on_submit(self, interaction: discord.Interaction) -> None:
        await interaction.response.defer(ephemeral=True, thinking=True)
        typed_name = self.username.value.strip().lstrip("@")
        secret = self.secret.value.strip()
        kind = "token" if secret.startswith("KGAT_") else "legacy"
        if kind == "legacy" and not typed_name:
            await interaction.followup.send(
                "المفتاح ده من النوع القديم (kaggle.json)، فلازم تكتب الـ Username كمان.", ephemeral=True)
            return
        creds = Credentials(typed_name, secret, kind)
        try:
            username = await kaggle_client.verify(creds)
        except AuthError:
            await interaction.followup.send(
                "❌ Kaggle رفض البيانات دي. اتأكد من التوكين وجرّب تاني.", ephemeral=True)
            return
        except KaggleError as e:
            await interaction.followup.send(f"⚠️ معرفتش أتواصل مع Kaggle دلوقتي:\n```{e}```", ephemeral=True)
            return
        await self.bot.db.save_user(interaction.user.id, username, crypto.encrypt(secret), kind)
        await interaction.followup.send(
            f"✅ تم تسجيل الدخول كـ **{username}**.\n"
            "التوكين متخزّن **مشفّر** عند صاحب البوت. تقدر تمسحه بـ `/logout`، "
            "وتقدر تلغيه في أي وقت من إعدادات Kaggle.\n"
            "الخطوة الجاية: `/upload` لرفع ملف الـ Blend.",
            ephemeral=True)


class Account(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="login", description="سجّل توكين Kaggle بتاعك")
    @cooldown(15)
    async def login(self, interaction: discord.Interaction) -> None:
        await interaction.response.send_modal(LoginModal(self.bot))

    @app_commands.command(name="logout", description="امسح التوكين بتاعك من البوت")
    @cooldown(5)
    async def logout(self, interaction: discord.Interaction) -> None:
        await self.bot.db.delete_user(interaction.user.id)
        await interaction.response.send_message(
            "🗑️ اتمسح التوكين وسجل الملفات من البوت.\n"
            "لو عايز تلغي التوكين نفسه، امسحه من إعدادات Kaggle.", ephemeral=True)

    @app_commands.command(name="whoami", description="اعرف أنت مسجّل بأنهي حساب")
    @cooldown(5)
    async def whoami(self, interaction: discord.Interaction) -> None:
        creds = await load_credentials(self.bot.db, interaction.user.id)
        if not creds:
            await interaction.response.send_message("مش مسجّل. استخدم `/login`.", ephemeral=True)
            return
        files = await self.bot.db.list_files(interaction.user.id, limit=1000)
        await interaction.response.send_message(
            f"👤 **{creds.username}**\n📂 عدد الملفات المرفوعة: **{len(files)}**", ephemeral=True)


async def setup(bot: commands.Bot) -> None:
    await bot.add_cog(Account(bot))
