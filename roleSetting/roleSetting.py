'''
* 등급 인원수 관리 (증가/감소/설정)
* author HDG
* date   2026.06.30
'''
import discord
from discord import app_commands
from discord.ext import commands
from sheet.sheet import update_rank_cnt, VALID_RANKS

# 등급 선택지 (VALID_RANKS에서 자동 생성 -> 등급 추가하면 세 명령어 모두 반영)
RANK_CHOICES = [app_commands.Choice(name=r, value=r) for r in VALID_RANKS]

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

    @app_commands.command(name='증가', description='등급 인원수를 증가')
    @app_commands.describe(등급='대상 등급', 숫자='증가시킬 인원 수 (예: 3)')
    @app_commands.choices(등급=RANK_CHOICES)
    async def increase(self, interaction: discord.Interaction, 등급: app_commands.Choice[str], 숫자: int):
        if not has_role_level(interaction, 1):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        if 숫자 < 1:
            await interaction.response.send_message('1 이상의 숫자를 입력하세요!', ephemeral=True)
            return

        success, message = update_rank_cnt(등급.value, 숫자, 'delta')
        await interaction.response.send_message(message, ephemeral=not success)

    @app_commands.command(name='감소', description='등급 인원수를 감소')
    @app_commands.describe(등급='대상 등급', 숫자='감소시킬 인원 수 (예: 3)')
    @app_commands.choices(등급=RANK_CHOICES)
    async def decrease(self, interaction: discord.Interaction, 등급: app_commands.Choice[str], 숫자: int):
        if not has_role_level(interaction, 1):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        if 숫자 < 1:
            await interaction.response.send_message('1 이상의 숫자를 입력하세요!', ephemeral=True)
            return

        success, message = update_rank_cnt(등급.value, -숫자, 'delta')
        await interaction.response.send_message(message, ephemeral=not success)

    '''
    2026.07.01 : 주석처리
    등급 설정은 증감으로 사용 - 필요하면 추후 개발 예정

    @app_commands.command(name='설정', description='등급 인원수를 설정')
    @app_commands.describe(등급='대상 등급', 숫자='설정할 인원 수 (예: 5)')
    @app_commands.choices(등급=RANK_CHOICES)
    async def set_cnt(self, interaction: discord.Interaction, 등급: app_commands.Choice[str], 숫자: int):
        if not has_role_level(interaction, 1):
            await interaction.response.send_message('길드마스터만 사용 가능합니다!', ephemeral=True)
            return
        if 숫자 < 0:
            await interaction.response.send_message('0 이상의 숫자를 입력하세요!', ephemeral=True)
            return

        success, message = update_rank_cnt(등급.value, 숫자, 'set')
        await interaction.response.send_message(message, ephemeral=not success)
    '''

async def setup(bot):
    await bot.add_cog(RoleSetting(bot))