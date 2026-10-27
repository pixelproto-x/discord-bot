import discord
from discord import app_commands
from discord.ext import commands

from cogs.social import is_safe_self_assignable_role


GAME_ROLE_NAMES = (
    "⛏ ｜麥塊玩家-Minecraft",
    "✨｜旅行者-Genshin",
    "🔧｜終末地幹員-Endfield",
    "🌁｜鑑定師-NTE",
    "🎵｜世界居民-Pjsekai",
    "🎮｜遊戲成員-Gamer",
)


def is_safe_game_role(role, bot_member) -> bool:
    """Prevent the public game-role menu from granting privileged or unmanageable roles."""
    return bool(
        role
        and bot_member
        and bot_member.guild_permissions.manage_roles
        and not role.managed
        and is_safe_self_assignable_role(role)
        and role < bot_member.top_role
    )


class GameRoleSelect(discord.ui.Select):
    def __init__(self):
        super().__init__(
            placeholder="選擇你想領取的遊戲身分組",
            min_values=0,
            max_values=6,
            options=[
                discord.SelectOption(label=role_name, value=role_name)
                for role_name in GAME_ROLE_NAMES
            ],
            custom_id="persistent_game_role_menu",
        )

    async def callback(self, interaction: discord.Interaction):
        if interaction.guild is None or not isinstance(interaction.user, discord.Member):
            await interaction.response.send_message("請在伺服器內使用這個選單。", ephemeral=True)
            return

        guild = interaction.guild
        member = interaction.user
        bot_member = guild.me
        if bot_member is None or not bot_member.guild_permissions.manage_roles:
            await interaction.response.send_message("機器人缺少管理身分組權限。", ephemeral=True)
            return

        selected_names = set(self.values)
        missing_names = [
            role_name for role_name in GAME_ROLE_NAMES
            if discord.utils.get(guild.roles, name=role_name) is None
        ]
        roles = {
            role_name: discord.utils.get(guild.roles, name=role_name)
            for role_name in GAME_ROLE_NAMES
        }
        add_roles = []
        remove_roles = []
        for role_name in GAME_ROLE_NAMES:
            role = roles[role_name]
            if role is None:
                continue
            if not is_safe_game_role(role, bot_member):
                await interaction.response.send_message(
                    f"身分組「{role.name}」具有不允許自助領取的權限，或超出機器人可管理的階層，已拒絕變更。",
                    ephemeral=True,
                )
                return
            if role_name in selected_names and role not in member.roles:
                add_roles.append(role)
            elif role_name not in selected_names and role in member.roles:
                remove_roles.append(role)

        try:
            if add_roles:
                await member.add_roles(*add_roles, reason="成員透過遊戲身分組選單自行領取")
            if remove_roles:
                await member.remove_roles(*remove_roles, reason="成員透過遊戲身分組選單取消選取")
        except discord.Forbidden:
            await interaction.response.send_message("身分組更新失敗，請確認機器人的管理身分組權限與身分組階層。", ephemeral=True)
            return
        except discord.HTTPException:
            await interaction.response.send_message("Discord 暫時無法更新身分組，請稍後再試。", ephemeral=True)
            return

        added = [role.name for role in add_roles]
        removed = [role.name for role in remove_roles]
        lines = []
        if added:
            lines.append("已領取：" + "、".join(added))
        if removed:
            lines.append("已移除：" + "、".join(removed))
        if not lines:
            lines.append("身分組已是最新狀態，沒有需要變更的項目。")
        if missing_names:
            lines.append("伺服器找不到以下身分組：" + "、".join(missing_names))
        await interaction.response.send_message("\n".join(lines), ephemeral=True)


class GameRoleView(discord.ui.View):
    def __init__(self):
        super().__init__(timeout=None)
        self.add_item(GameRoleSelect())


class RoleSelectCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    @app_commands.command(name="roles", description="發布遊戲身分組自選面板")
    @app_commands.guild_only()
    async def roles(self, interaction: discord.Interaction):
        embed = discord.Embed(
            title="🎮 遊戲身分組自選",
            description=(
                "在下方選單勾選你想領取的遊戲身分組。\n"
                "取消勾選後再次送出，即可移除對應身分組。\n"
                "最多可同時選擇 6 個身分組。"
            ),
            color=discord.Color.blurple(),
        )
        await interaction.response.send_message(embed=embed, view=GameRoleView())


async def setup(bot: commands.Bot):
    bot.add_view(GameRoleView())
    await bot.add_cog(RoleSelectCog(bot))
