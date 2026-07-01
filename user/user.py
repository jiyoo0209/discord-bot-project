'''
* 길드원 추가/삭제
* author LJY
* date   2026.06.30
'''
import discord
from discord import app_commands
from discord.ext import commands
from sheet.sheet import add_user, remove_user
from roleSetting.roleSetting import has_role_level, ROLE_IDS

class UserCog(commands.Cog):
    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name='가입', description='새로운 길드원 추가')
    @app_commands.describe(user_name='테런 닉네임', discord_user='대상 디스코드 유저')
    async def register_user(self, interaction: discord.Interaction, user_name: str, discord_user: discord.Member):
        # 처리 중 메시지 표시
        await interaction.response.defer(ephemeral=True)
        try:
            # 길마/서마만 사용 가능
            if not (has_role_level(interaction, 1) or has_role_level(interaction, 2)):
                await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
                return
            success, message = add_user(user_name)

            # 입장대기 삭제
            await discord_user.remove_roles(interaction.guild.get_role(ROLE_IDS[0]))
            # 일반 역할 부여
            await discord_user.add_roles(interaction.guild.get_role(ROLE_IDS[9]))
            # 메세지 출력
            await interaction.followup.send(message, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f'오류: {e}', ephemeral=True)

    @app_commands.command(name='탈퇴', description='길드원 탈퇴(삭제)')
    @app_commands.describe(user_name='테런 닉네임')
    async def delete_user(self, interaction: discord.Interaction, user_name: str):
        # 처리 중 메시지 표시
        await interaction.response.defer(ephemeral=True)
        try:
            # 길마/서마만 사용 가능
            if not (has_role_level(interaction, 1) or has_role_level(interaction, 2)):
                await interaction.response.send_message('길마/서마만 사용 가능합니다!', ephemeral=True)
                return
            success, message = remove_user(user_name)

            # 메세지 출력
            await interaction.followup.send(message, ephemeral=True)

        except Exception as e:
            await interaction.followup.send(f'오류: {e}', ephemeral=True)

async def setup(bot):
    await bot.add_cog(UserCog(bot))