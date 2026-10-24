import unittest
from unittest.mock import AsyncMock, patch

import discord
from discord.ext import commands

import bot as bot_module


class CogRegistrationTests(unittest.IsolatedAsyncioTestCase):
    async def test_all_project_cogs_register_without_command_collisions(self):
        client = commands.Bot(
            command_prefix="!",
            intents=discord.Intents.none(),
            help_command=None,
        )
        modules = (
            bot_module.utility,
            bot_module.social,
            bot_module.games,
            bot_module.music,
            bot_module.moderation,
            bot_module.profile,
            bot_module.voice,
            bot_module.giveaway,
            bot_module.media,
            bot_module.settings,
            bot_module.banter,
            bot_module.speech_control,
            bot_module.help_cog,
            bot_module.web_tools,
            bot_module.hacks,
            bot_module.history,
            bot_module.browser_mic,
            bot_module.marriage,
            bot_module.economy,
        )
        try:
            # Cog registration must not depend on public Lavalink servers being online.
            with patch.object(
                bot_module.music.MusicCog,
                "setup_nodes",
                new_callable=AsyncMock,
            ):
                for module in modules:
                    await module.setup(client)

            commands_registered = client.tree.get_commands()
            names = [command.name for command in commands_registered]
            self.assertTrue(names, "Expected the project to register slash commands.")
            self.assertEqual(len(names), len(set(names)), "Top-level slash command names must be unique.")
        finally:
            await client.close()


if __name__ == "__main__":
    unittest.main()
