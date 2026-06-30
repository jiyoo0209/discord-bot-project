'''
* 길드원 추가/삭제
* author LJY
* date   2026.06.30
'''
import discord
from discord.ext import commands
from sheet.sheet import add_user

def setup_user_commands(bot):
    @bot.tree.command(name='가입', description='새로운 길드원 추가')
    async def register_user(interaction: discord.Interaction, user_name: str):
        try:
            success, message = add_user(user_name)

            await interaction.response.send_message(message, ephemeral=True)

        except Exception as e:
            await interaction.response.send_message(f'오류: {e}', ephemeral=True)