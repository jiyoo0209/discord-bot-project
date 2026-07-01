'''
* 점수 체크 (/체크)
* author HDG
* date   2026.07.01
'''
import discord
from discord import app_commands
from discord.ext import commands
from sheet.sheet import add_points
from roleSetting.roleSetting import has_role_level
from typing import Optional


class PointCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='체크', description='길드원들에게 점수 기록 (날짜 지정 가능)')
    @app_commands.describe(
        점수='기록할 점수 (예: 5)',
        길드원='공백으로 구분한 닉네임 목록 (예: 카카오톡 주땅)',
        날짜='YYYYMMDD 형식 (예: 20260704), 미입력 시 오늘',
    )
    async def check(self, interaction: discord.Interaction, 점수: int, 길드원: str, 날짜: Optional[str] = None):
        if not (has_role_level(interaction, 1) or has_role_level(interaction, 2)):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return

        names = 길드원.split()
        await interaction.response.defer(ephemeral=True)
        success, message = add_points(names, 점수, date=날짜)
        await interaction.followup.send(message, ephemeral=True)

    @app_commands.command(name='점령', description='점령 체크 (토요일에만 사용 가능)')
    @app_commands.describe(
        길드원='공백으로 구분한 닉네임 목록 (예: 카카오톡 주땅)',
    )
    async def battle(self, interaction: discord.Interaction, 길드원: str):
        if not (has_role_level(interaction, 1) or has_role_level(interaction, 2)):
            await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
            return

        names = 길드원.split()
        await interaction.response.defer(ephemeral=True)
        success, message = add_points(names, 8, 'Y')
        await interaction.followup.send(message, ephemeral=True)


async def setup(bot):
    await bot.add_cog(PointCog(bot))
