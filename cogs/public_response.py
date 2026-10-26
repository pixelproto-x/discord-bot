from __future__ import annotations

import discord


class PublicizeView(discord.ui.View):
    """Attach to a private slash-command response to let its owner publish a copy."""

    def __init__(self, owner_id: int, content: str | None = None, embed: discord.Embed | None = None):
        super().__init__(timeout=900)
        self.owner_id = int(owner_id)
        self.content = content or None
        self.embed = embed.copy() if embed is not None else None

    @discord.ui.button(label="公開這則訊息", style=discord.ButtonStyle.primary, emoji="📢")
    async def publish(self, interaction: discord.Interaction, button: discord.ui.Button):
        if interaction.user.id != self.owner_id:
            await interaction.response.send_message("只有執行這個指令的人可以公開這則訊息。", ephemeral=True)
            return
        channel = interaction.channel
        if channel is None or not hasattr(channel, "send"):
            await interaction.response.send_message("找不到原本的文字頻道，無法公開。", ephemeral=True)
            return
        try:
            await channel.send(
                content=self.content,
                embed=self.embed.copy() if self.embed is not None else None,
                allowed_mentions=discord.AllowedMentions.none(),
            )
        except (discord.Forbidden, discord.HTTPException):
            await interaction.response.send_message(
                "公開失敗，請確認機器人在原本頻道有傳送訊息及嵌入連結的權限。",
                ephemeral=True,
            )
            return
        await interaction.response.send_message("已公開到原本的頻道；原本的私密回覆會保留。", ephemeral=True)
        button.disabled = True
        try:
            await interaction.message.edit(view=self)
        except (discord.NotFound, discord.Forbidden, discord.HTTPException):
            pass
