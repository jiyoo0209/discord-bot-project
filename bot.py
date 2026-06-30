import discord
from discord.ext import commands
import os
import asyncio
from dotenv import load_dotenv

load_dotenv()

intents = discord.Intents.default()
intents.message_content = True
bot = commands.Bot(command_prefix='/', intents=intents)

@bot.event
async def on_ready():
    print(f' 봇 온라인: {bot.user}')
    await bot.tree.sync()
    print('명령어 동기화 완료')

@bot.tree.command(name='test', description='테스트 명령어')
async def test(interaction: discord.Interaction):
    await interaction.response.send_message('정상 작동!')

@bot.tree.command(name='관리자', description='길마,서마만 사용 가능')
async def admin_command(interaction: discord.Interaction):
    # 허용할 역할 목록
    allowed_roles = ['💜길마', '🩵서마']

    # 사용자의 역할 이름 목록
    user_roles = [role.name for role in interaction.user.roles]

    # 허용된 역할이 있는지 확인
    has_permission = any(role in user_roles for role in allowed_roles)

    if not has_permission:
        await interaction.response.send_message('길마, 서마만 사용 가능합니다!', ephemeral=True)
        return

    await interaction.response.send_message('관리자입니다!')

async def main():
    token = os.getenv('DISCORD_TOKEN')
    async with bot:
        await bot.load_extension('roleSetting.roleSetting')
        # 나중에 다른 파일도 이렇게 추가
        # await bot.load_extension('dashboard')

        # bot 실행
        await bot.start(token)

asyncio.run(main())