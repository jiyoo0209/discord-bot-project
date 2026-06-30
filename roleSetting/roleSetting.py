'''
* 명예/우수 설정
* author HDG
* date   2026.06.30
'''
import discord
from discord import app_commands
from discord.ext import commands

from sheet.sheet import update_rank_cnt

ROLE_IDS = {
    1: 1414413421566234755,  # 길드마스터
    2: 1414416424826572951,  # 서브마스터
    3: 1414417235132284980,  # 명예
    4: 1414416934858133566,  # 우수
    9: 1414416713134641254,  # 일반
}

def has_role_level(interaction: discord.Interaction, level: int) -> bool:
    """명령어를 친 사람이 해당 등급의 역할을 가지고 있는지 확인"""
    target_id = ROLE_IDS[level]
    user_role_ids = [role.id for role in interaction.user.roles]
    return target_id in user_role_ids

class RoleSetting(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='명예설정', description='명예길드원 인원수를 설정')
    @app_commands.describe(숫자='설정할 명예길드원 수 (예: 3)')
    async def honor_setting(self, interaction: discord.Interaction, 숫자: int):
        # 길드마스터(등급 1)만 사용 가능
        if not has_role_level(interaction, 1):
            await interaction.response.send_message(
                '길드마스터만 사용 가능합니다!', ephemeral=True
            )
            return

        update_rank_cnt('명예', 숫자);
        await interaction.response.send_message(
            f'명예길드원이 {숫자}명으로 설정되었습니다!'
        )

    @app_commands.command(name='우수설정', description='우수길드원 인원수를 설정')
    @app_commands.describe(숫자='설정할 우수길드원 수 (예: 3)')
    async def excellent_setting(self, interaction: discord.Interaction, 숫자: int):
        # 길드마스터(등급 1)만 사용 가능
        if not has_role_level(interaction, 1):
            await interaction.response.send_message(
                '길드마스터만 사용 가능합니다!', ephemeral=True
            )
            return
        
        update_rank_cnt('우수', 숫자);
        await interaction.response.send_message(
            f'우수길드원이 {숫자}명으로 설정되었습니다!'
        )

async def setup(bot):
    await bot.add_cog(RoleSetting(bot))