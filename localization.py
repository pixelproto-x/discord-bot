import discord
from discord import app_commands


class TraditionalChineseTranslator(app_commands.Translator):
    async def translate(
        self,
        string: app_commands.locale_str,
        locale: discord.Locale,
        context: app_commands.TranslationContext,
    ) -> str | None:
        if locale.value == "zh-TW":
            return string.extras.get("zh_TW")
        return None
